"""Prepare this checkout's environment without starting PSCAD or editing another installation."""
import argparse
from copy import deepcopy
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

import yaml

from .config import DEFAULT, load_config
from . import helpdocs

AUTOMATION_URL = 'https://www.pscad.com/uploads/public/files/AutomationLibrary_463_124.zip'
WHEEL_NAME = 'mhrc_automation-1.2.4-py3-none-any.whl'


def _registered_homes():
    import winreg
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                parent = winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                                        0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                        try:
                            name = winreg.QueryValueEx(key, 'DisplayName')[0]
                            location = winreg.QueryValueEx(key, 'InstallLocation')[0]
                        except FileNotFoundError:
                            continue
                        if 'pscad' in name.lower() and location:
                            yield Path(location)


def _version(exe):
    import win32api
    info = win32api.GetFileVersionInfo(str(exe), '\\')
    return (info['FileVersionMS'] >> 16, info['FileVersionMS'] & 65535,
            info['FileVersionLS'] >> 16)


def find_pscad(home=None):
    if home:
        candidates = [Path(home)]
    else:
        candidates = list(_registered_homes())
        for variable in ('ProgramFiles', 'ProgramFiles(x86)'):
            base = Path(os.environ.get(variable, 'C:/Program Files'))
            for folder in base.glob('PSCAD*'):
                candidates.append(folder)
                candidates.extend(p for p in folder.iterdir() if p.is_dir())
        # Check one conventional install location per local drive, without scanning user files.
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        candidates.extend(Path('%s:/PSCAD' % chr(65 + i)) for i in range(26) if mask & (1 << i))
    found = []
    for candidate in dict.fromkeys(p.resolve() for p in candidates):
        exe = candidate/'bin'/'win64'/'Pscad.exe'
        if exe.is_file() and _version(exe) == (4, 6, 2):
            found.append((candidate, exe))
    if len(found) != 1:
        raise RuntimeError('Expected one PSCAD 4.6.2 x64 installation; found %s. Use -PscadHome with its directory.'
                           % [str(p) for p, _ in found])
    return found[0]


def automation_wheel(install, cache, supplied=None):
    if supplied:
        wheel = Path(supplied).resolve()
        if not wheel.is_file() or wheel.name != WHEEL_NAME:
            raise ValueError('Supply the official %s wheel' % WHEEL_NAME)
        return wheel
    roots = [install/'AutomationLibrary', install.parent/'AutomationLibrary']
    roots.extend(Path(os.environ[v])/'PSCAD'/'AutomationLibrary'
                 for v in ('ProgramFiles', 'ProgramFiles(x86)') if v in os.environ)
    for root in roots:
        if root.is_dir():
            wheel = next(root.rglob(WHEEL_NAME), None)
            if wheel:
                return wheel
    cache.mkdir(parents=True, exist_ok=True)
    wheel = cache/WHEEL_NAME
    if not wheel.exists():
        archive = cache/'AutomationLibrary_463_124.zip'
        if not archive.exists():
            print('Downloading the official X4 Automation Library package...', flush=True)
            with urllib.request.urlopen(AUTOMATION_URL, timeout=60) as response, archive.open('wb') as output:
                shutil.copyfileobj(response, output)
        with zipfile.ZipFile(archive) as package:
            members = [name for name in package.namelist() if name.replace('\\', '/').split('/')[-1] == WHEEL_NAME]
            if len(members) != 1:
                raise RuntimeError('Official package does not contain one %s; supply -AutomationWheel' % WHEEL_NAME)
            # Copy just the wheel; never extract archive paths or execute vendor installers.
            with package.open(members[0]) as source, wheel.open('wb') as output:
                shutil.copyfileobj(source, output)
    return wheel


def configure(repo, pscad_home=None, wheel=None):
    if os.name != 'nt':
        raise OSError('The quick setup is for Windows and PSCAD 4.6.2')
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('Run setup.ps1 to use a dedicated virtual environment')
    repo = Path(repo).resolve()
    config = repo/'config.yaml'
    if config.exists():
        cfg = load_config(config)
        install, exe = find_pscad(pscad_home or cfg['pscad']['install'] or None)
        if str(exe) != cfg['pscad']['exe']:
            raise ValueError('Existing config.yaml points to a different executable; update it explicitly')
    else:
        install, exe = find_pscad(pscad_home)
        if not str(repo/'work').isascii():
            raise ValueError('Place this checkout in an ASCII path before running setup')
        cfg = deepcopy(DEFAULT)
        cfg['pscad'].update(install=str(install), exe=str(exe))
        config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding='utf-8')
        cfg = load_config(config)
    home = Path(cfg['paths']['home'])
    home.mkdir(parents=True, exist_ok=True)
    try:
        installed = metadata.version('mhrc-automation')
    except metadata.PackageNotFoundError:
        installed = None
    if installed != '1.2.4':
        source = automation_wheel(install, home/'downloads', wheel)
        subprocess.run([sys.executable, '-m', 'pip', 'install', str(source)], check=True)
    # Pass the resolved source so inherited configuration cannot select another installation.
    help_result = helpdocs.prepare(install/'help'/'ol-help.chm')
    snippet = repo/'mcp.local.json'
    if not snippet.exists():
        snippet.write_text(json.dumps({'mcpServers': {'pscad': {
            'command': sys.executable, 'args': ['-m', 'pscad_mcp.server_sdk'],
            'env': {'PSCAD_AI_CONFIG': str(config)},
        }}}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'config': str(config), 'help': help_result, 'mcp_config': str(snippet),
            'note': 'Prepared local files only. PSCAD, its license and compiler were not launched or verified.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--pscad-home')
    parser.add_argument('--automation-wheel')
    args = parser.parse_args()
    print(json.dumps(configure(args.repo, args.pscad_home, args.automation_wheel), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
