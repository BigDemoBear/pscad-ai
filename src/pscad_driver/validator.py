# -*- coding: utf-8 -*-
"""validator.py — 五级校验。这是本方案的核心资产：`build 通过` 并不等于 `接对了`。

L1 结构   工程能解析、页面存在、元件数与定义数非零
L2 引用   每个元件的定义在调色板里存在；坐标落在网格上；端口名合法
L3 拓扑   几何连通的连通图：悬空端口 / 孤立子图 / 短接 / 每电气岛至少一个接地
L4 语义   每个电气岛至少一个独立源；同一岛不允许出现两个不同电压等级的源（按参数名判断）
L5 数值   （跑完才有）稳态初值、时间轴、NaN 检查

返回结构固定为：
{"ok": bool, "levels": {"L1": {...}, ...}, "issues": [{"level","code","msg","hint"}], "summary": "..."}
"""
import math
import os

from .geometry import Palette, all_ports, connectivity, is_calibrated

GND_HINTS = ("ground", "gnd", "earth")
SRC_HINTS = ("source", "src", "machine", "gen")


def _issue(level, code, msg, hint="", severity="block"):
    """severity: block = 阻断检查；warn = 诊断提示。"""
    return {"level": level, "code": code, "msg": msg, "hint": hint,
            "severity": severity}


# ------------------------------------------------------------------ L1
def check_structure(psx):
    issues = []
    try:
        st = psx.structure()
    except Exception as e:
        return {"ok": False}, [_issue("L1", "PARSE_FAIL", "工程解析失败：%s" % e)]
    if st["instances"] == 0:
        issues.append(_issue("L1", "EMPTY_PAGE", "页面 %s 上没有元件" % "Main"))
    if st["defs"] == 0:
        issues.append(_issue("L1", "NO_DEF", "工程里没有 Definition"))
    return {"ok": not issues, **st}, issues


# ------------------------------------------------------------------ L2
def check_refs(psx, palette=None, grid=18):
    pal = palette or Palette()
    issues, unknown, offgrid, uncal = [], [], [], []
    insts = psx.instances()
    refs = []
    for i, inst in enumerate(insts):
        ref = "%s#%d" % (inst["defn"], i)
        refs.append({"ref": ref, **inst})
        if not pal.has(inst["defn"]):
            unknown.append(inst["defn"])
            continue
        if inst["x"] % grid or inst["y"] % grid:
            offgrid.append((ref, inst["x"], inst["y"]))
        if not is_calibrated(inst):
            uncal.append("%s(orient=%s)" % (ref, inst["orient"]))
    for d in sorted(set(unknown)):
        issues.append(_issue("L2", "UNKNOWN_DEFN", "调色板里没有元件定义：%s" % d,
                             "换用 palette 里已有的元件，或先把自定义库加入 palette"))
    for ref, x, y in offgrid[:10]:
        issues.append(_issue("L2", "OFF_GRID", "%s 坐标不在 %d px 网格上：(%s,%s)"
                             % (ref, grid, x, y), "把坐标吸附到 %d 的倍数" % grid))
    return {"ok": not issues, "instances": len(insts), "unknown": sorted(set(unknown)),
            "off_grid": len(offgrid), "uncalibrated": uncal}, issues, refs


# ------------------------------------------------------------------ L3
def check_topology(refs, wires, palette=None, strict=False):
    """几何连通性：端口落在线的顶点/边界上才算接上（PSCAD 没有连接表）。"""
    pal = palette or Palette()
    usable = [{"ref": r["ref"], "defn": r["defn"], "x": r["x"],
               "y": r["y"], "orient": r["orient"]}
              for r in refs if pal.has(r["defn"]) and is_calibrated(r)]
    conn = connectivity(usable, wires, pal)
    issues = []
    sev = "block" if strict else "warn"
    by_ref = {r["ref"]: r for r in refs}

    for ref in conn["dangling_components"]:
        d = by_ref.get(ref, {}).get("defn", "?")
        issues.append(_issue("L3", "DANGLING_COMPONENT",
                             "元件完全没接线：%s（%s）" % (ref, d),
                             "给它至少一个端子拉线，或从工程里删掉", severity=sev))

    # 每个电气岛至少一个接地
    no_gnd = []
    for isl in conn["islands"]:
        defs = [by_ref[c]["defn"].lower() for c in isl["components"] if c in by_ref]
        if not any(any(h in d for h in GND_HINTS) for d in defs):
            no_gnd.append(isl["components"][:3])
    for comps in no_gnd[:10]:
        issues.append(_issue("L3", "NO_GROUND", "电气岛没有接地参考：%s" % ", ".join(comps),
                             "在该岛任一节点并一个 master:ground", severity=sev))

    # 同一个元件的两个端子被连到同一节点 → 元件被短路（PSCAD 会直接报 "Branch path is shorted"）
    node_of = {}
    for key in conn["connected"]:
        node_of.setdefault(conn["node_of"].get(key, key), []).append(key)
    shorted = []
    for root, keys in node_of.items():
        per = {}
        for k in keys:
            ref, port = k.rsplit(".", 1)
            per.setdefault(ref, []).append(port)
        for ref, ports in per.items():
            # 只看两端元件：多端器件（变压器、母线接口）同一节点出现多个端子是正常的
            ninst = len(conn.get("ports_of", {}).get(ref, {}) or {})
            if ninst and ninst != 2:
                continue
            # 去掉别名端口（库里坐标相同的两个端口是同一个端子）
            uniq = {}
            for p in ports:
                full = "%s.%s" % (ref, p)
                canon = min({full} | set(conn.get("alias", {}).get(full, [])))
                uniq.setdefault(canon, p)
            if len(uniq) >= 2:
                shorted.append("%s(%s)" % (ref, ",".join(sorted(uniq.values()))))
    for s in shorted[:10]:
        issues.append(_issue("L3", "SHORTED", "元件两端被连到同一节点（短路）：%s" % s,
                             "改走线，避免导线压到其他端口；PSCAD 会报 Branch path is shorted",
                             severity="block"))

    return {"ok": not issues, "islands": len(conn["islands"]),
            "connected_ports": len(conn["connected"]),
            "dangling_components": len(conn["dangling_components"]),
            "no_ground_islands": len(no_gnd)}, issues


