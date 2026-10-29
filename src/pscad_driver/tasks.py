"""One local worker owns PSCAD; CLI and MCP share its tasks and saved checkpoints."""
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import secrets
import tempfile
import threading
import time
import traceback
import uuid

from .config import path_of, load_config

HOST, PORT = "127.0.0.1", int(load_config()["service"]["port"])
TERMINAL = {"succeeded", "failed", "stopped", "interrupted"}


class TaskStore:
    def __init__(self, directory=None, dispatch=None):
        self.directory = Path(directory or (Path(path_of("home")) / "tasks"))
        self.directory.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.tasks = {}
        self.queue = queue.Queue()
        self.transport = None
        self.dispatch = dispatch or self._dispatch
        for file in self.directory.glob("*.json"):
            item = json.loads(file.read_text(encoding="utf-8"))
            self.tasks[item["id"]] = item
            if item["status"] not in TERMINAL | {"paused"}:
                item.update(status="interrupted", phase="worker_restarted",
                            error="Worker exited before completion; batch resume is explicit.")
                self._save(item)

    def _save(self, item):
        item["updated"] = time.time()
        file = self.directory / (item["id"] + ".json")
        temporary = file.with_suffix(".tmp")
        temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(file)

    def update(self, tid, **fields):
        with self.changed:
            self.tasks[tid].update(fields)
            self._save(self.tasks[tid])
            self.changed.notify_all()

    def submit(self, spec):
        if spec.get("op") not in {"connect", "close", "create", "edit", "inspect", "run", "build", "batch", "snapshot", "messages", "case_new"}:
            raise ValueError("Unknown task op: %s" % spec.get("op"))
        if spec["op"] == "batch" and (not isinstance(spec.get("steps"), list) or not spec["steps"]):
            raise ValueError("A batch requires a nonempty explicit steps list")
        tid = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        item = {"id": tid, "spec": deepcopy(spec), "status": "queued", "phase": "queued",
                "created": time.time(), "stop_requested": False, "pause_requested": False,
                "completed_steps": [], "attempts": {}, "result": None}
        with self.lock:
            self.tasks[tid] = item
            self._save(item)
            self.queue.put(tid)
        return self.status(tid)

    def status(self, tid=None, include_result=True):
        with self.lock:
            if tid is None:
                return [{k: t.get(k) for k in ("id", "status", "phase", "created", "updated", "step", "total_steps")}
                        for t in sorted(self.tasks.values(), key=lambda t: t["created"], reverse=True)]
            result = deepcopy(self.tasks[tid])
            result.pop("spec", None)
            if not include_result:
                result.pop("result", None)
                result.pop("completed_steps", None)
            return result

    def wait(self, tid, seconds=30):
        deadline = time.monotonic() + min(max(float(seconds), 0), 55)
        with self.changed:
            while self.tasks[tid]["status"] not in TERMINAL | {"paused"}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.changed.wait(remaining)
            result = self.status(tid)
            result["observation_timed_out"] = result["status"] not in TERMINAL | {"paused"}
            return result

    def control(self, tid, action):
        with self.lock:
            t = self.tasks[tid]
            if action == "stop":
                if t["status"] == "running" and t["spec"]["op"] not in {"run", "build", "batch"}:
                    raise ValueError("This model operation is not interruptible; wait for it to complete")
                if t["status"] not in TERMINAL:
                    self.update(tid, stop_requested=True)
                    if t["status"] in {"queued", "paused"}:
                        self.update(tid, status="stopped", phase="stopped")
            elif action == "pause":
                if t["spec"]["op"] != "batch":
                    raise ValueError("Pause stops dispatch between batch steps; use stop for a single simulation")
                if t["status"] in {"running", "queued"}:
                    self.update(tid, pause_requested=True)
            elif action == "resume":
                if t["spec"]["op"] != "batch" or t["status"] not in {"paused", "interrupted", "failed", "stopped"}:
                    raise ValueError("Only a paused/interrupted/failed/stopped batch can resume")
                self.update(tid, status="queued", phase="queued", stop_requested=False, pause_requested=False, error=None)
                self.queue.put(tid)
            else:
                raise ValueError("action must be pause, resume or stop")
            return self.status(tid)

    def work(self):
        while True:
            tid = self.queue.get()
            try:
                self.process(tid)
            finally:
                self.queue.task_done()

    def process(self, tid):
        t = self.tasks[tid]
        if t["status"] != "queued":
            return
        self.update(tid, status="running", phase="starting")
        progress = lambda **fields: self.update(tid, **fields)
        cancelled = lambda: self.tasks[tid]["stop_requested"]
        try:
            spec = t["spec"]
            if spec["op"] == "batch":
                steps = spec["steps"]
                self.update(tid, total_steps=len(steps))
                for index, step in enumerate(steps):
                    if any(r["index"] == index for r in t["completed_steps"]):
                        continue
                    if cancelled():
                        self.update(tid, status="stopped", phase="stopped")
                        return
                    if t["pause_requested"]:
                        self.update(tid, status="paused", phase="between_steps")
                        return
                    step = deepcopy(step)
                    if step.get("op", "run") != "run":
                        raise ValueError("Batch steps must be run operations; model corrections belong to the executing AI")
                    step["op"] = "run"
                    step.setdefault("case_id", spec.get("case_id"))
                    attempt = t["attempts"].get(str(index), 0) + 1
                    t["attempts"][str(index)] = attempt
                    step.setdefault("tag", "%s_s%04d_a%d" % (tid, index, attempt))
                    self.update(tid, step=index + 1, phase="dispatching")
                    result = self.dispatch(step, progress, cancelled)
                    if cancelled() and not result.get("ok", True):
                        self.update(tid, status="stopped", phase="stopped", last_result=result)
                        return
                    if not result.get("ok", True):
                        self.update(tid, status="failed", phase="step_failed", last_result=result)
                        return
                    t["completed_steps"].append({"index": index, "result": result})
                    self.update(tid, completed_count=len(t["completed_steps"]))
                result = {"ok": True, "steps": len(steps), "completed": len(t["completed_steps"])}
            else:
                if spec["op"] == "run" and not spec.get("tag"):
                    spec["tag"] = tid
                result = self.dispatch(spec, progress, cancelled)
            state = "stopped" if cancelled() and not result.get("ok", True) else ("succeeded" if result.get("ok", True) else "failed")
            self.update(tid, status=state, phase=state, result=result)
        except Exception as error:
            self.update(tid, status="failed", phase="exception", error="%s: %s" % (type(error).__name__, error),
                        traceback=traceback.format_exc())

    def _dispatch(self, spec, progress, cancelled):
        from .transport import Transport
        from . import author, editor, case, workflow
        from .runner import Runner
        if self.transport is None:
            self.transport = Transport(audit=str(Path(path_of("logs")) / "worker_transport.jsonl"))
        transport = self.transport
        op = spec["op"]
        cid = spec.get("case_id")
        if op == "connect":
            transport.connect()
            return {"ok": True, "pid": transport._pid, "background": True}
        if op == "close":
            transport.close()
            return {"ok": True}
        if op == "create":
            return author.compose(cid, spec["plan"], spec.get("replace", False), transport=transport)
        if op == "edit":
            return editor.edit(cid, spec.get("ops"), spec.get("changes"), spec.get("settings"), spec.get("page", "Main"), transport)
        if op == "inspect":
            return editor.inspect(cid, spec.get("page"), spec.get("keyword", ""), spec.get("offset", 0), spec.get("limit", 100))
        if op == "case_new":
            transport.reset()
            directory, path = case.new(spec["template"], cid, spec.get("force", False))
            return {"ok": True, "directory": directory, "pscx": path}
        if op == "run":
            if spec.get("changes") or spec.get("settings"):
                editor.edit(cid, changes=spec.get("changes"), settings=spec.get("settings"),
                            page=spec.get("page", "Main"), transport=transport)
            # timeout 不写死默认值：交给 Runner 用 config.run.run_timeout，
            # 避免任务服务与直接调用两条路的默认超时不一致。
            result = workflow.execute(cid, spec.get("tag"), spec.get("timeout"), spec.get("snapshot", False),
                                      transport, progress, cancelled)
            result["session_pid"] = transport._pid
            return result
        directory, path = case.pscx_of(cid)
        plan_path = Path(directory) / "plan.json"
        libraries = json.loads(plan_path.read_text(encoding="utf-8")).get("libraries") if plan_path.exists() else None
        project = transport.load(path, libraries=libraries)
        runner = Runner(transport, progress=progress, cancelled=cancelled)
        if op == "build":
            return runner.build(project, directory, Path(path).stem, spec.get("timeout"), spec.get("clean", False))
        if op == "messages":
            return {"messages": runner.messages(project, spec.get("level"))}
        if op == "snapshot":
            from .vision import snapshot_project
            path = spec.get("path") or str(Path(directory) / "canvas.png")
            image, size = snapshot_project(project, path, page=spec.get("page", "Main"), expected_pid=transport._pid)
            return {"path": image, "size": size}
        raise ValueError("Unknown operation: %s" % op)


