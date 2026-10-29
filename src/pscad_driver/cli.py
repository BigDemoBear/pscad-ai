# -*- coding: utf-8 -*-
"""cli.py — 供用户和 AI 调用的命令行入口。

  python -m pscad_driver.cli doctor                      # 环境自检
  python -m pscad_driver.cli palette                     # 生成调色板（离线）
  python -m pscad_driver.cli params                      # 生成参数索引（离线）
  python -m pscad_driver.cli templates                   # 列出模板
  python -m pscad_driver.cli case <template> <case_id>   # 复制模板 → 工况
  python -m pscad_driver.cli patch <case_id> K=V ...     # 改参数（离线）
  python -m pscad_driver.cli validate <case_id|pscx>     # 五级校验（前四级离线）
  python -m pscad_driver.cli run <case_id> [--tag T] [--set K=V ...] [--snapshot]
  python -m pscad_driver.cli read <run_dir>              # 读结果 + 出 CSV
  python -m pscad_driver.cli assemble <case_id> <plan.json>
"""
import argparse
import json
import os
import sys
import time

from . import case as casemod
from . import geometry, manifest, palette as palmod, params as paramsmod, reader
from .config import load_config, path_of
from .psxmodel import Psx, roundtrip
from .validator import validate_psx


def _p(msg):
    print(msg, flush=True)


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


def _case_pscx(case_id):
    return casemod.pscx_of(case_id)


# ---------------------------------------------------------------- 子命令
def cmd_doctor(a):
    cfg = load_config()
    info = {"config": {k: cfg[k] for k in ("pscad", "grid")}}
    info["pscad_exe"] = os.path.isfile(cfg["pscad"]["exe"])
    try:
        import mhrc.automation  # noqa: F401
        info["mhrc_modules"] = sorted(m for m in dir(mhrc.automation) if not m.startswith("_"))[:6]
    except Exception as e:
        info["mhrc"] = "FAIL %s" % e
    for m in ("numpy", "matplotlib", "PIL", "yaml"):
        try:
            __import__(m)
            info[m] = "ok"
        except Exception as e:
            info[m] = "missing (%s)" % e
    assets = path_of("assets")
    for f in ("palette.json", "params_index.json"):
        info[f] = os.path.isfile(os.path.join(assets, f))
    info["templates"] = [t["name"] for t in casemod.list_templates()]
    _p(json.dumps(info, ensure_ascii=False, indent=1))


def cmd_palette(a):
    pal, idx, files = palmod.build()
    _p("定义 %d 个，端口 %d 个" % (pal["count"], idx["port_count"]))
    _p("产物：%s" % (files,))


def cmd_params(a):
    r = paramsmod.build()
    _p("扫到元件类型 %d 个 → %s" % (r["defs"], r["path"]))


def cmd_templates(a):
    for t in casemod.list_templates():
        _p("%-10s %6.1f KB  %s" % (t["name"], t["kb"], t["desc"] or t["pscx"]))


def cmd_case(a):
    from .tasks import request
    _p(_dumps(request("submit", {"op": "case_new", "template": a.template, "case_id": a.case_id, "force": a.force})))


def _targeted_sets(sets):
    changes = {}
    for kv in sets:
        k, _, v = kv.partition("=")
        if "." not in k:
            raise ValueError("Specify ref.parameter=value; global same-name parameter edits are no longer supported")
        ref, key = k.rsplit(".", 1)
        changes.setdefault(ref, {})[key] = v
    return changes


def cmd_patch(a):
    from .tasks import request
    changes = _targeted_sets(a.sets)
    result = request("submit", {"op": "edit", "case_id": a.case_id, "changes": changes})
    _p(_dumps(result))
    return 0


