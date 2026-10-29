# -*- coding: utf-8 -*-
"""transport.py — PSCAD会话、工程加载与进程生命周期管理。

会话约束：
  1. 进程内单例 + 全局锁：PSCAD 不接受并发命令。
  2. 启动后立刻设编译器全名，且本会话内不重启（该设置不持久化）。
  3. 不主动查询证书状态；授权由用户的PSCAD安装管理。
  4. 退出超时即强杀进程，不等官方超时。
"""
import json
import logging
import os
import subprocess
import threading
import time

from .config import CONFIG, load_config

log = logging.getLogger("pscad.transport")

_LOCK = threading.RLock()
_INSTANCE = None


class TransportError(RuntimeError):
    pass


class Transport:
    def __init__(self, cfg=None, audit=None):
        cfg = cfg or CONFIG or load_config()
        self.cfg = cfg
        self.exe = cfg["pscad"]["exe"]
        self.fortran = cfg["pscad"]["fortran"]
        self.audit = audit                      # 可选：logs/raw_calls.jsonl 路径
        self._pscad = None
        self._pid = None
        self.loaded_projects = set()
        self._loaded = {}

    # ---------------------------------------------------------------- 启动
    def connect(self):
        """返回 PSCAD 句柄（已设好编译器）。失败抛 TransportError。"""
        global _INSTANCE
        with _LOCK:
            if self._pscad is not None and self._alive():
                return self._pscad
            if not os.path.isfile(self.exe):
                raise TransportError("找不到 PSCAD：%s" % self.exe)

            from mhrc.automation.pscad import PSCAD     # 延迟导入，便于离线模块使用
            if self.cfg["pscad"].get("minimize", True):
                from .background import BackgroundPSCAD
                controller = BackgroundPSCAD
            else:
                controller = PSCAD
            t0 = time.time()
            p = controller(None, {
                "path": self.exe,
                "launch-minimized": bool(self.cfg["pscad"].get("minimize", True)),
                "/silence": bool(self.cfg["pscad"].get("silence", True)),
            })
            self._pscad = p
            self._pid = p._proc.pid
            # Blocking-call stalls are handled by the project ActivityGuard.
            self.loaded_projects.clear()
            self._loaded.clear()
            _INSTANCE = self
            got = p.settings().get("fortran_version")
            if got != self.fortran:
                # 必须用全名，且设完不能重启会话
                p.settings(fortran_version=self.fortran)
                got = p.settings().get("fortran_version")
            if got != self.fortran:
                raise TransportError("编译器切换失败：当前 %r，期望 %r" % (got, self.fortran))
            self._audit("connect", ok=True, sec=round(time.time() - t0, 2),
                        fortran=got, pid=self._pid, controller=type(p).__name__,
                        desktop=getattr(p, "_desktop_name", "current"))
            return p

    def _alive(self):
        try:
            return bool(self._pscad and self._pscad.is_alive())
        except Exception:
            return False

    # ---------------------------------------------------------------- 调用
    def call(self, fn, *a, **kw):
        """所有 PSCAD 调用都从这里过：串行 + 计时 + 审计。"""
        with _LOCK:
            p = self.connect()
            t0 = time.time()
            try:
                out = fn(p, *a, **kw) if a or kw else fn(p)
                return out
            finally:
                if self.audit:
                    self._audit("call", fn=getattr(fn, "__name__", str(fn)),
                                sec=round(time.time() - t0, 2))

    def project(self, name):
        """取工程句柄（load 无返回值，必须再取一次）。"""
        p = self.connect()
        return p.project(name)

    def reset(self):
        """Unload our saved workspace while keeping the owned PSCAD process alive."""
        if self._pscad is not None and self._alive():
            self._pscad.new_workspace()
        self.loaded_projects.clear()
        self._loaded.clear()

    def saved(self, pscx):
        pscx = os.path.abspath(pscx)
        self._loaded[pscx] = os.stat(pscx).st_mtime_ns

    def load(self, pscx, libraries=None):
        """加载工程并返回句柄。工程名 = 文件名（不含扩展名）。"""
        p = self.connect()
        pscx = os.path.abspath(pscx)
        name = os.path.splitext(os.path.basename(pscx))[0]
        stamp = os.stat(pscx).st_mtime_ns
        if self._loaded.get(pscx) == stamp:
            return p.project(name)
        if self._loaded:
            self.reset()
        t0 = time.time()
        for library in libraries or []:
            p.load(os.path.abspath(library))
        p.load(pscx)
        loaded = p.list_projects()
        if name not in {item.get("name") for item in loaded}:
            raise TransportError("PSCAD did not load project %s; native project list: %s" % (name, loaded))
        self.loaded_projects.add(name)
        self._loaded[pscx] = stamp
        prj = p.project(name)
        if prj is None:
            raise TransportError("加载后取不到工程句柄：%s" % name)
        self._audit("load", pscx=pscx, sec=round(time.time() - t0, 2))
        return prj

    def close(self, kill_after=8.0):
        """先温和退出，超时强杀。"""
        with _LOCK:
            if self._pscad is None:
                return
            p, self._pscad = self._pscad, None
            t = threading.Thread(target=self._quiet_quit, args=(p,), daemon=True)
            t.start()
            t.join(kill_after)
            if t.is_alive():
                self.kill()
                self._audit("quit_killed", after=kill_after)
            if hasattr(p, "close_desktop"):
                p.close_desktop()
            self.loaded_projects.clear()
            self._loaded.clear()

    @staticmethod
    def _quiet_quit(p):
        try:
            p.quit()
        except Exception:
            pass

    def kill(self):
        """Terminate only the PSCAD process started by this Transport."""
        if self._pid is not None:
            # taskkill输出编码取决于Windows环境，解码失败不应掩盖退出结果。
            subprocess.run(["taskkill", "/F", "/PID", str(self._pid)],
                           capture_output=True, text=True, errors="replace")

    def force_disconnect(self):
        """杀掉 PSCAD **并关闭通信 socket**——这才是让阻塞调用返回的关键。

        仅终止进程时，等待线程仍可能停在半开的socket上。
        原因在 mhrc：`command.wait_for()` 是 `while self._sock.rx_open(): recv()`，
        而 `recv()` 在 socket 超时时返回 None、`rx_open()` 仍为 True，于是无限空转；
        `_read_xml` 只把 `socket.timeout` 和 `ConnectionResetError` 当正常情况，
        其它 `OSError` 会冒出去。主动 close socket 正好触发 `OSError`，
        让 `recv()` 抛异常、`wait_for()` 退出。

        返回是否成功关闭了 socket（仅用于诊断）。
        """
        # 顺序很重要：先杀进程，再关 socket。
        # 反过来会卡在 `shutdown(SHUT_WR)` 上——对端还活着时，Windows 上
        # shutdown可能与另一线程的recv争锁，导致进程终止步骤无法执行。
        self.kill()
        closed = False
        xml = getattr(self._pscad, "_sock", None) if self._pscad is not None else None
        if xml is not None:
            # 直接关底层 socket：XmlSocket.close() 会先 shutdown(SHUT_WR)，
            # 而对端刚死时 shutdown 可能和另一线程的 recv 抢锁、卡住不走。
            raw = getattr(xml, "_sock", None)
            if raw is not None:
                try:
                    raw.close()          # recv 立刻抛 OSError(10038/10053)
                    closed = True
                except Exception:        # noqa: BLE001
                    pass
            try:
                xml._sock = None
            except Exception:            # noqa: BLE001
                pass
        self._audit("force_disconnect", socket_closed=closed, pid=self._pid)
        return closed

    # ---------------------------------------------------------------- 审计
    def _audit(self, event, **kw):
        if not self.audit:
            return
        os.makedirs(os.path.dirname(self.audit), exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event}
        rec.update(kw)
        with open(self.audit, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------- 便捷
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def running():
    """当前是否有 PSCAD 进程在跑。"""
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Pscad.exe"],
                         capture_output=True, text=True)
    return "Pscad.exe" in (out.stdout or "")