def _token_path():
    return Path(os.environ['LOCALAPPDATA']) / 'pscad-ai' / ('worker-%d.token' % PORT)


def _worker_token():
    """Publish a complete random token atomically inside the current user's local profile."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd, temporary = tempfile.mkstemp(prefix='token-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(secrets.token_urlsafe(32))
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass  # Another client published the shared token first.
        finally:
            Path(temporary).unlink()
    token = path.read_text(encoding='ascii').strip()
    if len(token) != 43:
        raise ValueError('Invalid worker token file: %s' % path)
    return token


def serve():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    token = _worker_token()
    # Bind before touching saved tasks so simultaneous starts cannot mark live work interrupted.
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                authorization = self.headers.get('Authorization', '')
                if not secrets.compare_digest(authorization.encode('utf-8'), ('Bearer ' + token).encode('ascii')):
                    self.send_error(401, 'Worker authentication required')
                    return
                if self.headers.get('Origin') is not None or self.headers.get('Host') != '%s:%s' % (HOST, PORT):
                    self.send_error(403, 'Cross-origin requests are not supported')
                    return
                if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                    self.send_error(415, 'Use application/json')
                    return
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method = self.path.lstrip("/")
                if method == "health":
                    result = {"service": "pscad-4.6.2-worker", "pid": __import__("os").getpid()}
                elif method == "submit":
                    result = store.submit(request)
                elif method == "status":
                    result = store.status(**request)
                elif method == "wait":
                    result = store.wait(**request)
                elif method == "control":
                    result = store.control(**request)
                else:
                    raise ValueError("Unknown endpoint")
                payload = {"result": result}
                code = 200
            except Exception as error:
                payload, code = {"error": "%s: %s" % (type(error).__name__, error)}, 400
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    store = TaskStore()
    threading.Thread(target=store.work, daemon=True, name="pscad-owner").start()
    server.serve_forever()


def request(method, args=None, start=True):
    import subprocess
    import sys
    import urllib.request
    import urllib.error
    token = _worker_token()
    def send():
        body = json.dumps(args or {}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request("http://%s:%s/%s" % (HOST, PORT, method), data=body,
                                     headers={"Content-Type": "application/json", 'Authorization': 'Bearer ' + token})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.load(response)["result"]
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 415):
                raise RuntimeError('Worker rejected the request (HTTP %s); check the local token and service version' % error.code) from error
            raise RuntimeError(json.load(error)["error"]) from error
    try:
        return send()
    except urllib.error.URLError:
        if not start:
            raise
    log_path = Path(path_of("logs")) / "worker.log"
    with open(log_path, "ab") as log:
        subprocess.Popen([sys.executable, "-X", "utf8", "-m", "pscad_driver.tasks", "serve"],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            return send()
        except urllib.error.URLError:
            time.sleep(0.2)
    raise RuntimeError("Worker did not start; read %s" % log_path)


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("list")
    sub.add_parser("health")
    q = sub.add_parser("submit"); q.add_argument("file")
    q = sub.add_parser("status"); q.add_argument("id")
    q = sub.add_parser("wait"); q.add_argument("id"); q.add_argument("--seconds", type=float, default=30)
    q = sub.add_parser("control"); q.add_argument("id"); q.add_argument("action", choices=["pause", "resume", "stop"])
    a = p.parse_args()
    if a.command == "serve":
        return serve()
    method, args = a.command, {}
    if method == "submit":
        args = json.loads(Path(a.file).read_text(encoding="utf-8-sig"))
    elif method in {"status", "wait", "control"}:
        args = {"tid": a.id}
        if method == "wait":
            args["seconds"] = a.seconds
        if method == "control":
            args["action"] = a.action
    elif method == "list":
        method = "status"
    print(json.dumps(request(method, args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
