"""Prepare and query locally installed PSCAD Help; never redistribute it."""
from html.parser import HTMLParser
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

from .config import load_config, path_of


def _html_files(directory):
    return sorted(p for p in directory.rglob('*')
                  if p.is_file() and p.suffix.lower() in {'.htm', '.html'})


def prepare(source=None):
    """Extract the user's CHM or copy an HTML tree into the private work directory."""
    directory = Path(path_of('home')) / '_help'
    if directory.exists():
        files = _html_files(directory)
        if files:
            return {'directory': str(directory), 'pages': len(files), 'reused': True}
        raise ValueError('Help directory exists but contains no HTML; remove or rename it before preparing again')
    if source is None:
        install = load_config()['pscad']['install']
        if not install:
            raise ValueError('Run setup.ps1 or configure the PSCAD installation first')
        source = Path(install) / 'help' / 'ol-help.chm'
    source = Path(source).resolve()
    if source.is_dir():
        if not _html_files(source):
            raise ValueError('Source directory contains no Help HTML')
        shutil.copytree(source, directory)
    else:
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.suffix.lower() != '.chm':
            raise ValueError('Supply the installed Help CHM or an extracted HTML directory')
        if os.name != 'nt':
            raise OSError('CHM extraction uses Windows hh.exe; use an extracted HTML directory on other systems')
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        with tempfile.TemporaryDirectory(prefix='help-', dir=directory.parent) as temp:
            subprocess.run([str(Path(os.environ['WINDIR'])/'hh.exe'), '-decompile', temp, str(source)],
                           startupinfo=startup, check=True, timeout=120)
            if not _html_files(Path(temp)):
                raise RuntimeError('hh.exe returned without extracting Help HTML from %s' % source)
            shutil.copytree(temp, directory)
    return {'directory': str(directory), 'pages': len(_html_files(directory)), 'reused': False}


def _directory():
    directory = Path(path_of('home')) / '_help'
    if not directory.is_dir():
        prepare()
    return directory.resolve()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored -= 1

    def handle_data(self, text):
        if not self.ignored and text.strip():
            self.parts.append(text.strip())


def read(name, start=0, limit=10000):
    directory = _directory()
    file = (directory / name).resolve()
    if not file.is_relative_to(directory) or file.suffix.lower() not in (".htm", ".html"):
        raise ValueError("Supply a relative Help HTML path returned by search")
    parser = _Text()
    parser.feed(file.read_text(encoding="utf-8", errors="replace"))
    text = "\n".join(parser.parts)
    return {"source": str(file), "start": start, "length": len(text),
            "text": text[start:start + limit]}


def search(query, limit=12):
    directory = _directory()
    words = re.findall(r"\w+", query.lower())
    if not words:
        raise ValueError("Search query is empty")
    rows = []
    for path in _html_files(directory):
        relative = path.relative_to(directory).as_posix()
        title = path.stem.replace("_", " ")
        name_score = sum(5 for w in words if w in title.lower())
        text = read(relative, limit=100000)["text"]
        score = name_score + sum(min(text.lower().count(w), 4) for w in words)
        if all(w in (title + " " + text).lower() for w in words):
            at = text.lower().find(words[0])
            rows.append({"file": relative, "title": title, "score": score,
                         "excerpt": text[max(0, at - 70):at + 500]})
    return sorted(rows, key=lambda r: (-r["score"], r["file"]))[:limit]


def main():
    import argparse
    import json
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("prepare"); q.add_argument("--source")
    q = sub.add_parser("search"); q.add_argument("query"); q.add_argument("--limit", type=int, default=12)
    q = sub.add_parser("read"); q.add_argument("name"); q.add_argument("--start", type=int, default=0); q.add_argument("--limit", type=int, default=10000)
    a = p.parse_args()
    if a.command == 'prepare':
        result = prepare(a.source)
    elif a.command == 'search':
        result = search(a.query, a.limit)
    else:
        result = read(a.name, a.start, a.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
