# -*- coding: utf-8 -*-
"""vision.py — 让 AI 看见自己画的图。

流程：选择画布对象 → 复制位图 → 读取剪贴板 → 保存 PNG。
只读取本次由指定PSCAD进程写入的位图，不能确认来源时拒绝保存。
"""
import os
from io import BytesIO
import threading
import time

_LOCK = threading.RLock()


def _clipboard_sequence():
    import win32clipboard
    return win32clipboard.GetClipboardSequenceNumber()


def _owned_bitmap_bytes(expected_pid, previous_sequence):
    import win32clipboard
    import win32con
    import win32process
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.GetClipboardSequenceNumber() == previous_sequence:
            raise RuntimeError("PSCAD did not update the clipboard")
        owner = win32clipboard.GetClipboardOwner()
        if not owner or win32process.GetWindowThreadProcessId(owner)[1] != expected_pid:
            raise RuntimeError("Clipboard image is not owned by the requested PSCAD process")
        for fmt in (win32con.CF_DIB, win32con.CF_DIBV5):
            if win32clipboard.IsClipboardFormatAvailable(fmt):
                return bytes(win32clipboard.GetClipboardData(fmt))
        raise RuntimeError("PSCAD did not provide a supported clipboard bitmap")
    finally:
        win32clipboard.CloseClipboard()


def snapshot(canvas, path, bbox=None, settle=1.0, expected_pid=None):
    """截取画布一块区域，落 PNG。返回 (path, (w,h))。"""
    from PIL import Image
    if not isinstance(expected_pid, int) or expected_pid <= 0:
        raise ValueError("A verified PSCAD process ID is required for a snapshot")
    with _LOCK:
        if bbox is None:
            bbox = (0, 0, 4000, 4000)
        canvas.select_components(*bbox)
        previous = _clipboard_sequence()
        if canvas.copy_as_bitmap() is False:
            raise RuntimeError("PSCAD could not copy the requested canvas")
        time.sleep(settle)                     # 剪贴板写入有延迟
        bitmap = _owned_bitmap_bytes(expected_pid, previous)
        img = Image.open(BytesIO(bitmap))
        img.load()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        img.save(path)
        return path, img.size


def snapshot_project(prj, path, bbox=None, page="Main", expected_pid=None):
    canvas = prj.user_canvas(page)
    return snapshot(canvas, path, bbox, expected_pid=expected_pid)


def clear_selection(canvas):
    try:
        canvas.command("clear-selection").execute()
    except Exception:
        pass