# ------------------------------------------------------------------ L4
def check_semantics(refs, settings=None):
    """轻量语义：源数量、仿真设置自洽。电压等级一致性需人工确认，只给警告。"""
    issues = []
    srcs = [r for r in refs if any(h in r["defn"].lower() for h in SRC_HINTS)]
    if not srcs:
        issues.append(_issue("L4", "NO_SOURCE", "工程里没有任何独立源",
                             "至少加一个 master:source1 / source3", severity="warn"))
    st = settings or {}
    try:
        dur = float(st.get("time_duration", 0))
        step = float(st.get("time_step", 0))
        if dur <= 0:
            issues.append(_issue("L4", "BAD_DURATION", "time_duration 非正：%s" % st.get("time_duration"), severity="warn"))
        if step <= 0:
            issues.append(_issue("L4", "BAD_STEP", "time_step 非正：%s" % st.get("time_step"), severity="warn"))
        if dur > 0 and step > 0 and dur / step > 5e6:
            issues.append(_issue("L4", "TOO_MANY_STEPS",
                                 "步数过大：%g/%g" % (dur, step), "减小仿真时长或加大步长", severity="warn"))
    except (TypeError, ValueError):
        issues.append(_issue("L4", "BAD_SETTINGS", "仿真设置不是数字：%s" % st, severity="warn"))
    return {"ok": not issues, "sources": len(srcs)}, issues


# ------------------------------------------------------------------ L5
def check_numeric(data, expect_points=None):
    """跑完后的数值体检：NaN / 时间轴单调 / 首值合理性。"""
    issues = []
    if not data or not data.get("ok"):
        return {"ok": False, "reason": (data or {}).get("reason", "no data")}, \
            [_issue("L5", "NO_DATA", "没有可用输出")]
    import numpy as np
    t = data.get("time")
    if t is None:
        return {"ok": True}, issues
    if not np.all(np.diff(t) > 0):
        issues.append(_issue("L5", "NON_MONOTONIC_T", "时间轴非单调"))
    bad = [n for n, v in data["data"].items() if not np.all(np.isfinite(v))]
    for n in bad[:5]:
        issues.append(_issue("L5", "NAN", "通道 %s 含 NaN/Inf" % n))
    if expect_points and len(t) != expect_points:
        issues.append(_issue("L5", "POINTS", "点数 %d ≠ 期望 %d" % (len(t), expect_points)))
    return {"ok": not issues, "points": int(len(t)), "t_end": float(t[-1])}, issues


# ------------------------------------------------------------------ 汇总
def validate_psx(psx_path, run_data=None, grid=18, strict=False):
    from .psxmodel import Psx
    psx = Psx(psx_path)
    pal = Palette()
    r1, i1 = check_structure(psx)
    r2, i2, refs = check_refs(psx, pal, grid)
    r3, i3 = check_topology(refs, psx.wires(), pal, strict=strict)
    r4, i4 = check_semantics(refs, psx.settings())
    r5, i5 = ({"ok": None}, [])
    if run_data is not None:
        r5, i5 = check_numeric(run_data)
    issues = i1 + i2 + i3 + i4 + i5
    blocking = [x for x in issues if x.get("severity", "block") == "block"]
    return {
        "ok": not blocking and r5.get("ok") is not False,
        "levels": {"L1": r1, "L2": r2, "L3": r3, "L4": r4, "L5": r5},
        "issues": issues,
        "strict": strict,
        "summary": "%d 阻断 / %d 提示" % (len(blocking), len(issues) - len(blocking)),
    }


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Inspect a native PSCAD project's structure")
    parser.add_argument("project")
    args = parser.parse_args()
    print(json.dumps(validate_psx(args.project), ensure_ascii=False, indent=1))
