# -*- coding: utf-8 -*-
"""baseline.py — 基线与差分：证明"同工况重跑结果不变，改参数只改该改的地方"。

用法（也由 CLI 暴露）：
    save(case_id, tag)           把某次运行冻结成基线
    diff(case_id, tag)           某次运行 vs 该 case 的基线
    compare(dir_a, dir_b)        任意两次运行之间比较

判定（同名通道集合必须相同）：
    原始时间轴与通道数值的浮点表示完全一致 → bit_exact
    max|Δ| <= atol + rtol*max|ref| → numerical（数值一致，末位差）
    否则                   → changed     （列出变化通道）
不同时间轴只在当前结果覆盖参考范围时插值；插值后的结果最多判为numerical。
"""
import json
import csv
import os
import shutil

import numpy as np

from . import manifest, reader
from .config import path_of, child_path

ATOL = 1e-12
RTOL = 1e-6


def _csv_of(run_dir):
    """定位channels.csv；没有时从同目录.inf/.out生成。"""
    p = os.path.join(run_dir, "channels.csv")
    if os.path.isfile(p):
        return p
    infs = [f for f in os.listdir(run_dir) if f.endswith(".inf")] if os.path.isdir(run_dir) else []
    if not infs:
        raise FileNotFoundError("目录里既没有 channels.csv 也没有 .inf：%s" % run_dir)
    base = os.path.join(run_dir, os.path.splitext(infs[0])[0])
    return reader.to_csv(base, os.path.join(run_dir, "channels.csv"))


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as stream:
        rows = csv.reader(stream)
        names = next(rows, [])
        if "TIME" not in names or len(names) < 2 or len(set(names)) != len(names):
            raise ValueError("CSV must contain TIME and unique signal channel names: %s" % path)
        samples = [row for row in rows if row]
    if not samples or any(len(row) != len(names) for row in samples):
        raise ValueError("CSV must contain complete nonempty sample rows: %s" % path)
    matrix = np.asarray(samples, dtype=float)
    values = {name: matrix[:, i] for i, name in enumerate(names)}
    if not values["TIME"].size or not all(np.isfinite(v).all() for v in values.values()):
        raise ValueError("CSV must contain nonempty finite samples: %s" % path)
    if not np.all(np.diff(values["TIME"]) > 0):
        raise ValueError("CSV time must be strictly increasing: %s" % path)
    return values


def _align(ref, cur):
    """仅在覆盖参考时间范围时，将当前结果插值到参考样点。"""
    t_ref, t_cur = ref["TIME"], cur["TIME"]
    if np.array_equal(t_ref, t_cur):
        return ref, cur
    if t_cur[0] > t_ref[0] or t_cur[-1] < t_ref[-1]:
        raise ValueError("Current result does not cover the reference time range")
    cur2 = {"TIME": t_ref}
    for k, v in cur.items():
        if k == "TIME":
            continue
        cur2[k] = np.interp(t_ref, t_cur, v)
    return ref, cur2


def compare(run_a, run_b, atol=ATOL, rtol=RTOL):
    """比较两次运行。run_a 作参考。返回报告 dict（可直接进 manifest）。"""
    a, b = load_csv(_csv_of(run_a)), load_csv(_csv_of(run_b))
    channels_a, channels_b = set(a) - {"TIME"}, set(b) - {"TIME"}
    if channels_a != channels_b:
        raise ValueError("Channel sets differ; missing in current=%s; extra in current=%s" %
                         (sorted(channels_a - channels_b), sorted(channels_b - channels_a)))
    aligned = not np.array_equal(a["TIME"], b["TIME"])
    exact = all(a[name].tobytes() == b[name].tobytes() for name in a)
    a, b = _align(a, b)
    rows, verdict = [], "bit_exact" if exact else "numerical"
    for name in a:
        if name == "TIME":
            continue
        va, vb = a[name], b[name]
        d = np.abs(va - vb)
        mx = float(np.max(d)) if d.size else 0.0
        scale = float(np.max(np.abs(va))) if va.size else 0.0
        tol = atol + rtol * scale
        if mx > tol:
            verdict = "changed"
        i = int(np.argmax(d)) if d.size else 0
        rows.append({
            "channel": name, "max_abs_diff": mx,
            "rel": (mx / scale) if scale else None,
            "at_t": float(a["TIME"][i]) if d.size else None,
            "changed": bool(mx > tol),
        })
    rows.sort(key=lambda r: -r["max_abs_diff"])
    return {
        "ok": verdict != "changed",
        "verdict": verdict,
        "a": os.path.abspath(run_a), "b": os.path.abspath(run_b),
        "channels": len(rows),
        "time_alignment": "linear_interpolation" if aligned else "original_samples",
        "changed": [r["channel"] for r in rows if r["changed"]],
        "top": rows[:8],
    }


