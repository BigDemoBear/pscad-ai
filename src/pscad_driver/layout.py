# -*- coding: utf-8 -*-
"""layout.py — 只负责"排在哪"，不负责"连什么"。

输入：元件清单（ref/defn/类别）+ 连接关系
输出：每个 ref 的画布坐标（全部落在 18 px 网格、互不重叠）

分列规则：源 → 无源 → 变换 → 负荷 → 测量。生成侧一律 orient=0，避免方向标定问题。
"""
from .geometry import Palette, snap

COLS = {
    "source": 0, "src": 0, "machine": 0, "gen": 0,
    "transformer": 2, "xfmr": 2, "tline": 2, "cable": 2,
    "load": 3, "rlc": 3,
    "meter": 4, "out": 4, "graph": 5,
}


def _category(defn):
    d = defn.split(":")[-1].lower()
    if any(k in d for k in ("source", "src", "machine", "gen")):
        return "source"
    if any(k in d for k in ("transformer", "xfmr", "tline", "cable", "line")):
        return "transformer"
    if any(k in d for k in ("load", "rlc")):
        return "load"
    if any(k in d for k in ("meter", "out", "graph", "voltmeter", "ammeter", "multimeter")):
        return "meter"
    return "load"


def place(components, x0=180, y0=180, col_gap=180, row_gap=90, palette=None):
    """components: [{"ref","defn"}] → [{**c, "x","y","w","h","col","row"}]

    同一列内按出现顺序向下排；不用真实符号尺寸，用固定格距即可（布局只是排布，不参与连通判定）。
    """
    pal = palette if isinstance(palette, Palette) else Palette(palette)
    cols = {}
    out = []
    for c in components:
        cat = _category(c["defn"])
        col = COLS.get(cat, 3)
        row = cols.get(col, 0)
        cols[col] = row + 1
        # plan 里给了坐标就用它（吸附到网格），否则按列自动排
        x = snap(c["x"]) if c.get("x") is not None else snap(x0 + col * col_gap)
        y = snap(c["y"]) if c.get("y") is not None else snap(y0 + row * row_gap)
        ports = {}
        if pal.has(c["defn"]):
            ports = {p: (x + v["x"], y + v["y"]) for p, v in pal.ports(c["defn"]).items()}
        out.append({**c, "x": x, "y": y, "orient": 0, "col": col, "row": row,
                    "ports": ports})
    return out


def _on_seg(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    if min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
        return (x2 - x1) * (y - y1) == (y2 - y1) * (x - x1)
    return False


def _clean(path, blocked):
    """路径不能压到除首尾以外的任何端口（压到就等于把它们短接）。"""
    ends = {path[0], path[-1]}
    for a, b in zip(path, path[1:]):
        for p in blocked:
            if p in ends:
                continue
            if _on_seg(p, a, b):
                return False
    return True


def route(p1, p2, prefer="h", blocked=None, grid=None):
    """两点正交连线；自动避让其他端口，避免"导线压过端口"造成短路。

    blocked: 其他端口的坐标列表（assemble 会传进来）
    """
    grid = grid or 18
    p1, p2 = tuple(p1), tuple(p2)
    cand = []
    if p1[0] == p2[0] or p1[1] == p2[1]:
        cand.append([p1, p2])                            # 直线（可能压到别的端口）
        for d in (grid, -grid, 2 * grid, -2 * grid):     # 绕行
            if p1[1] == p2[1]:                           # 水平线 → 上下绕
                cand.append([p1, (p1[0], p1[1] + d), (p2[0], p1[1] + d), p2])
            else:                                        # 垂直线 → 左右绕
                cand.append([p1, (p1[0] + d, p1[1]), (p1[0] + d, p2[1]), p2])
    else:
        mx, my = snap((p1[0] + p2[0]) / 2, grid), snap((p1[1] + p2[1]) / 2, grid)
        cand.append([p1, (mx, p1[1]), (mx, p2[1]), p2])   # 先横
        cand.append([p1, (p1[0], my), (p2[0], my), p2])   # 先竖
        for d in (grid, -grid, 2 * grid, -2 * grid):
            cand.append([p1, (p1[0] + d, p1[1]), (p1[0] + d, p2[1]), p2])
            cand.append([p1, (p1[0], p1[1] + d), (p2[0], p1[1] + d), p2])
    if not blocked:
        return cand[0]
    for c in cand:
        if _clean(c, blocked):
            return c
    return cand[0]
