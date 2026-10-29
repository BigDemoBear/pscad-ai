# -*- coding: utf-8 -*-
"""manifest.py — 每次运行的档案：改了什么、用的什么版本、耗时、结论、证据在哪。

原则：没有 manifest 的结果一律视为不可复现，不作为交付。
"""
import json
import os
import platform
import time

from .config import CONFIG, load_config, path_of, child_path


def new_run_dir(case_id, tag=None):
    tag = tag or time.strftime("%Y%m%d-%H%M%S")
    d = child_path(path_of("runs"), case_id, tag)
    os.makedirs(d, exist_ok=True)
    return d


def write(run_dir, payload):
    """写 manifest.json，返回路径。payload 里 None 字段自动略过。"""
    cfg = CONFIG or load_config()
    rec = {
        "schema": "pscad_ai/manifest/1",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "env": {
            "pscad": cfg["pscad"]["exe"],
            "fortran": cfg["pscad"]["fortran"],
            "python": platform.python_version(),
            "host": platform.node(),
        },
    }
    for k, v in payload.items():
        if v is not None:
            rec[k] = v
    p = os.path.join(run_dir, "manifest.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return p


def read(run_dir):
    p = os.path.join(run_dir, "manifest.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)
