# -*- coding: utf-8 -*-
"""case.py — 工况管理：从模板复制工程并保存工况信息。

模板是**目录**：`templates/<名字>/` 里放一个 `.pscx` + `template.json`。
注意：`.pscx` 文件名必须与工程内部 `<project name=...>` 一致，否则 PSCAD 取不到句柄；
所以模板里的工程文件统一叫 `homework.pscx`，模板身份由目录名 + template.json 决定。
"""
import json
import os
import shutil
import time

from .config import path_of, child_path


def cases_dir():
    return path_of("cases")


def templates_dir():
    return path_of("templates")


def _read_meta(d):
    p = os.path.join(d, "template.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def list_templates():
    """列出模板：目录式（新）+ 直接放在模板根下的 .pscx（兼容旧布局）。"""
    root = templates_dir()
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isdir(d):
            pscx = [f for f in sorted(os.listdir(d)) if f.lower().endswith(".pscx")]
            if not pscx:
                continue
            meta = _read_meta(d)
            out.append({"name": name, "dir": d, "pscx": os.path.join(d, pscx[0]),
                        "desc": meta.get("desc", ""), "params": meta.get("params", {}),
                        "kb": round(os.path.getsize(os.path.join(d, pscx[0])) / 1024, 1)})
        elif name.lower().endswith(".pscx"):
            out.append({"name": os.path.splitext(name)[0], "dir": root, "pscx": d,
                        "desc": "", "params": {},
                        "kb": round(os.path.getsize(d) / 1024, 1)})
    return out


def find(template):
    """按名字或路径找模板，返回 (pscx 路径, meta)。"""
    if os.path.isfile(template):
        return template, {}
    for t in list_templates():
        if t["name"] == template:
            return t["pscx"], {"desc": t["desc"], "params": t["params"]}
    raise FileNotFoundError("模板不存在：%s（可用：%s）"
                            % (template, [t["name"] for t in list_templates()]))


def new(template, case_id, force=False):
    """复制模板到 cases/<case_id>/，返回 (case_dir, pscx)。"""
    case_dir = child_path(cases_dir(), case_id)
    src, meta = find(template)
    if os.path.isdir(case_dir) and not force:
        raise FileExistsError("工况已存在：%s（要覆盖请 force=True）" % case_dir)
    if force and os.path.isdir(case_dir):
        shutil.rmtree(case_dir)
    os.makedirs(case_dir, exist_ok=True)

    pscx = os.path.join(case_dir, os.path.basename(src))
    shutil.copy2(src, pscx)
    rec = {
        "case_id": case_id,
        "template": os.path.basename(os.path.dirname(src)) if meta else
                    os.path.splitext(os.path.basename(src))[0],
        "template_desc": meta.get("desc", ""),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return case_dir, pscx


def info(case_id):
    d = child_path(cases_dir(), case_id)
    p = os.path.join(d, "case.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def pscx_of(case_id):
    """工况目录里的工程文件（文件名保持模板原名）。"""
    d = child_path(cases_dir(), case_id)
    for f in sorted(os.listdir(d)):
        if f.lower().endswith(".pscx"):
            return d, os.path.join(d, f)
    raise FileNotFoundError("工况目录里没有 .pscx：%s" % d)
