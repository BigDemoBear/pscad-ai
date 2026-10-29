# -*- coding: utf-8 -*-
"""palette.py — 把 PSCAD 主库（master.pslx）变成 AI 可读的调色板。

不需要启动 PSCAD，纯 XML 解析，秒级完成。

产出 assets/palette.json：
{
  "source": "...\\master.pslx",
  "count": 345,
  "defs": {
     "resistor": {
        "classid": "UserCmpDefn", "crc": "41315292", "desc": "Resistor ",
        "ports": {"A": {"x":0,"y":0,"dim":0,"model":"Natural","mode":""}, ...},
        "svg_box": [minx,miny,maxx,maxy],
        "groups": ["Passive Elements", ...]        # 来自 StationDefn 归类
     }
  }
}
产出 assets/palette_index.json：{"groups": {...}, "names": [...]}，给 LLM 检索用。
"""
import json
import os
import xml.etree.ElementTree as ET

from .config import path_of


def _fnum(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _parse_definition(d):
    """解析单个 <Definition>：端口、描述、图形包围盒、参数占位。"""
    svg = d.find("svg")
    ports, xs, ys = {}, [], []
    if svg is not None:
        for g in svg:
            if g.tag == "port":
                ports[g.get("name")] = {
                    "x": _fnum(g.get("x"), 0),
                    "y": _fnum(g.get("y"), 0),
                    "dim": _fnum(g.get("dim"), 0),
                    "model": g.get("model") or "",
                    "mode": g.get("mode") or "",
                    "type": g.get("type") or "",
                }
            for k, buf in (("x", xs), ("x1", xs), ("x2", xs), ("cx", xs)):  # 图形范围
                if g.get(k) is not None:
                    v = _fnum(g.get(k))
                    if v is not None:
                        buf.append(v)
            for k, buf in (("y", ys), ("y1", ys), ("y2", ys), ("cy", ys)):
                if g.get(k) is not None:
                    v = _fnum(g.get(k))
                    if v is not None:
                        buf.append(v)
            if g.tag == "rect":
                xs.append(_fnum(g.get("x"), 0) + _fnum(g.get("width"), 0))
                ys.append(_fnum(g.get("y"), 0) + _fnum(g.get("height"), 0))

    box = [min(xs), min(ys), max(xs), max(ys)] if xs and ys else None

    desc = ""
    pl = d.find("paramlist")
    if pl is not None:
        for p in pl.findall("param"):
            if p.get("name") == "Description":
                desc = p.get("value") or ""
                break

    return {
        "classid": d.get("classid"),
        "crc": d.get("crc"),
        "desc": desc,
        "ports": ports,
        "svg_box": box,
        "groups": [],
    }


def _parse_groups(root, defs):
    """StationDefn 的 schematic 里列出了它的成员元件 → 得到元件→分组。"""
    for d in root.iter("Definition"):
        if d.get("classid") != "StationDefn":
            continue
        gname = d.get("name") or ""
        for u in d.iter("User"):
            dn = (u.get("defn") or "").split(":")[-1]
            if dn in defs and gname:
                if gname not in defs[dn]["groups"]:
                    defs[dn]["groups"].append(gname)


def build(master_path=None, out_dir=None):
    """解析主库，写 palette.json + palette_index.json，返回 (palette, index, files)。"""
    if master_path is None:
        from .config import CONFIG, load_config
        cfg = CONFIG or load_config()
        master_path = os.path.join(cfg["pscad"]["install"], "master.pslx")
    if not os.path.isfile(master_path):
        raise FileNotFoundError(master_path)

    root = ET.parse(master_path).getroot()
    defs = {}
    for d in root.iter("Definition"):
        name = d.get("name")
        if not name:
            continue
        info = _parse_definition(d)
        if not info["ports"] and info["classid"] != "UserCmpDefn":
            continue
        defs[name] = info
    _parse_groups(root, defs)

    palette = {
        "source": master_path,
        "pscad_version": root.get("version"),
        "count": len(defs),
        "grid": 18,
        "defs": defs,
    }

    groups = {}
    for name, info in defs.items():
        for g in info["groups"] or ["(ungrouped)"]:
            groups.setdefault(g, []).append(name)
    index = {
        "groups": {g: sorted(v) for g, v in sorted(groups.items())},
        "port_count": sum(len(i["ports"]) for i in defs.values()),
        "names": sorted(defs),
    }

    out_dir = out_dir or path_of("assets")
    os.makedirs(out_dir, exist_ok=True)
    p1 = os.path.join(out_dir, "palette.json")
    p2 = os.path.join(out_dir, "palette_index.json")
    with open(p1, "w", encoding="utf-8") as f:
        json.dump(palette, f, ensure_ascii=False, indent=1)
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    return palette, index, (p1, p2)


def load(assets_dir=None):
    """读已生成的 palette.json。"""
    p = os.path.join(assets_dir or path_of("assets"), "palette.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def search(keyword, palette=None, limit=20):
    """模糊查元件（名称 / 描述 / 分组）。"""
    pal = palette or load()
    kw = keyword.lower()
    hits = []
    for name, info in pal["defs"].items():
        score = 0
        if kw in name.lower():
            score += 3
        if kw in (info["desc"] or "").lower():
            score += 2
        if any(kw in g.lower() for g in info["groups"]):
            score += 1
        if score:
            hits.append((score, name, len(info["ports"]), info["desc"]))
    hits.sort(key=lambda t: (-t[0], t[1]))
    return [{"name": n, "ports": p, "desc": d} for _, n, p, d in hits[:limit]]


if __name__ == "__main__":
    pal, idx, files = build()
    print("定义数:", pal["count"], " 端口数:", idx["port_count"])
    print("分组:", ", ".join("%s(%d)" % (g, len(v)) for g, v in list(idx["groups"].items())[:10]))
    print("产物:", files)
