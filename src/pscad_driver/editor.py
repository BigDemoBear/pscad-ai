"""Incremental edits preserve the existing native project and unrelated objects."""
from copy import deepcopy
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from . import author, case
from .layout import route
from .psxmodel import Psx
from .canvas import fit_file as fit_canvas


def _load(case_id):
    directory, path = case.pscx_of(case_id)
    directory = Path(directory)
    refs_path = directory / "refs.json"
    refs = json.loads(refs_path.read_text(encoding="utf-8")) if refs_path.exists() else {}
    plan_path = directory / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    return directory, path, Psx(path), refs, plan


def refresh(psx, refs, libraries=None):
    sources = author.definition_sources(psx.root, libraries)
    for d in psx.root.findall("definitions/Definition"):
        page = d.get("name")
        if d.find("schematic[@classid='UserCanvas']") is None:
            continue
        by_id = {str(v["id"]): k for k, v in refs.get(page, {}).items()}
        current = {}
        for item in psx.instances(page):
            ref = by_id.get(item["id"], item["id"])
            scope, name = item["raw_defn"].split(":", 1)
            definition = author.definition(name, sources[scope])
            current[ref] = {k: item[k] for k in ("id", "params", "x", "y", "orient")}
            current[ref].update(defn=item["raw_defn"], ports=author.active_ports(
                definition, item["params"], item["x"], item["y"], item["orient"]))
        refs[page] = current
    return refs


def inspect(case_id, page=None, keyword="", offset=0, limit=100):
    _, path, psx, refs, plan = _load(case_id)
    refs = refresh(psx, refs, plan.get("libraries"))
    pages = {}
    for name, items in refs.items():
        if page and name != page:
            continue
        rows = [{"ref": ref, **item} for ref, item in items.items()
                if not keyword or keyword.lower() in (ref + " " + item["defn"] + " " + json.dumps(item["params"])).lower()]
        wires = [{**{k: v for k, v in w.items() if k != "_el"},
                  "params": {p.get("name"): p.get("value") for p in w["_el"].findall("User/paramlist/param")}}
                 for w in psx.wires(name)]
        pages[name] = {"total_components": len(rows), "total_wires": len(wires),
                       "components": rows[offset:offset + limit], "wires": wires[offset:offset + limit]}
    return {"pscx": path, "settings": psx.settings(), "pages": pages, "offset": offset, "limit": limit}


