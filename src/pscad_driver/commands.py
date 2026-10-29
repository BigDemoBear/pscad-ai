# -*- coding: utf-8 -*-
"""commands.py — 原生命令与菜单通道。

raw()按ALLOW筛选命令，menu()提供原生菜单调用。调用信息写入
logs/raw_calls.jsonl。接口依赖PSCAD版本，升级后需确认兼容性。
"""
import json
import os
import time

from .config import path_of

# PSCAD 4.6.2 + AL 1.2.4，Project/UserCmp 作用域：
#   可用：list-parameters / list-components / get-run-status / clean-files /
#         list-scenarios / get-definition
#   不可用（命令存在于二进制但返回 success=false）：get-pgb / runtime-messages /
#         dismiss-message / set-volley / get-volley / set-affinity / get-affinity /
#         is-busy / is-module
# raw()仅接受ALLOW中的命令；已知不可用命令列在UNVERIFIED中。
ALLOW = {
    "list-parameters", "list-components", "get-run-status", "clean-files",
    "list-scenarios", "get-definition",
    # 工程/画布编辑命令，也可由mhrc封装调用。
    "add-components", "remove-components", "select-components", "clear-selection",
    "get-location", "set-location", "save-as", "load-meta-files",
}

# 已知不可用或未支持的命令；通过raw()调用会被NotAllowed拒绝。
UNVERIFIED = {
    "get-pgb", "runtime-messages", "dismiss-message", "set-volley", "get-volley",
    "set-affinity", "get-affinity", "is-busy", "is-module", "get-port-location",
    "get-port", "set-port", "list-messages",
}


class NotAllowed(PermissionError):
    pass


def _log(rec):
    p = os.path.join(path_of("logs"), "raw_calls.jsonl")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def raw(obj, name, params=None, tags=None):
    """obj 可以是 PSCAD / Project / Canvas / Component（都有 command() 方法）。

    params: 直接挂在命令根上的属性
    tags:    {"tag": {"attr": "value"}} 形式的子元素
    """
    if name not in ALLOW:
        raise NotAllowed("%s 不在白名单内（见 commands.ALLOW）" % name)
    t0 = time.time()
    ok, resp_xml = False, None
    try:
        cmd = obj.command(name)
        for k, v in (params or {}).items():
            cmd.param(cmd.root, k, str(v))
        for tag, attrs in (tags or {}).items():
            el = cmd.tag(tag)
            for k, v in (attrs or {}).items():
                el.set(k, str(v))
        resp = cmd.execute()
        ok = True
        try:
            import xml.etree.ElementTree as ET
            resp_xml = ET.tostring(resp, encoding="unicode")[:800]
        except Exception:
            resp_xml = repr(resp)[:400]
    finally:
        _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": name,
              "params": params, "tags": tags, "ok": ok,
              "sec": round(time.time() - t0, 2), "resp": resp_xml})
    return resp_xml


def menu(obj, ident, command_id):
    """菜单命令通道：ident 如 IDM_EDIT_EXPORT_FILE，command_id 来自 resource.h。"""
    cmd = obj.command("generic")
    cmd.param(cmd.root, "command-id", str(command_id))
    cmd.root.set("ident", ident)
    t0 = time.time()
    try:
        resp = cmd.execute()
        ok = True
        import xml.etree.ElementTree as ET
        out = ET.tostring(resp, encoding="unicode")[:400]
    finally:
        _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": "generic",
              "ident": ident, "command-id": command_id,
              "sec": round(time.time() - t0, 2)})
    return out


def busy(prj):
    """PSCAD 是否正忙（比轮询状态更直接）。"""
    try:
        return "true" in (raw(prj, "is-busy") or "").lower()
    except Exception:
        return False


def pgb(prj):
    """直接取通道（PGB）清单，与 .inf 互为校验。"""
    return raw(prj, "get-pgb")