# ------------------------------------------------------------------ 基线
def baseline_dir(case_id):
    d = child_path(path_of("baseline"), case_id)
    os.makedirs(d, exist_ok=True)
    return d


def save(case_id, tag, run_root=None):
    """把 runs/<case>/<tag> 冻结为基线（复制 CSV + manifest 摘要）。"""
    run_dir = child_path(run_root or path_of("runs"), case_id, tag)
    if not os.path.isdir(run_dir):
        raise FileNotFoundError(run_dir)
    csv = _csv_of(run_dir)
    d = baseline_dir(case_id)
    shutil.copy2(csv, os.path.join(d, "baseline.csv"))
    mf = manifest.read(run_dir) or {}
    meta = {
        "case_id": case_id, "from_run": os.path.abspath(run_dir),
        "pscx_sha1": (mf.get("case") or {}).get("pscx_sha1"),
        "changed": (mf.get("case") or {}).get("changed"),
        "env": mf.get("env"),
        "saved": mf.get("ts"),
    }
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return {"baseline": d, "csv": os.path.join(d, "baseline.csv"), "meta": meta}


def diff(case_id, tag=None, run_root=None):
    """runs/<case>/<tag> 与基线比较；tag 为空则取最新一次运行。"""
    d = baseline_dir(case_id)
    base_csv = os.path.join(d, "baseline.csv")
    if not os.path.isfile(base_csv):
        raise FileNotFoundError("还没有基线：%s（先跑 baseline save）" % base_csv)
    root = child_path(run_root or path_of("runs"), case_id)
    if tag is None:
        tags = [t for t in os.listdir(root) if os.path.isdir(os.path.join(root, t))]
        tag = sorted(tags)[-1]
    run_dir = child_path(root, tag)
    a = os.path.join(d, "_baseline_tmp")           # 用 baseline.csv 冒充一次 run
    os.makedirs(a, exist_ok=True)
    shutil.copy2(base_csv, os.path.join(a, "channels.csv"))
    rep = compare(a, run_dir)
    rep["baseline"] = base_csv
    rep["run"] = os.path.abspath(run_dir)
    return rep


def write_report(rep, path):
    """把差分结果写成一页 CSV + 一页 Markdown 摘要。"""
    import csv as _csv
    rows = [rep[k] for k in ("verdict", "channels", "changed")]
    with open(path + ".csv", "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["channel", "max_abs_diff", "rel", "at_t", "changed"])
        for r in rep["top"]:
            w.writerow([reader.csv_headers([r["channel"]])[0], "%.12g" % r["max_abs_diff"],
                        "" if r["rel"] is None else "%.3g" % r["rel"],
                        r["at_t"], r["changed"]])
    lines = [
        "# 差分报告",
        "",
        "- 结论：**%s**" % rep["verdict"],
        "- 通道数：%d，变化通道：%s" % (rep["channels"], ", ".join(rep["changed"]) or "无"),
        "- 参考：%s" % rep.get("baseline", rep.get("a")),
        "- 对比：%s" % rep.get("run", rep.get("b")),
        "",
        "| 通道 | max|Δ| | 相对 | 发生时刻 | 判定 |",
        "|---|---|---|---|---|",
    ]
    for r in rep["top"]:
        lines.append("| %s | %.6g | %s | %s | %s |"
                     % (r["channel"], r["max_abs_diff"],
                        "n/a" if r["rel"] is None else "%.3g" % r["rel"], r["at_t"],
                        "变化" if r["changed"] else "一致"))
    with open(path + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path + ".md", path + ".csv", rows


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "save":
        print(json.dumps(save(sys.argv[2], sys.argv[3]), ensure_ascii=False, indent=1))
    elif len(sys.argv) >= 2:
        print(json.dumps(diff(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None),
                         ensure_ascii=False, indent=1))
