# -*- coding: utf-8 -*-
"""assemble.py — 在线装配：调色板取件 → API 落图 → 按端口坐标拉线 → 截图自检。

通过画布 API 创建元件和导线，由 PSCAD 填充实例内部字段。
装配计划描述元件类型、参数、位置和连接。

用法（plan 结构）：
{
  "case": "demo",
  "components": [
     {"ref": "SRC", "defn": "source1", "params": {"Vmag": "1.0"}},
     {"ref": "R1",  "defn": "resistor", "params": {"R": "10"}},
     {"ref": "G1",  "defn": "ground"}
  ],
  "connections": [["SRC.A", "R1.A"], ["R1.B", "G1.A"]]
}
"""
import json
import os

from . import geometry, manifest, vision
from .config import path_of
from .layout import place, route


def load_plan(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def assemble(transport, prj, case_dir, plan, palette=None, snapshot=True,
             tag="asm", force=False):
    """按 plan 在已加载的工程上装出拓扑。返回报告 dict。

    幂等：同一 case 只允许装配一次（重复装配会叠元件、叠线）。
    要重来就 `case new --force` 重建工况，或传 force=True。
    """
    marker = os.path.join(case_dir, "assembled.json")
    if os.path.isfile(marker) and not force:
        raise FileExistsError("该工况已装配过（%s）；请新建工况或 force=True" % marker)
    pal = palette or geometry.Palette()
    comps = plan["components"]
    placed = place(comps, palette=pal)

    canvas = prj.user_canvas("Main")
    created, failed = {}, []

    for c in placed:
        ref, defn = c["ref"], c["defn"]
        if not pal.has(defn):
            failed.append({"ref": ref, "why": "调色板无此元件：%s" % defn})
            continue
        try:
            obj = canvas.add_component("master", defn.split(":")[-1], c["x"], c["y"])
            loc = obj.get_location()
            created[ref] = {"obj": obj, "id": None, "loc": list(loc),
                            "defn": defn, "params": c.get("params") or {}}
            # 落图后坐标会被吸附，回填实际值，后续拉线用实际坐标
            c["x"], c["y"] = int(loc[0]), int(loc[1])
            c["ports"] = {p: (c["x"] + v["x"], c["y"] + v["y"])
                          for p, v in pal.ports(defn).items()}
            if c.get("params"):
                obj.set_parameters(**{k: str(v) for k, v in c["params"].items()})
        except Exception as e:
            failed.append({"ref": ref, "why": "%s: %s" % (type(e).__name__, e)})

    wires, wire_fail = [], []
    # 所有端口坐标：走线必须避开它们，否则两根线压到同一端口就会短路
    all_port_xy = [xy for c in placed for xy in (c.get("ports") or {}).values()]
    for a, b in plan.get("connections", []):
        ra, pa = a.split(".")
        rb, pb = b.split(".")
        if ra not in created or rb not in created:
            wire_fail.append({"conn": [a, b], "why": "端点元件未创建成功"})
            continue
        try:
            xy1 = _port(created[ra], c_of(placed, ra), pa)
            xy2 = _port(created[rb], c_of(placed, rb), pb)
            pts = route(xy1, xy2, blocked=all_port_xy)
            w = canvas.add_wire(*pts)
            wires.append({"conn": [a, b], "wire": str(w), "pts": pts})
        except Exception as e:
            wire_fail.append({"conn": [a, b], "why": "%s: %s" % (type(e).__name__, e)})

    prj.save()
    with open(marker, "w", encoding="utf-8") as f:
        json.dump({"refs": [c["ref"] for c in placed], "wires": len(wires)},
                  f, ensure_ascii=False, indent=1)

    report = {
        "ok": not failed and not wire_fail,
        "components": len(created), "wires": len(wires),
        "failed": failed, "wire_failed": wire_fail,
        "placed": [{"ref": c["ref"], "defn": c["defn"], "x": c["x"], "y": c["y"]}
                   for c in placed],
        "wiring": wires,
    }
    if snapshot:
        try:
            xs = [c["x"] for c in placed] + [c["x"] + 200 for c in placed]
            ys = [c["y"] for c in placed] + [c["y"] + 200 for c in placed]
            bbox = (min(xs) - 40, min(ys) - 40, max(xs) + 40, max(ys) + 40)
            p, size = vision.snapshot(canvas, os.path.join(case_dir, "canvas.png"), bbox,
                                      expected_pid=transport._pid)
            report["snapshot"] = {"path": p, "size": list(size)}
        except Exception as e:
            report["snapshot"] = {"error": str(e)}
    return report


def c_of(placed, ref):
    for c in placed:
        if c["ref"] == ref:
            return c
    raise KeyError(ref)


def _port(created, comp, name):
    ports = comp["ports"]
    if name not in ports:
        raise KeyError("元件 %s 没有端口 %s（可用：%s）"
                       % (comp["defn"], name, ",".join(sorted(ports))))
    return ports[name]


if __name__ == "__main__":
    import sys
    print(json.dumps(load_plan(sys.argv[1]), ensure_ascii=False, indent=1)[:800])
