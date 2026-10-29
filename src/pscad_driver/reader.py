# -*- coding: utf-8 -*-
"""reader.py — 离线读 PSCAD 4.6.2 输出（不依赖 mhrc，不需要 PSCAD 在运行）。

PSCAD 4.6.2 文本输出格式：
  <base>.inf       每行一个通道：PGB(n) Output Desc=".." Group=".." Units=".."
  <base>_01.out    TIME + 通道 1..10
  <base>_02.out    TIME + 通道 11..20   （每 10 通道一个文件，首行为空行）
"""
import json
import os
import re

import numpy as np

INF_RE = re.compile(
    r'PGB\(\s*(\d+)\s*\)\s+(\S+)\s+Desc="([^"]*)"\s+Group="([^"]*)"'
    r'(?:\s+Max=(\S+))?(?:\s+Min=(\S+))?(?:\s+Units="([^"]*)")?'
)


def read_inf(inf_path):
    """解析 .inf → 按 PGB 序号排序的通道元数据。"""
    items = []
    with open(inf_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = INF_RE.search(line)
            if not m:
                continue
            items.append({
                "pgb": int(m.group(1)), "type": m.group(2),
                "desc": m.group(3), "group": m.group(4),
                "max": m.group(5), "min": m.group(6), "units": m.group(7) or "",
            })
    items.sort(key=lambda x: x["pgb"])
    return items


def channel_names(items):
    """列名：默认用 desc；desc 重名时自动加 group 前缀。"""
    cnt = {}
    for it in items:
        cnt[it["desc"]] = cnt.get(it["desc"], 0) + 1
    dups = {k for k, v in cnt.items() if v > 1}
    return ["%s:%s" % (it["group"], it["desc"]) if (it["desc"] in dups and it["group"])
            else it["desc"] for it in items]


def _load_matrix(path):
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                rows.append([float(x) for x in parts])
            except ValueError:
                continue
    if not rows:
        return np.zeros((0, 0))
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise ValueError("Incomplete output rows: %s" % path)
    return np.array(rows, dtype=float)


def read_out(basename):
    """读 <base>.inf + <base>_01.out... → dict(time, data{name: ndarray}, channels, points, files)。"""
    inf = basename + ".inf"
    if not os.path.isfile(inf):
        raise FileNotFoundError("找不到通道清单：%s" % inf)
    items = read_inf(inf)
    names = channel_names(items)
    if not items:
        raise ValueError("No output channels in %s" % inf)
    if len(set(names)) != len(names):
        names = ["%s#%d" % (n, it["pgb"]) for n, it in zip(names, items)]

    files, blocks, time = [], [], None
    for i in range(1, (len(items) + 9) // 10 + 1):
        p = "%s_%02d.out" % (basename, i)
        if not os.path.isfile(p):
            raise ValueError("Missing output channel block: %s" % p)
        m = _load_matrix(p)
        if m.size == 0:
            raise ValueError("Empty output channel block: %s" % p)
        files.append(p)
        if time is None:
            time, blocks = m[:, 0], [m[:, 1:]]
        else:
            if m.shape[0] != len(time) or not np.array_equal(m[:, 0], time):
                raise ValueError("Output blocks have different time axes: %s" % p)
            blocks.append(m[:, 1:])
    if not files:
        raise FileNotFoundError("未找到 %s_01.out 等数据文件" % basename)

    mat = np.hstack(blocks)
    if mat.shape[1] != len(items):
        raise ValueError("Expected %d channels, found %d" % (len(items), mat.shape[1]))
    if not np.all(np.isfinite(mat)) or not np.all(np.isfinite(time)) or not np.all(np.diff(time) > 0):
        raise ValueError("Nonfinite values or invalid time axis in %s" % basename)
    data = {}
    for i, n in enumerate(names):
        if i < mat.shape[1]:
            data[n] = mat[:, i]
    return {"time": time, "data": data, "channels": names,
            "points": int(len(time)), "files": files,
            "meta": [{k: v for k, v in it.items() if k != "pgb"} for it in items]}


def to_csv(base, csv_path=None):
    r = read_out(base)
    csv_path = csv_path or (base + "_all.csv")
    cols = [r["time"]] + [r["data"][c] for c in r["channels"] if c in r["data"]]
    import csv
    names = ["TIME"] + [c for c in r["channels"] if c in r["data"]]
    with open(csv_path, "w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerow(csv_headers(names))
        np.savetxt(stream, np.column_stack(cols), delimiter=",", fmt="%.10g")
    return csv_path


def csv_headers(names):
    """Escape spreadsheet formulas and leading apostrophes without changing raw channels."""
    return ["'" + name if (name.startswith(("'", "\t", "\r", "\n"))
                           or name.lstrip().startswith(('=', '+', '-', '@')))
            else name for name in names]


def summary(run_dir, basename=None):
    """给定 run 目录（含 <ns>.inf），返回可直接进 manifest 的摘要。"""
    cand = [basename + ".inf"] if basename else (
        [f for f in os.listdir(run_dir) if f.endswith(".inf")] if os.path.isdir(run_dir) else [])
    if not cand:
        return {"ok": False, "reason": "no .inf in %s" % run_dir}
    base = os.path.join(run_dir, os.path.splitext(cand[0])[0])
    r = read_out(base)
    return {
        "ok": True, "namespace": os.path.basename(base),
        "channels": r["channels"], "points": r["points"],
        "t_range": [float(r["time"][0]), float(r["time"][-1])],
        "files": [os.path.basename(p) for p in r["files"]],
        "units": {c: m.get("units", "") for c, m in zip(r["channels"], r["meta"])},
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Summarize a PSCAD output directory")
    parser.add_argument("run_dir")
    args = parser.parse_args()
    print(json.dumps(summary(args.run_dir), ensure_ascii=False, indent=1))
