# -*- coding: utf-8 -*-
"""Monitor progress and output activity around blocking automation calls.

Native messages/build/run calls can block before an outer timeout is checked.
The guard observes explicit progress touches and watched-file activity. If both
remain idle beyond the configured limit, it terminates the owned PSCAD process
and disconnects its socket. The caller must open a new session after a stall.
"""
import os
import threading
import time

__all__ = ["ActivityGuard"]


class ActivityGuard:
    def __init__(self, transport, idle=180.0, watch=(), interval=5.0, on_stall=None):
        self.transport = transport
        self.idle = float(idle)
        # 检查间隔必须明显小于 idle，否则第一次检查就已经太晚（idle 很小时尤其明显）。
        self.interval = max(0.2, min(float(interval), self.idle / 4.0))
        self.watch = [str(p) for p in watch if p]
        self.on_stall = on_stall
        self.fired = False
        self.reason = None
        self.killed_pid = None
        self.socket_closed = None
        self._beat = time.monotonic()
        self._fp = None
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 心跳
    def touch(self, why=None):
        """外部报告"还在动"。why 只用于诊断，不参与判断。"""
        self._beat = time.monotonic()

    # ------------------------------------------------------------ 文件指纹
    def _fingerprint(self):
        items = []
        for root in self.watch:
            if not os.path.isdir(root):
                continue
            for base, _dirs, files in os.walk(root):
                for name in files:
                    try:
                        st = os.stat(os.path.join(base, name))
                    except OSError:
                        continue
                    items.append((base, name, st.st_mtime_ns, st.st_size))
        return tuple(sorted(items))

    # ---------------------------------------------------------------- 判定
    def _check(self):
        with self._lock:
            if self.fired:
                return
            now = time.monotonic()
            fp = self._fingerprint()
            if fp != self._fp:
                self._fp = fp
                self._beat = now
            silent = now - self._beat
            if silent <= self.idle:
                return
            self.fired = True
            pid = getattr(self.transport, "_pid", None)
            self.killed_pid = pid
            self.reason = ("no message and no file activity for %.0f s (idle limit %.0f s)"
                           % (silent, self.idle))
            try:
                # 优先用 force_disconnect：只 kill 进程时 socket 会半开，
                # 阻塞的recv线程可能仍等待半开的socket。
                force = getattr(self.transport, "force_disconnect", None)
                if callable(force):
                    self.socket_closed = bool(force())
                else:
                    self.transport.kill()
            except Exception as exc:                       # noqa: BLE001
                self.reason += "; disconnect failed: %s: %s" % (type(exc).__name__, exc)
            if self.on_stall:
                try:
                    self.on_stall(self.reason)
                except Exception:                          # noqa: BLE001
                    pass

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self._check()
            except Exception:                              # noqa: BLE001
                # 守护线程自己不能死，否则阻塞仍在
                pass

    # ------------------------------------------------------------ 生命周期
    def start(self):
        self._fp = self._fingerprint()
        self._beat = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="pscad-activity-guard")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 2)
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    # ---------------------------------------------------------------- 报告
    def as_dict(self):
        return {"stalled": self.fired, "stall_reason": self.reason,
                "killed_pid": self.killed_pid, "socket_closed": self.socket_closed,
                "idle_limit_s": self.idle, "watched": self.watch}
