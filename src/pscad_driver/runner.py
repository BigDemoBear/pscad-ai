"""Native build/run with event-aware control and current, complete numeric output."""
from pathlib import Path
import math
import re
import time
import xml.etree.ElementTree as ET

from . import reader
from .guard import ActivityGuard

BUILD_DONE_RE = re.compile(r"Build:\s*(\d+)\s+errors?", re.I)

# 产物与工程源文件的 mtime 容差。Windows 上两者可能落在同一时间片，
# 只保留 2 ms 余量；超过这个余量说明“改了工程但没重新编译”。
ARTIFACT_TOLERANCE_NS = 2_000_000


def _source_mtime(case_dir):
    """工程源文件的最新 mtime（ns）。空目录返回 0。"""
    times = []
    for path in Path(case_dir).glob("*.pscx"):
        try:
            times.append(path.stat().st_mtime_ns)
        except OSError:
            pass
    return max(times) if times else 0


def _artifact_state(exe, src_mtime_ns):
    """产物可用性：返回 (可用?, 原因码)。原因码为空表示可用。

    只看“文件在不在”会漏掉最危险的一种：工程改过、编译却没真正重跑，
    旧 exe 仍躺在那儿。这里要求 exe 不旧于 .pscx。
    """
    if not exe.exists():
        return False, "missing-artifact"
    if src_mtime_ns and exe.stat().st_mtime_ns + ARTIFACT_TOLERANCE_NS < src_mtime_ns:
        return False, "stale-artifact"
    return True, None


def _messages(project):
    return [(m[0] or "", m[2] if len(m) > 2 else "") for m in project.messages()]


def errors_of(project):
    return [t for t, level in _messages(project) if str(level).lower() in ("error", "fatal")]


def _settings(case_dir, ns):
    root = ET.parse(Path(case_dir) / (ns + ".pscx")).getroot()
    return {p.get("name"): p.get("value") for p in root.findall("paramlist[@name='Settings']/param")}


class _RunMonitor:
    """mhrc.run waits inside its consumer loop. Handle control there, on the same thread."""
    def __init__(self, controller, cancelled, deadline, tick, poll):
        from mhrc.automation.handler import BuildEvent
        self.events = BuildEvent()
        self.controller, self.cancelled, self.deadline = controller, cancelled, deadline
        self.tick, self.poll = tick, poll
        self.next_tick = 0
        self.next_stop = 0
        self.stop_sent = False
        self.timed_out = False

    def send(self, message):
        now = time.monotonic()
        if now >= self.next_tick:
            self.tick()
            self.next_tick = now + self.poll
        self.timed_out = self.timed_out or now >= self.deadline
        if (self.cancelled() or self.timed_out) and now >= self.next_stop:
            # Never invoke a response-waiting command from a message consumer.
            # The stop may first arrive during the run command's implicit build;
            # keep requesting it until the outer native operation ends.
            self.controller.command_id("ID_RIBBON_HOME_RUN_STOP")
            self.stop_sent = True
            self.next_stop = now + 2
        return self.events.send(message)


