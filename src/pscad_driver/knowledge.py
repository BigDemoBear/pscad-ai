"""Query local Help, installed automation APIs, and actual project/library definitions."""
import ast
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET

from . import helpdocs
from .author import active_ports
from .config import load_config


def sources(case_id=None, libraries=None):
    cfg = load_config()
    paths = [Path(cfg["pscad"]["install"]) / "master.pslx"]
    if case_id:
        from .case import pscx_of
        import json
        directory, model = pscx_of(case_id)
        paths.append(Path(model))
        plan = Path(directory) / "plan.json"
        if plan.exists():
            paths.extend(Path(p) for p in json.loads(plan.read_text(encoding="utf-8")).get("libraries", []))
    paths.extend(Path(p) for p in (libraries or []))
    return [(p, ET.parse(p).getroot()) for p in dict.fromkeys(paths)]


def component(name, case_id=None, libraries=None, include_script=False, params=None):
    namespace, short = name.split(":", 1) if ":" in name else (None, name)
    candidates = []
    for path, root in sources(case_id, libraries):
        scope = root.get("name")
        if namespace == "self" and path.suffix != ".pscx":
            continue
        if namespace and namespace != "self" and namespace != scope:
            continue
        d = root.find("definitions/Definition[@name='%s']" % short)
        if d is not None:
            candidates.append((path, root, d))
    if not candidates:
        raise KeyError("Definition not found: %s" % name)
    if len(candidates) != 1:
        raise ValueError("Ambiguous name; qualify the namespace: %s" % [r.get("name") + ":" + short for _, r, _ in candidates])
    path, root, d = candidates[0]
    parameters = [{**p.attrib, "default": p.findtext("value", ""),
                   "choices": [c.text for c in p.findall("choice")],
                   "condition": p.findtext("cond", "true"), "help": p.findtext("help", "")}
                  for p in d.findall("form//parameter")]
    desc = d.find("paramlist/param[@name='Description']")
    result = {"definition": root.get("name") + ":" + short, "source": str(path),
              "description": desc.get("value", "") if desc is not None else "",
              "is_page": d.find("schematic") is not None,
              "parameters": parameters,
              "ports": [{**p.attrib, "condition": p.text or "true"} for p in d.findall("svg/port")]}
    if params is not None:
        values = {p["name"]: p["default"] for p in parameters}
        values.update(params)
        result["active_ports"] = active_ports(d, values)
    if include_script:
        result["script"] = {s.get("name"): s.text or "" for s in d.findall("script/segment")}
    return result


@lru_cache(maxsize=1)
def _api_index():
    # Locate installed files without importing/starting the automation controller.
    import importlib.util
    package = Path(importlib.util.find_spec("mhrc.automation").origin).parent
    result = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            members = node.body if isinstance(node, ast.ClassDef) else [node]
            for method in members:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.name.startswith("_"):
                    name = (node.name + "." if isinstance(node, ast.ClassDef) else "") + method.name
                    result.append({"module": path.stem, "name": name,
                                   "signature": "(%s)" % ast.unparse(method.args),
                                   "documentation": ast.get_docstring(method) or "",
                                   "source": str(path), "line": method.lineno})
    return result


def api(query, limit=10):
    words = query.lower().split()
    rows = []
    for item in _api_index():
        text = (item["module"] + " " + item["name"] + " " + item["documentation"]).lower()
        if all(word in text for word in words):
            score = sum(5 if word in item["name"].lower() else 1 for word in words)
            rows.append((score, item))
    return [item for _, item in sorted(rows, key=lambda r: (-r[0], r[1]["name"]))[:limit]]


def search(query, case_id=None, libraries=None, limit=8):
    words = query.lower().split()
    hits = []
    for path, root in sources(case_id, libraries):
        for d in root.findall("definitions/Definition"):
            desc = d.find("paramlist/param[@name='Description']")
            description = desc.get("value", "") if desc is not None else ""
            text = (d.get("name", "") + " " + description).lower()
            if all(word in text for word in words):
                hits.append({"definition": root.get("name") + ":" + d.get("name"),
                             "description": description, "source": str(path)})
    return {"definitions": hits[:limit], "automation_api": api(query, limit),
            "help": helpdocs.search(query, limit)}


def main():
    import argparse
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("kind", choices=["search", "component", "api", "help"])
    p.add_argument("query")
    p.add_argument("--case")
    p.add_argument("--libraries", nargs="*")
    p.add_argument("--script", action="store_true")
    p.add_argument("--limit", type=int, default=8)
    a = p.parse_args()
    if a.kind == "component":
        result = component(a.query, a.case, a.libraries, a.script)
    elif a.kind == "api":
        result = api(a.query, a.limit)
    elif a.kind == "help":
        result = helpdocs.read(a.query)
    else:
        result = search(a.query, a.case, a.libraries, a.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