def edit(case_id, ops=None, changes=None, settings=None, page="Main", transport=None):
    if transport is None:
        raise ValueError("Incremental editor must run in the shared worker session")
    transport.reset()
    directory, path, psx, refs, plan = _load(case_id)
    # Retain the exact pre-edit native project. The edit journal records intent separately.
    history = directory / "revisions"
    history.mkdir(exist_ok=True)
    stamp = time.time_ns()
    (history / ("before_edit_%s.pscx" % stamp)).write_bytes(Path(path).read_bytes())
    ops = list(ops or [])
    ops = [{"op": "set", "ref": ref, "params": values} for ref, values in (changes or {}).items()] + ops
    if settings:
        for key, value in settings.items():
            el = psx.root.find("paramlist[@name='Settings']/param[@name='%s']" % key)
            if el is None:
                raise KeyError("Unknown project setting: %s" % key)
            el.set("value", str(value))
    pending = []
    for op in ops:
        kind = op["op"]
        op_page = op.get("page", page)
        if kind in ("add", "clone", "connect"):
            pending.append((op_page, op))
            continue
        if kind == "import_definition":
            source = ET.parse(op["file"]).getroot()
            defs = [source] if source.tag == "Definition" else source.findall(".//Definition")
            for d in defs:
                if psx.root.find("definitions/Definition[@name='%s']" % d.get("name")) is not None:
                    raise ValueError("Definition already exists: %s" % d.get("name"))
                psx.root.find("definitions").append(deepcopy(d))
            continue
        if kind in ("disconnect", "wire_set"):
            wire = next((w for w in psx.wires(op_page) if w["id"] == str(op["id"])), None)
            if wire is None:
                raise KeyError("Wire not found: %s" % op["id"])
            if kind == "disconnect":
                psx.page(op_page).remove(wire["_el"])
            else:
                params = {p.get("name"): p for p in wire["_el"].findall("User/paramlist/param")}
                for key, value in op["params"].items():
                    if key not in params:
                        raise KeyError("Wire %s has no parameter %s" % (op["id"], key))
                    params[key].set("value", str(value))
            continue
        ref = str(op.get("ref", op.get("iid", "")))
        iid = refs.get(op_page, {}).get(ref, {}).get("id", ref)
        item = psx.find(iid=iid, page=op_page)
        if item is None:
            raise KeyError("Component not found: %s/%s" % (op_page, ref))
        if kind == "set":
            params = {p.get("name"): p for p in item["_el"].findall("paramlist/param")}
            for key, value in op["params"].items():
                if key not in params:
                    raise KeyError("%s has no parameter %s" % (ref, key))
                params[key].set("value", str(value))
        elif kind == "move":
            for key in ("x", "y", "orient"):
                if key in op:
                    value = int(op[key])
                    if key == "orient":
                        author.transform(0, 0, value)
                    elif value % 18:
                        raise ValueError("Coordinates must be multiples of 18")
                    item["_el"].set(key, str(value))
        elif kind == "remove":
            psx.page(op_page).remove(item["_el"])
        else:
            raise ValueError("Unsupported edit operation: %s" % kind)
    psx.save()
    refs = refresh(psx, refs, plan.get("libraries"))
    if pending:
        project = transport.load(path, libraries=plan.get("libraries"))
        sources = author.definition_sources(psx.root, plan.get("libraries"))
        for op_page, op in pending:
            canvas = project.user_canvas(op_page)
            page_refs = refs.setdefault(op_page, {})
            kind = op["op"]
            if kind == "connect":
                ends = []
                for endpoint in (op["from"], op["to"]):
                    ref, port = endpoint.rsplit(".", 1)
                    pt = page_refs[ref]["ports"][port]
                    ends.append((pt["x"], pt["y"]))
                vertices = [ends[0], *map(tuple, op["via"]), ends[1]] if "via" in op else route(*ends)
                if ends[0] != ends[1]:
                    canvas.add_wire(*vertices)
                continue
            item = dict(op)
            if kind == "clone":
                source = page_refs[str(op["source"])]
                item = {**source, **op, "params": {**source["params"], **op.get("params", {})}}
            ref = str(item["ref"])
            if ref in page_refs:
                raise ValueError("Duplicate component ref: %s" % ref)
            dn = item["defn"]
            scope, name = dn.split(":", 1) if ":" in dn else ("master", dn)
            scope = psx.name if scope == "self" else scope
            d = author.definition(name, sources[scope])
            known = {p.get("name") for p in d.findall("form//parameter")}
            if set(item.get("params", {})) - known:
                raise KeyError("Unknown parameters for %s" % dn)
            x, y = int(item["x"]), int(item["y"])
            if x % 18 or y % 18:
                raise ValueError("Coordinates must be multiples of 18")
            obj = canvas.add_component(scope, name, x, y)
            if item.get("params"):
                obj.set_parameters(**{k: str(v) for k, v in item["params"].items()})
            orient = item.get("orient", 0)
            author.orient_component(obj, orient)
            x, y = map(int, obj.get_location())
            values = obj.get_parameters()
            page_refs[ref] = {"id": str(obj._id[0]), "defn": scope + ":" + name,
                              "params": values, "ports": author.active_ports(d, values, x, y, orient)}
        project.save()
        transport.saved(path)
        psx = Psx(path)
        refs = refresh(psx, refs, plan.get("libraries"))
    (directory / "refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(directory / "edits.jsonl", "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time_ns": stamp, "page": page, "settings": settings,
                                 "ops": ops, "source_of_truth": path}, ensure_ascii=False) + "\n")
    canvas = fit_canvas(path, libraries=plan.get("libraries", []), backup=False)
    if canvas["changed"]:
        transport.reset()
    return {"ok": True, "pscx": path, "operations": len(ops), "journal": str(directory / "edits.jsonl"), "canvas": canvas,
            "note": "Existing wires stay in place on move/remove; explicitly disconnect and reconnect affected wires."}
