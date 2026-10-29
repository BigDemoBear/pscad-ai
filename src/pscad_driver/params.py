# -*- coding: utf-8 -*-
"""params.py — 参数索引：元件到底有哪些参数、默认值、单位、可选值。

为什么需要它：`master.pslx` 的定义里只写了 `Description`，真正的参数名和默认值
只出现在**工程实例**上。所以做法是扫母本工程，按元件类型把参数收集起来。

产出 assets/params_index.json：
{
  "resistor": {
     "R": {"default": "1.0", "units": "", "values": ["1.0"], "count": 3},
     ...
  }
}
"""
import json
import os
import re

from .config import path_of

UNIT_RE = re.compile(r"^\s*([-+0-9.eE]+)\s*(?:\[([^\]]*)\])?\s*$")


def scan(paths):
    """扫若干 .pscx，收集 元件类型 → 参数 → 取值集合。"""
    from .psxmodel import Psx
    acc = {}
    for p in paths:
        try:
            psx = Psx(p)
        except Exception:
            continue
        for inst in psx.instances():
            d = inst["defn"]
            bucket = acc.setdefault(d, {})
            for k, v in inst["params"].items():
                b = bucket.setdefault(k, {"values": [], "count": 0})
                b["count"] += 1
                if v not in b["values"]:
                    b["values"].append(v)
    # 归一：抽单位、定默认值
    out = {}
    for d, params in acc.items():
        out[d] = {}
        for k, b in params.items():
            vals = b["values"]
            m = UNIT_RE.match(vals[0]) if vals else None
            units = (m.group(2) or "") if m else ""
            out[d][k] = {
                "default": vals[0] if vals else "",
                "units": units,
                "values": vals[:20],
                "distinct": len(vals),
                "count": b["count"],
            }
    return out


def build(template_dir=None, out_dir=None):
    template_dir = template_dir or path_of("templates")
    paths = []
    for ext in ("*.pscx", "*.psc"):
        for root, _dirs, files in os.walk(template_dir):
            paths += [os.path.join(root, f) for f in files if re.match(r".*\." + ext[-3:], f)]
    paths = sorted(set(paths))
    index = scan(paths)
    out_dir = out_dir or path_of("assets")
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "params_index.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"sources": paths, "count": len(index), "defs": index},
                  f, ensure_ascii=False, indent=1)
    return {"path": p, "defs": len(index), "sources": paths}


def load(assets_dir=None):
    p = os.path.join(assets_dir or path_of("assets"), "params_index.json")
    if not os.path.isfile(p):
        return {"defs": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def get(defn, assets_dir=None):
    return load(assets_dir)["defs"].get(defn.split(":")[-1], {})


if __name__ == "__main__":
    r = build()
    print("扫描:", r["sources"])
    print("元件类型:", r["defs"], "→", r["path"])