class Runner:
    def __init__(self, transport, cfg=None, progress=None, cancelled=None):
        self.t = transport
        self.cfg = cfg or transport.cfg
        self.build_timeout = self.cfg["run"]["build_timeout"]
        self.run_timeout = self.cfg["run"]["run_timeout"]
        self.poll = self.cfg["run"]["poll"]
        # 卡死判定：既无消息、也无文件活动，持续这么多秒就判 stalled 并强杀自有 PSCAD。
        self.stall_idle_build = float(self.cfg["run"].get("stall_idle_build", 180))
        self.stall_idle_run = float(self.cfg["run"].get("stall_idle_run", 300))
        self.progress = progress or (lambda **fields: None)
        self.cancelled = cancelled or (lambda: False)

    def build(self, prj, case_dir, ns, timeout=None, clean=False):
        if clean:
            prj.clean()
        t0 = time.time()
        gf46 = Path(case_dir) / (ns + ".gf46")
        exe = gf46 / (ns + ".exe")
        src = _source_mtime(case_dir)
        limit = timeout if timeout is not None else self.build_timeout
        # guard 必须最先启动：`_messages()` 取基线、`prj.build()` 提交都可能阻塞，
        guard = ActivityGuard(self.t, idle=self.stall_idle_build, watch=[gf46],
                              on_stall=lambda why: self.progress(phase="stalled", detail=why))
        guard.start()
        done_by, errs = None, []
        try:
            self.progress(phase="building", simulation_time=None)
            n0 = len(_messages(prj))
            before = exe.stat().st_mtime_ns if exe.exists() else None
            # Single-project workflows avoid project.focus(), invoked by project.build().
            if getattr(self.t, "loaded_projects", set()) == {ns}:
                self.t.connect().build_all()
            else:
                prj.build()
            while time.time() - t0 < limit:
                time.sleep(self.poll)
                messages = _messages(prj)[n0:]
                # 不在这里 touch：PSCAD 卡住时仍会周期性刷编译进度消息，
                # 编译器可能持续返回相同的进度消息；由ActivityGuard根据
                # 产物文件变化判断进展，不因轮询收到消息而重置计时。
                errs = [t for t, level in messages if str(level).lower() in ("error", "fatal")]
                if errs:
                    done_by = "error"
                    break
                summaries = [BUILD_DONE_RE.search(t) for t, _ in messages]
                summaries = [m for m in summaries if m]
                if summaries:
                    if int(summaries[-1].group(1)):
                        errs.append(summaries[-1].group(0))
                    done_by = "message"
                    break
                if exe.exists() and exe.stat().st_mtime_ns != before:
                    done_by = "artifact"
                    break
                if exe.exists() and any("Solve Time" in t or "Time for Compile" in t for t, _ in messages):
                    done_by = "unchanged-build-message"
                    break
        except Exception as exc:                       # noqa: BLE001
            # stalled 时守护已 kill+关 socket，这里把“socket 被打断”收成可读失败
            errs.append("%s: %s" % (type(exc).__name__, exc))
        finally:
            guard.stop()

        usable, code = _artifact_state(exe, src)
        if not usable and not errs:
            # 两种都写明：没有产物，或产物比工程还旧（改了没重编译）
            errs.append("%s: %s (exe=%s, source_mtime_ns=%s)" % (
                code, "no usable executable", exe, src))
        return {"ok": bool(done_by and not errs and usable), "stage": "build",
                "sec": round(time.time() - t0, 2),
                "exe": str(exe) if usable else None,
                "errors": errs, "done_by": done_by,
                "timed_out": done_by is None,
                "artifact": code or "fresh",
                "timeout_used_s": round(limit, 1),
                "stalled": guard.fired, "stall_reason": guard.reason}

    def run(self, prj, case_dir, ns, timeout=None):
        settings = _settings(case_dir, ns)
        duration = float(settings["time_duration"].split()[0])
        sample = float(settings["sample_step"].split()[0]) * 1e-6
        expected_last_sample = math.floor(duration / sample + 1e-9) * sample
        output_name = Path(settings.get("output_filename") or ns).stem
        base = Path(case_dir) / (ns + ".gf46") / output_name
        inf = base.with_suffix(".inf")
        limit = timeout if timeout is not None else self.run_timeout

        def signature():
            count = len(reader.read_inf(str(inf))) if inf.exists() else 0
            paths = [base.parent / (output_name + "_%02d.out" % i)
                     for i in range(1, (count + 9) // 10 + 1)]
            return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
                    for p in paths}

        before = signature()
        n0, t0 = len(_messages(prj)), time.time()
        deadline = time.monotonic() + limit

        def current_time():
            now = signature()
            if not inf.exists() or not now or any(sig is None or before.get(p) == sig for p, sig in now.items()):
                return None
            try:
                ends = []
                for filename in now:
                    with open(filename, "rb") as stream:
                        stream.seek(max(0, Path(filename).stat().st_size - 4096))
                        lines = stream.read().splitlines()
                    ends.append(float(lines[-1].split()[0]))
                last = min(ends)
                return last
            except (OSError, ValueError, IndexError):
                return None  # Files may have no complete flushed row yet.

        guard = ActivityGuard(self.t, idle=self.stall_idle_run, watch=[base.parent],
                              on_stall=lambda why: self.progress(phase="stalled", detail=why))

        mark = {"t": None}

        def tick():
            current = current_time()
            if current is not None and (mark["t"] is None or current > mark["t"]):
                mark["t"] = current
                guard.touch()          # 只有仿真时间前进才算进展
            self.progress(phase="stopping" if self.cancelled() else "running",
                          simulation_time=current, simulation_duration=duration)

        monitor = None
        complete, errs, reason, last = False, [], "No current output", None
        guard.start()
        try:
            tick()
            if getattr(prj, "_pscad", None) is not None:
                monitor = _RunMonitor(prj._pscad, self.cancelled, deadline, tick, self.poll)
                prj.run(consumer=monitor)
            else:
                prj.run()  # Test-only substitute projects have no native event socket.
            while True:
                last = current_time()
                complete = last is not None and last >= expected_last_sample - max(sample * 0.01, 1e-9)
                errs = [t for t, level in _messages(prj)[n0:] if str(level).lower() in ("error", "fatal")]
                if complete or errs:
                    break
                if self.cancelled() or time.monotonic() >= deadline:
                    if monitor is None or not monitor.stop_sent:
                        prj.stop()
                    reason = "%s; output ends at %s; requested duration is %s s" % (
                        "Explicit stop requested" if self.cancelled() else "Execution deadline exceeded", last, duration)
                    break
                reason = "Output ends at %s; requested duration is %s s" % (last, duration)
                time.sleep(self.poll)
        except Exception as exc:                       # noqa: BLE001
            # stalled 时守护已杀进程，这里把“socket 被打断”转成可读失败，别把信息丢掉
            errs.append("%s: %s" % (type(exc).__name__, exc))
        finally:
            guard.stop()
        cancelled = self.cancelled() and not complete
        timed_out = bool((monitor and monitor.timed_out) or (not complete and not cancelled and not errs))
        result = {"ok": complete and not errs, "stage": "run", "sec": round(time.time() - t0, 2),
                  "output_base": str(base),
                  "inf": str(inf) if complete else None, "errors": errs,
                  "cancelled": cancelled, "timed_out": timed_out,
                  "native_stop_sent": bool(monitor and monitor.stop_sent),
                  "requested_duration": duration, "expected_last_sample": expected_last_sample,
                  "last_output_time": last,
                  "timeout_used_s": round(limit, 1),
                  "stalled": guard.fired, "stall_reason": guard.reason}
        if timed_out:
            result["ok"] = False
        if complete:
            self.progress(phase="collecting", simulation_time=last, simulation_duration=duration)
        else:
            result["reason"] = reason
        return result

    def build_and_run(self, prj, case_dir, ns, build_timeout=None, run_timeout=None, clean=False):
        b = self.build(prj, case_dir, ns, timeout=build_timeout, clean=clean)
        if self.cancelled():
            return {"ok": False, "build": b, "run": None, "cancelled": True}
        r = self.run(prj, case_dir, ns, timeout=run_timeout) if b["ok"] else None
        return {"ok": bool(b["ok"] and r and r["ok"]), "build": b, "run": r}

    def messages(self, prj, level=None):
        messages = _messages(prj)
        return [m for m in messages if str(m[1]).lower() == level] if level else messages
