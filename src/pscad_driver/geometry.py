# -*- coding: utf-8 -*-
"""geometry.py — 端口绝对坐标推算 + 几何连通性判定。

本模块使用的坐标规则：
    端口画布坐标 = (实例左上角 x + 端口相对 x, 实例左上角 y + 端口相对 y)
    端口相对坐标来自 master.pslx 的 <svg><port x y>，单位与画布一致（1:1）。
    实例 x,y 由 PSCAD 落盘时已吸附到 18 px 网格。

方向码0～7与author.transform共享，覆盖旋转和镜像。

连通性模型（PSCAD 4.6 的线是"几何对象"，没有连接表）：
    * `<Wire>`（WireOrthogonal）的连接点在 `<vertex>` 上；
    * `Bus` / `TLine` / `Cable` 这类线对象没有 vertex，连接靠**包围盒边界**；
    * 端口只要落在线的顶点或边界上，就算接上。
"""
import json
import os

from .config import path_of

GRID = 18
_SUPPORTED = range(8)


def snap(v, grid=GRID):
    return int(round(float(v) / grid) * grid)


class Palette:
    def __init__(self, data=None):
        if data is None:
            assets = path_of("assets")
            p = os.path.join(assets, "palette.json")
            if not os.path.isfile(p):
                from .palette import build
                build(out_dir=assets)
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        self.data = data
        self.defs = data["defs"]

    def ports(self, defn):
        d = self.defs.get(defn) or self.defs.get(defn.split(":")[-1])
        if d is None:
            raise KeyError("调色板里没有元件：%s" % defn)
        return d["ports"]

    def has(self, defn):
        return (defn in self.defs) or (defn.split(":")[-1] in self.defs)

    def terminals(self, defn):
        """真正的电气端子（model=Natural）。Transfer 端口是控制信号，允许空置。"""
        return {k: v for k, v in self.ports(defn).items()
                if (v.get("model") or "Natural") == "Natural"}


def port_abs(inst, port, palette=None):
    pal = palette if isinstance(palette, Palette) else Palette(palette)
    orient = int(inst.get("orient", 0))
    if orient not in _SUPPORTED:
        raise NotImplementedError("orient=%s 未标定，拒绝猜测（元件 %s）"
                                  % (orient, inst.get("defn")))
    ports = pal.ports(inst["defn"])
    if port not in ports:
        raise KeyError("元件 %s 没有端口 %s（可用：%s）"
                       % (inst["defn"], port, ", ".join(sorted(ports))))
    px, py = ports[port]["x"], ports[port]["y"]
    from .author import transform
    dx, dy = transform(px, py, orient)
    return (int(inst["x"]) + dx, int(inst["y"]) + dy)


def all_ports(inst, palette=None, skip_unsupported=True):
    pal = palette if isinstance(palette, Palette) else Palette(palette)
    out = {}
    for p in pal.ports(inst["defn"]):
        try:
            out[p] = port_abs(inst, p, pal)
        except NotImplementedError:
            if not skip_unsupported:
                raise
    return out


def is_calibrated(inst):
    return int(inst.get("orient", 0)) in _SUPPORTED


# ------------------------------------------------------------------ 连通
def _on_segment(p, a, b):
    """点是否落在线段上（整数坐标，允许端点）。"""
    (x, y), (x1, y1), (x2, y2) = p, a, b
    if min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
        return (x2 - x1) * (y - y1) == (y2 - y1) * (x - x1)
    return False


def wire_segments(w):
    """把一个线对象变成若干线段。

    pts 存在 → 折线；否则用 bbox 的四条边（Bus / TLine / Cable 的连接靠边界）。
    """
    if w.get("pts"):
        pts = w["pts"]
        return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    box = w.get("box")
    if not box:
        return []
    x, y, ww, hh = box
    p1, p2, p3, p4 = (x, y), (x + ww, y), (x + ww, y + hh), (x, y + hh)
    return [(p1, p2), (p2, p3), (p3, p4), (p4, p1)]


