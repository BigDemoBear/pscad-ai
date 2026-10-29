"""Create a genuinely empty project and author circuits with the native API.

The JSON plan describes the model, not a particular experiment or answer.
Master-library definitions supply parameter names, defaults, units and port conditions.
"""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .config import load_config, path_of, child_path
from .canvas import fit_file as fit_canvas
from .layout import route
from .psxmodel import Psx
from .transport import Transport


def library_root():
    return ET.parse(Path(load_config()["pscad"]["install"]) / "master.pslx").getroot()


def definition(name, root=None):
    root = root if root is not None else library_root()
    found = root.find("definitions/Definition[@name='%s']" % name.split(":")[-1])
    if found is None:
        raise KeyError("No definition: %s" % name)
    return found


def schema(name):
    d = definition(name)
    return {
        "name": d.get("name"),
        "description": d.find("paramlist/param[@name='Description']").get("value", ""),
        "parameters": [{**p.attrib, "default": p.findtext("value", ""),
                        "choices": [c.text for c in p.findall("choice")],
                        "condition": p.findtext("cond", "true"),
                        "help": p.findtext("help", "")}
                       for p in d.findall("form//parameter")],
        "ports": [{**p.attrib, "condition": p.text or "true"}
                  for p in d.findall("svg/port")],
        "script": {p.get("name"): p.text or "" for p in d.findall("script/segment")},
    }


def condition(expression, params):
    """Evaluate the arithmetic/boolean expressions used by library SVG ports."""
    expression = expression.strip().lower().replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression).strip()
    tree = ast.parse(expression, mode="eval")
    allowed = (ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
               ast.Name, ast.Load, ast.Constant, ast.And, ast.Or, ast.Not,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.USub, ast.UAdd,
               ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
    env = {"true": True, "false": False}
    for key, value in params.items():
        try:
            env[key.lower()] = float(str(value).split("[")[0].strip())
        except ValueError:
            env[key.lower()] = str(value)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError("Unsupported port expression: %s" % expression)
    return bool(eval(compile(tree, "<port-condition>", "eval"), {"__builtins__": {}}, env))


def transform(dx, dy, orient):
    if orient not in range(8):
        raise ValueError("orient must be 0..7")
    return ((dx, dy), (-dy, dx), (-dx, -dy), (dy, -dx),
            (-dx, dy), (-dy, -dx), (dx, -dy), (dy, dx))[orient]


def orient_component(obj, orient):
    if orient not in range(8):
        raise ValueError("orient must be 0..7")
    if orient >= 4:
        obj._generic("IDM_MIRROR")
    for _ in range(orient % 4):
        obj._generic("IDM_ROTATERIGHT")


def definition_sources(project_root, libraries=None):
    roots = [library_root(), project_root]
    roots.extend(ET.parse(p).getroot() for p in (libraries or []))
    return {r.get("name"): r for r in roots}


def active_ports(d, params, x=0, y=0, orient=0):
    ports = {}
    for p in d.findall("svg/port"):
        if condition(p.text or "true", params):
            dx, dy = transform(int(p.get("x", 0)), int(p.get("y", 0)), orient)
            ports[p.get("name")] = {**p.attrib, "x": x + dx, "y": y + dy}
    return ports


def blank(case_id, settings=None):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,29}", case_id):
        raise ValueError("Project name must be an ASCII identifier, at most 30 characters")
    directory = Path(child_path(path_of("cases"), case_id))
    directory.mkdir(exist_ok=True)
    dst = directory / (case_id + ".pscx")
    if dst.exists():
        raise FileExistsError("Project already exists: %s" % dst)
    shell_path = Path(path_of("assets")) / "blank.pscx"
    if not shell_path.exists():
        shell_path = Path(__file__).parent / "assets" / "blank.pscx"
    shell = ET.parse(shell_path)
    root = shell.getroot()
    root.set("name", case_id)
    for el in root.iter():
        for key, value in list(el.attrib.items()):
            if value.startswith("blank:"):
                el.set(key, case_id + value[5:])
    values = {"time_duration": "0.2", "time_step": "20", "sample_step": "100",
              "output_filename": case_id + ".out", "snapshot_filename": case_id + ".snp"}
    values.update({str(k): str(v) for k, v in (settings or {}).items()})
    pl = root.find("paramlist[@name='Settings']")
    for key, value in values.items():
        p = pl.find("param[@name='%s']" % key)
        if p is None:
            raise KeyError("Unknown project setting: %s" % key)
        p.set("value", value)
    ET.indent(shell)
    shell.write(dst, encoding="utf-8")
    (directory / "case.json").write_text(json.dumps({"case_id": case_id, "origin": "empty-project"},
                                                    indent=2), encoding="utf-8")
    return directory, dst


