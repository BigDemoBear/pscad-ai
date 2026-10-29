"""One execution path for CLI and AI clients. Native numeric files are the deliverable."""
import json
from pathlib import Path
import shutil
import time

from . import case, manifest, reader
from .config import path_of
from .psxmodel import Psx
from .runner import Runner
from .transport import Transport
from .validator import validate_psx
from .canvas import fit_file as fit_canvas


def execute(case_id, tag=None, timeout=None, snapshot=False,
            transport=None, progress=None, cancelled=None, build_timeout=None):
    """一次完整运行：载入 → build → run → 收集数值 → 写 manifest。

    参数顺序兼容既有调用方（tasks.py 按位置传前 4 个），新增的 build_timeout
    放在末尾，且必须用关键字传。
    """
    directory, pscx = case.pscx_of(case_id)
    canvas = fit_canvas(pscx)
    project = Psx(pscx)
    run_dir = Path(manifest.new_run_dir(case_id, tag))
    if (run_dir / "manifest.json").exists():
        raise FileExistsError("Run tag already used: %s" % run_dir)
    started = time.time()
    # Palette geometry covers a subset of master symbols. Projects with refs
    # use native compiler diagnostics for electrical validation.
    if (Path(directory) / "refs.json").exists():
        pre = {"ok": project.structure()["instances"] > 0,
               "scope": "nonempty XML model; electrical checks delegated to PSCAD and executing AI",
               "summary": "Native project; no universal topology assertion"}
    else:
        pre = validate_psx(pscx)
    result = {"ok": False, "build": None, "run": None}
    errors = []
    numeric = None
    if pre["ok"]:
        owned_transport = transport is None
        transport = transport or Transport(audit=str(Path(path_of("logs")) / "transport.jsonl"))
        try:
            plan_path = Path(directory) / "plan.json"
            libraries = json.loads(plan_path.read_text(encoding="utf-8")).get("libraries") if plan_path.exists() else None
            handle = transport.load(str(Path(pscx).resolve()), libraries=libraries)
            runner = Runner(transport, progress=progress, cancelled=cancelled)
            result = runner.build_and_run(handle, directory, project.name,
                                          build_timeout=build_timeout, run_timeout=timeout)
            if snapshot:
                from .vision import snapshot_project
                snapshot_project(handle, str(run_dir / "canvas.png"), expected_pid=transport._pid)
        except Exception as error:
            errors.append("%s: %s" % (type(error).__name__, error))
        finally:
            if owned_transport:
                transport.close()
    else:
        errors.append("Pre-run validation failed")
    if result["ok"]:
        try:
            base = str(Path(result["run"]["inf"]).with_suffix(""))
            numeric = reader.read_out(base)
            for path in [base + ".inf", *numeric["files"]]:
                shutil.copy2(path, run_dir / Path(path).name)
            import csv
            with open(run_dir / "channels.csv", "w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(reader.csv_headers(["TIME", *numeric["data"]]))
                writer.writerows(zip(numeric["time"], *numeric["data"].values()))
        except Exception as error:
            errors.append("%s: %s" % (type(error).__name__, error))
            result["ok"] = False
    shutil.copy2(pscx, run_dir / Path(pscx).name)
    if not result["ok"] and (result.get("run") or {}).get("output_base"):
        # Retain partial numeric evidence without declaring it a complete simulation.
        partial = Path(result["run"]["output_base"])
        for file in [partial.with_suffix(".inf"), *partial.parent.glob(partial.name + "_*.out")]:
            if file.exists():
                shutil.copy2(file, run_dir / file.name)
    for name in ("plan.json", "refs.json", "edits.jsonl"):
        path = Path(directory) / name
        if path.exists():
            shutil.copy2(path, run_dir / name)
    payload = {"case": {"case_id": case_id, "pscx": str(run_dir / Path(pscx).name)},
               "result": result, "validation": {"pre": pre}, "errors": errors,
               "task_satisfied": None, "canvas": canvas,
               # 超时与卡死必须留痕：请求值与实际生效值分开记，不一致才查得出来。
               "timeouts": {"run_requested_s": timeout,
                            "build_requested_s": build_timeout,
                            "build_used_s": (result.get("build") or {}).get("timeout_used_s"),
                            "run_used_s": (result.get("run") or {}).get("timeout_used_s")},
               "stalled": bool((result.get("build") or {}).get("stalled")
                               or (result.get("run") or {}).get("stalled")),
               "stall_reason": (result.get("build") or {}).get("stall_reason")
                               or (result.get("run") or {}).get("stall_reason"),
               "total_sec": round(time.time() - started, 2)}
    if not result["ok"]:
        payload["numeric_evidence"] = "Any retained output is diagnostic only; completeness/current-run provenance is not established"
    if numeric:
        payload["data"] = {"base": str(run_dir / Path(base).name),
                           "points": numeric["points"], "channels": numeric["channels"],
                           "t_range": [float(numeric["time"][0]), float(numeric["time"][-1])],
                           "files": [Path(p).name for p in numeric["files"]]}
    manifest.write(str(run_dir), payload)
    return {"ok": bool(result["ok"] and not errors), "run_dir": str(run_dir), **payload}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("--tag")
    parser.add_argument("--timeout", type=int, default=None,
                        help="运行超时秒数；不传则用 config.run.run_timeout")
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args()
    from .tasks import request
    result = request("submit", {"op": "run", "case_id": args.case_id, "tag": args.tag,
                                "timeout": args.timeout, "snapshot": args.snapshot})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