def cmd_validate(a):
    target = a.target
    if os.path.isfile(target):
        pscx = target
    else:
        _, pscx = _case_pscx(target)
    rep = validate_psx(pscx)
    _p(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep["ok"] else 2


def cmd_roundtrip(a):
    src = a.pscx or _case_pscx(a.case_id)[1]
    dst = os.path.join(os.path.dirname(src), "_roundtrip.pscx")
    _p(json.dumps(roundtrip(src, dst), ensure_ascii=False, indent=1))


def cmd_run(a):
    """Run through the shared numeric-output workflow."""
    from .tasks import request
    result = request("submit", {"op": "run", "case_id": a.case_id, "tag": a.tag,
                                "timeout": a.timeout, "snapshot": a.snapshot,
                                "changes": _targeted_sets(a.sets)})
    _p(_dumps(result))
    return 0


def cmd_read(a):
    r = reader.read_out(a.base)
    csv = reader.to_csv(a.base, os.path.join(os.path.dirname(a.base), "channels.csv"))
    _p(json.dumps({"channels": len(r["channels"]), "points": r["points"],
                   "t_range": [float(r["time"][0]), float(r["time"][-1])],
                   "files": [os.path.basename(p) for p in r["files"]],
                   "csv": csv}, ensure_ascii=False, indent=1))


def cmd_baseline(a):
    from . import baseline as bl
    if a.action == "save":
        out = bl.save(a.case_id, a.tag)
    else:
        out = bl.diff(a.case_id, a.tag)
        if a.report:
            md, csv, _ = bl.write_report(out, a.report)
            out["report"] = {"md": md, "csv": csv}
    _p(_dumps(out))
    return 0 if a.action == "save" or out["ok"] else 1


def cmd_diff(a):
    from . import baseline as bl
    rep = bl.compare(a.a, a.b)
    if a.report:
        md, csv, _ = bl.write_report(rep, a.report)
        rep["report"] = {"md": md, "csv": csv}
    _p(_dumps(rep))
    return 0 if rep["ok"] else 1


def cmd_assemble(a):
    from .tasks import request
    from pathlib import Path
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8-sig"))
    ops = []
    for i, component in enumerate(plan["components"]):
        item = {"op": "add", "x": 180 + (i % 5) * 216, "y": 180 + (i // 5) * 180, **component}
        ops.append(item)
    for connection in plan.get("connections", []):
        ends = connection if isinstance(connection, dict) else {"from": connection[0], "to": connection[1]}
        ops.append({"op": "connect", **ends})
    _p(_dumps(request("submit", {"op": "edit", "case_id": a.case_id, "ops": ops})))
    return 0


# ---------------------------------------------------------------- 入口
def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("task", "data", "knowledge"):
        module = __import__("pscad_driver." + {"task": "tasks"}.get(args[0], args[0]), fromlist=["main"])
        old_argv = sys.argv
        try:
            sys.argv = [old_argv[0], *args[1:]]
            return module.main() or 0
        finally:
            sys.argv = old_argv
    ap = argparse.ArgumentParser(prog="pscad_driver")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("palette").set_defaults(fn=cmd_palette)
    sub.add_parser("params").set_defaults(fn=cmd_params)
    sub.add_parser("templates").set_defaults(fn=cmd_templates)

    p = sub.add_parser("case"); p.add_argument("template"); p.add_argument("case_id")
    p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_case)

    p = sub.add_parser("patch"); p.add_argument("case_id"); p.add_argument("sets", nargs="+")
    p.set_defaults(fn=cmd_patch)

    p = sub.add_parser("validate"); p.add_argument("target"); p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("roundtrip"); p.add_argument("case_id", nargs="?"); p.add_argument("--pscx")
    p.set_defaults(fn=cmd_roundtrip)

    p = sub.add_parser("run"); p.add_argument("case_id")
    p.add_argument("--tag"); p.add_argument("--set", dest="sets", action="append", default=[])
    p.add_argument("--timeout", type=int, default=None)
    shots = p.add_mutually_exclusive_group()
    shots.add_argument("--snapshot", action="store_true", help="Request a source-verified canvas image")
    shots.add_argument("--no-snap", action="store_true", help="Compatibility option; snapshots are disabled by default")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("read"); p.add_argument("base"); p.set_defaults(fn=cmd_read)

    p = sub.add_parser("baseline"); p.add_argument("action", choices=["save", "diff"])
    p.add_argument("case_id"); p.add_argument("tag", nargs="?"); p.add_argument("--report")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("diff"); p.add_argument("a"); p.add_argument("b"); p.add_argument("--report")
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("assemble"); p.add_argument("case_id"); p.add_argument("plan")
    p.set_defaults(fn=cmd_assemble)

    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
