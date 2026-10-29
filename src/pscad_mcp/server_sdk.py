"""MCP protocol adapter. Native mutations share the persistent CLI worker."""
import json

from mcp.server import MCPServer
from pscad_driver import data, editor, helpdocs, knowledge, reader, tasks
from pscad_driver.config import load_config

mcp = MCPServer("pscad-ai", instructions=(
    "PSCAD 4.6.2. Create/edit/build/run return asynchronous task IDs. "
    "Use task_status/task_wait/task_control; observation timeout never cancels a task. "
    "Compilation success is not an electrical acceptance verdict. "
    "Interpret original numeric outputs and adjust the engineering model yourself."
))


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


def _submit(op, **fields):
    return _json(tasks.request("submit", {"op": op, **fields}))


@mcp.tool(description="环境路径与固定版本；不启动 PSCAD，不查询证书")
def env_probe() -> str:
    cfg = load_config()
    return _json({"pscad": cfg["pscad"], "home": cfg["paths"]["home"],
                  "entry": "pscad_driver.tasks", "worker_endpoint": "%s:%s" % (tasks.HOST, tasks.PORT)})


@mcp.tool(description="连接自己的后台持久 PSCAD 会话；返回任务编号")
def pscad_connect() -> str:
    return _submit("connect")


@mcp.tool(description="关闭自己会话，保留本地任务服务；返回任务编号")
def pscad_close() -> str:
    return _submit("close")


@mcp.tool(description="提交持久任务：op=create/edit/run/build/batch/inspect/snapshot/messages/connect/close/case_new；立即返回编号")
def task_submit(spec: dict) -> str:
    return _json(tasks.request("submit", spec))


@mcp.tool(description="查看任务状态、阶段、仿真时间、完成工况与结果；空编号列出任务")
def task_status(task_id: str = "", include_result: bool = True) -> str:
    return _json(tasks.request("status", {"tid": task_id or None, "include_result": include_result}))


@mcp.tool(description="等待最多55秒；observation_timed_out仅表示观察超时，任务继续")
def task_wait(task_id: str, seconds: float = 30) -> str:
    return _json(tasks.request("wait", {"tid": task_id, "seconds": seconds}))


@mcp.tool(description="pause在当前工况结束后暂停批处理派发；resume跳过已完成工况；stop请求停止自己的仿真")
def task_control(task_id: str, action: str) -> str:
    return _json(tasks.request("control", {"tid": task_id, "action": action}))


@mcp.tool(description="从空工程按计划建模，支持pages、definition_files、libraries与orient0～7；返回任务编号")
def project_create(case_id: str, plan: dict, replace: bool = False) -> str:
    return _submit("create", case_id=case_id, plan=plan, replace=replace)


@mcp.tool(description="复制已有模板，返回任务编号")
def case_new(template: str, case_id: str, force: bool = False) -> str:
    return _submit("case_new", template=template, case_id=case_id, force=force)


@mcp.tool(description="读取实际工程各页面、条件端口、参数、ref/id、导线；支持页面筛选和分页")
def model_inspect(case_id: str, page: str = "", keyword: str = "", offset: int = 0, limit: int = 100) -> str:
    return _json(editor.inspect(case_id, page or None, keyword, offset, limit))


@mcp.tool(description="按ref/id增量编辑；ops支持set/add/clone/remove/move/connect/disconnect/wire_set/import_definition；返回任务编号。移动/删除不自动改线，显式重连")
def model_edit(case_id: str, changes: dict | None = None, settings: dict | None = None,
               page: str = "Main", ops: list | None = None) -> str:
    return _submit("edit", case_id=case_id, changes=changes, settings=settings, page=page, ops=ops)


@mcp.tool(description="编译和运行，归档实际工程、原始inf/out、CSV、诊断；返回任务编号。执行AI须据数值判断任务是否满足")
def simulation_execute(case_id: str, tag: str = "", timeout: int | None = None, snapshot: bool = False) -> str:
    return _submit("run", case_id=case_id, tag=tag or None, timeout=timeout, snapshot=snapshot)


@mcp.tool(description="与simulation_execute相同的统一编译运行路径；返回任务编号")
def project_run(case_id: str, tag: str = "", timeout: int | None = None) -> str:
    return simulation_execute(case_id, tag, timeout)


@mcp.tool(description="仅编译并返回任务编号，供诊断模型")
def project_build(case_id: str, clean: bool = False, timeout: int | None = None) -> str:
    return _submit("build", case_id=case_id, clean=clean, timeout=timeout)


@mcp.tool(description="获取原生工程诊断消息，返回任务编号")
def project_messages(case_id: str, level: str = "") -> str:
    return _submit("messages", case_id=case_id, level=level or None)


@mcp.tool(description="原生页面截图，返回任务编号")
def canvas_snapshot(case_id: str, path: str = "", page: str = "Main") -> str:
    return _submit("snapshot", case_id=case_id, path=path or None, page=page)


@mcp.tool(description="按明确工况列表逐组改参/运行/保存检查点；steps=[{changes:{ref:{param:value}},settings:{...}}]；返回任务编号")
def sweep_run(case_id: str, steps: list) -> str:
    return _submit("batch", case_id=case_id, steps=steps)


@mcp.tool(description="联合查询本地官方Help、已安装自动化API和实际元件/工程/外部库定义")
def knowledge_search(query: str, case_id: str = "", libraries: list | None = None, limit: int = 8) -> str:
    return _json(knowledge.search(query, case_id or None, libraries, limit))


@mcp.tool(description="查询实际定义：参数/默认值/单位/条件端口/自定义子页；指定params可计算有效端口，脚本按需返回")
def component_schema(defn: str, case_id: str = "", libraries: list | None = None,
                     include_script: bool = False, params: dict | None = None) -> str:
    return _json(knowledge.component(defn, case_id or None, libraries, include_script, params))


@mcp.tool(description="查询已安装mhrc.automation API签名、文档和源码位置；存在接口不保证4.6.2支持")
def api_query(query: str, limit: int = 10) -> str:
    return _json(knowledge.api(query, limit))


@mcp.tool(description="搜索本地官方Help或按文件名分页读正文")
def help_query(query: str = "", file: str = "", start: int = 0, limit: int = 10000) -> str:
    return _json(helpdocs.read(file, start, limit) if file else helpdocs.search(query, min(limit, 20)))


@mcp.tool(description="从指定输出inf列出通道名称和单位；base不含后缀")
def channels_list(base: str) -> str:
    items = reader.read_inf(base + ".inf" if not base.endswith(".inf") else base)
    names = reader.channel_names(items)
    if len(set(names)) != len(names):
        names = ["%s#%s" % (name, item["pgb"]) for name, item in zip(names, items)]
    return _json({"channels": [{"name": name, **item} for name, item in zip(names, items)]})


@mcp.tool(description="仅读取所选通道和[start,end)时间窗；stats用全部原始样本，raw限点且超限报错，envelope保留分箱极值供预览")
def channels_read(base: str, channels: list | None = None, window: list | None = None,
                  mode: str = "stats", max_points: int = 2000) -> str:
    return _json(data.query(base, channels, window, mode, max_points))


@mcp.tool(description="流式导出选定通道和时间窗的全部原始样本CSV")
def export_csv(base: str, path: str, channels: list | None = None, window: list | None = None) -> str:
    return _json(data.export(base, path, channels, window))


@mcp.tool(description="比较两个已归档运行结果")
def baseline_diff(run_dir: str, other_dir: str) -> str:
    from pscad_driver.baseline import compare
    return _json(compare(run_dir, other_dir))


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