def compose(case_id, plan, replace=False, transport=None):
    """Create/rebuild a project from a full plan. No old electrical topology is inherited.

    Optional definition_files contain native Definition XML for local components/modules.
    Each page in plan.pages has its own components/connections/outputs.
    Main-page components/connections/outputs can instead be at the plan's top level.
    """
    directory = Path(child_path(path_of("cases"), case_id))
    owned_transport = transport is None
    if transport is not None:
        transport.reset()
    dst = directory / (case_id + ".pscx")
    if replace and dst.exists():
        # Keep prior model revisions; leave build/output files to the runner's freshness check.
        history = directory / "revisions"
        history.mkdir(exist_ok=True)
        revision = len(list(history.glob("*.pscx"))) + 1
        dst.replace(history / ("model_%03d.pscx" % revision))
        pp = directory / "plan.json"
        if pp.exists():
            pp.replace(history / ("plan_%03d.json" % revision))
    directory, dst = blank(case_id, plan.get("settings"))
    tree = ET.parse(dst)
    defs = tree.getroot().find("definitions")
    for file in plan.get("definition_files", []):
        source = ET.parse(file).getroot()
        ds = [source] if source.tag == "Definition" else list(source.findall(".//Definition"))
        for d in ds:
            if defs.find("Definition[@name='%s']" % d.get("name")) is not None:
                raise ValueError("Duplicate local definition: %s" % d.get("name"))
            defs.append(deepcopy(d))
    tree.write(dst, encoding="utf-8")
    sources = definition_sources(tree.getroot(), plan.get("libraries"))
    pages = plan.get("pages") or {"Main": plan}
    refs = {}
    transport = transport or Transport(audit=str(Path(path_of("logs")) / "author.jsonl"))
    try:
        project = transport.load(str(dst), libraries=plan.get("libraries"))
        for page, spec in pages.items():
            canvas = project.user_canvas(page)
            refs[page] = {}
            for i, item in enumerate(spec.get("components", [])):
                ref = item["ref"]
                if ref in refs[page]:
                    raise ValueError("Duplicate component ref: %s" % ref)
                dn = item["defn"]
                lib, name = dn.split(":", 1) if ":" in dn else ("master", dn)
                if lib == "self":
                    lib = case_id
                d = definition(name, sources[lib])
                known = {p.get("name") for p in d.findall("form//parameter")}
                unknown = set(item.get("params", {})) - known
                if unknown:
                    raise KeyError("%s: unknown parameters %s; read component_schema" % (ref, sorted(unknown)))
                x = round(item.get("x", 180 + (i % 5) * 216) / 18) * 18
                y = round(item.get("y", 180 + (i // 5) * 180) / 18) * 18
                obj = canvas.add_component(lib, name, x, y)
                if obj is None:
                    raise RuntimeError("PSCAD did not create %s (%s)" % (ref, dn))
                if item.get("params"):
                    obj.set_parameters(**{k: str(v) for k, v in item["params"].items()})
                orient_component(obj, item.get("orient", 0))
                loc = obj.get_location()
                params = obj.get_parameters()
                ports = active_ports(d, params, int(loc[0]), int(loc[1]), item.get("orient", 0))
                refs[page][ref] = {"id": str(obj._id[0]), "defn": lib + ":" + name,
                                   "params": params, "ports": ports}
            blocked = [(p["x"], p["y"]) for c in refs[page].values() for p in c["ports"].values()]
            for conn in spec.get("connections", []):
                a, b = (conn["from"], conn["to"]) if isinstance(conn, dict) else conn
                ends = []
                for endpoint in (a, b):
                    ref, port = endpoint.rsplit(".", 1)
                    p = refs[page][ref]["ports"].get(port)
                    if p is None:
                        raise KeyError("Inactive/missing port %s; active ports: %s" %
                                       (endpoint, list(refs[page][ref]["ports"])))
                    ends.append((p["x"], p["y"]))
                if ends[0] == ends[1]:
                    continue
                via = conn.get("via") if isinstance(conn, dict) else None
                vertices = [ends[0], *map(tuple, via), ends[1]] if via else route(*ends, blocked=blocked)
                canvas.add_wire(*vertices)
            for i, out in enumerate(spec.get("outputs", [])):
                x, y = out.get("x", 180), out.get("y", 1260 + i * 72)
                label = canvas.add_component("master", "datalabel", x, y)
                label.set_parameters(Name=out["signal"])
                pgb = canvas.add_component("master", "pgb", x + 108, y)
                pgb.set_parameters(Name=out.get("name", out["signal"]),
                                   Units=out.get("units", ""), Scale=str(out.get("scale", 1)),
                                   Group=out.get("group", ""))
                la, lb = label.get_location(), pgb.get_location()
                canvas.add_wire((int(la[0]), int(la[1])), (int(lb[0]), int(lb[1])))
            project.save()
            transport.saved(str(dst))
    finally:
        if owned_transport:
            transport.close()
    (directory / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")
    canvas = fit_canvas(dst, libraries=plan.get("libraries", []), backup=False)
    if canvas["changed"]:
        transport.reset()
        refs = json.loads((directory / "refs.json").read_text(encoding="utf-8"))
    return {"ok": True, "case_id": case_id, "pscx": str(dst),
            "refs": refs, "structure": Psx(dst).structure(), "canvas": canvas}


def inspect(case_id):
    from .case import pscx_of
    directory, path = pscx_of(case_id)
    p = Psx(path)
    pages = {}
    for d in p.root.findall("definitions/Definition"):
        if d.find("schematic[@classid='UserCanvas']") is not None:
            pages[d.get("name")] = [{k: v for k, v in i.items() if k != "_el"}
                                   for i in p.instances(d.get("name"))]
    refs = Path(directory) / "refs.json"
    return {"pscx": path, "settings": p.settings(), "pages": pages,
            "refs": json.loads(refs.read_text(encoding="utf-8")) if refs.exists() else {}}


def patch(case_id, changes=None, settings=None, page="Main"):
    from .case import pscx_of
    directory, path = pscx_of(case_id)
    psx = Psx(path)
    refs_path = Path(directory) / "refs.json"
    refs = json.loads(refs_path.read_text(encoding="utf-8")) if refs_path.exists() else {}
    # Normalize refs written by the first authoring release: mhrc stores an id chain tuple.
    for page_refs in refs.values():
        for item in page_refs.values():
            match = re.fullmatch(r"\((\d+),\)", item["id"])
            if match:
                item["id"] = match.group(1)
    plan_path = Path(directory) / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
    page_plan = (plan.get("pages", {}).get(page) if "pages" in plan else plan) if plan else None
    changed = {}
    for ref, params in (changes or {}).items():
        iid = refs.get(page, {}).get(ref, {}).get("id", ref)
        inst = psx.find(iid=iid, page=page)
        if inst is None:
            raise KeyError("No component ref/id: %s" % ref)
        existing = {p.get("name"): p for p in inst["_el"].findall("paramlist/param")}
        for key, value in params.items():
            if key not in existing:
                raise KeyError("%s has no parameter %s" % (ref, key))
            old = existing[key].get("value")
            existing[key].set("value", str(value))
            changed[ref + "." + key] = [old, str(value)]
        for stable_ref, item in refs.get(page, {}).items():
            if item["id"] == iid:
                item["params"].update({k: str(v) for k, v in params.items()})
                if page_plan:
                    for component in page_plan.get("components", []):
                        if component["ref"] == stable_ref:
                            component.setdefault("params", {}).update({k: str(v) for k, v in params.items()})
    for key, value in (settings or {}).items():
        el = psx.root.find("paramlist[@name='Settings']/param[@name='%s']" % key)
        if el is None:
            raise KeyError("Unknown setting: %s" % key)
        changed["settings." + key] = [el.get("value"), str(value)]
        el.set("value", str(value))
    if plan is not None and settings:
        plan.setdefault("settings", {}).update({k: str(v) for k, v in settings.items()})
    psx.save()
    if refs_path.exists():
        refs_path.write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")
    if plan is not None:
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    canvas = fit_canvas(path)
    return {"changed": changed, "pscx": path, "canvas": canvas}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    q = subs.add_parser("schema"); q.add_argument("name")
    q = subs.add_parser("build"); q.add_argument("case_id"); q.add_argument("plan"); q.add_argument("--replace", action="store_true")
    q = subs.add_parser("inspect"); q.add_argument("case_id")
    q = subs.add_parser("patch"); q.add_argument("case_id"); q.add_argument("patch_file")
    a = parser.parse_args(argv)
    if a.command == "schema":
        result = schema(a.name)
    elif a.command == "build":
        from .tasks import request
        result = request("submit", {"op": "create", "case_id": a.case_id,
                                    "plan": json.loads(Path(a.plan).read_text(encoding="utf-8-sig")), "replace": a.replace})
    elif a.command == "inspect":
        from .editor import inspect as inspect_model
        result = inspect_model(a.case_id)
    else:
        from .tasks import request
        result = request("submit", {"op": "edit", "case_id": a.case_id,
                                    **json.loads(Path(a.patch_file).read_text(encoding="utf-8-sig"))})
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