def connectivity(instances, wires, palette=None):
    """构建连通关系。

    instances: [{"ref","defn","x","y","orient"}]
    wires:     [{"id","classid","pts","box"}]

    返回 {ports:{ref.port:(x,y)}, connected:[...], comps:[{ref,total,used}],
          islands:[{ports:[...],wires:[...]}], dangling_components:[...]}
    """
    pal = palette if isinstance(palette, Palette) else Palette(palette)

    port_xy, port_owner = {}, {}
    comp_stat = []
    for inst in instances:
        pts = all_ports(inst, pal)
        terms = set(pal.terminals(inst["defn"]))
        used = 0
        for p, xy in pts.items():
            key = "%s.%s" % (inst["ref"], p)
            port_xy[key] = xy
            port_owner[key] = inst["ref"]
        comp_stat.append({"ref": inst["ref"], "defn": inst["defn"],
                          "total": len(terms), "used": 0, "_terms": terms})

    # 显式折线（WireOrthogonal）→ 参与建图；只有包围盒的线（Bus/TLine/Cable）
    # 只用来"消除悬空"，不参与建图：它们的矩形内部不是连接点，否则会把整排端口误并成一团。
    segs = [(w.get("id"), s) for w in wires if w.get("pts")
            for s in wire_segments(w)]
    boxes = [w for w in wires if not w.get("pts") and w.get("box")]

    # PSCAD 的线上只有"端点"才是连接点；线中段跨过某个端口并不会连上。
    # 只认显式折线（WireOrthogonal）的两个端点。Bus / TLine 的矩形语义未标定，
    # 把它们当"连线"会把同一元件的两个端子误并成一个节点（例如 source3 的 A/C 端子）。
    ends = {}
    for w in wires:
        if w.get("pts"):
            ends[w.get("id")] = {tuple(w["pts"][0]), tuple(w["pts"][-1])}
    hit = {}
    for key, xy in port_xy.items():
        for wid, e in ends.items():
            if xy in e:
                hit.setdefault(key, set()).add(wid)

    touched = set(hit)
    for key, xy in port_xy.items():
        if key in touched:
            continue
        for w in boxes:
            x, y, ww, hh = w["box"]
            if x - 2 <= xy[0] <= x + ww + 2 and y - 2 <= xy[1] <= y + hh + 2:
                touched.add(key)
                break

    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for key, wids in hit.items():
        for wid in wids:
            union(key, "W:%s" % wid)

    # 同一个线对象连起来的端口属于同一节点
    by_wire = {}
    for key, wids in hit.items():
        for wid in wids:
            by_wire.setdefault(wid, []).append(key)
    for wid, keys in by_wire.items():
        for k in keys[1:]:
            union(keys[0], k)

    comps = {s["ref"]: s for s in comp_stat}
    for key in touched:
        ref = port_owner[key]
        port = key.rsplit(".", 1)[1]
        if port in comps[ref]["_terms"]:
            comps[ref]["used"] += 1

    islands = {}
    for key in hit:
        islands.setdefault(find(key), {"ports": [], "wires": set()}). \
            get("ports").append(key)
    for key, wids in hit.items():
        root = find(key)
        islands.setdefault(root, {"ports": [], "wires": set()})["wires"].update(wids)

    dangling = [s["ref"] for s in comp_stat if s["total"] > 0 and s["used"] == 0]
    out_islands = []
    for root, d in islands.items():
        out_islands.append({"ports": sorted(d["ports"]),
                            "wires": sorted(d["wires"]),
                            "components": sorted({port_owner[p] for p in d["ports"]})})

    node_of = {k: "N%d" % (abs(hash(find(k))) % 100000) for k in hit}
    ports_of = {}
    for inst in instances:
        ports_of[inst["ref"]] = pal.terminals(inst["defn"])
    # 同一元件里坐标完全相同的端口是"别名端口"（如 breaker 的 B1/N1），不算短路
    alias = {}
    for inst in instances:
        pts = all_ports(inst, pal)
        for p, xy in pts.items():
            alias.setdefault("%s.%s" % (inst["ref"], p), []).extend(
                "%s.%s" % (inst["ref"], q) for q, xy2 in pts.items() if xy2 == xy and q != p)
    return {
        "ports": port_xy,
        "connected": sorted(touched),
        "wired": sorted(hit),
        "node_of": node_of,
        "ports_of": ports_of,
        "alias": alias,
        "components": [{k: v for k, v in s.items() if not k.startswith("_")}
                       for s in comp_stat],
        "islands": out_islands,
        "dangling_components": sorted(dangling),
    }
