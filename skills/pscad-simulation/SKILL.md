---
name: pscad-simulation
description: 通过本项目驱动 PSCAD 4.6.2 完成电磁暂态仿真，包括从空工程建模、增量修改已有工程、组织长任务、读取数值及根据结果诊断调整。适用于实际 PSCAD 仿真委托。
---

# PSCAD 仿真

将用户的问题转成工程、工况、测量和判定指标，自主调用 PSCAD/EMTDC，分析实际数值，必要时调整模型、控制、初始化、步长或工况，直到满足要求或遇到需要报告的重大阻塞。工具不替执行 AI 决定电气模型或判断目标达成。

## 安装与配置

首次使用按仓库README运行setup.ps1，建立独立环境并自动查找PSCAD、安装依赖、生成配置和准备Help。
在仓库目录使用`.venv/Scripts/python.exe`，进程内PSCAD_AI_CONFIG指向该仓库config.yaml；不使用其他项目的Python或配置。
CLI与MCP共用本项目任务服务，默认端口46263；勿指向其他安装正在使用的工作目录或服务。
工况名和tag使用ASCII名称，可含字母、数字、下划线、连字符与内部点号；不要传入路径或结尾点号。未指定超时时，CLI、MCP和任务接口使用配置时限。
先确认README中的外部软件已安装，环境调整限定在所配置的独立环境中。
编译路径和工程名用ASCII；time_duration单位秒，time_step与sample_step单位微秒。
默认在独立Windows桌面运行，不抢前台。MCP是可选入口，CLI也能完成任务。

## 查资料和工程

Help准备和检索功能随仓库提供；官方原文从用户本机合法安装的CHM自动提取到工作目录`_help/`，不提交Git。
准备脚本会处理Help；已配置环境首次查询缺少资料时也会自动准备。独立执行`python -m pscad_driver.helpdocs prepare`可提前处理；`--source`可指定自己的CHM或HTML目录。搜索返回相对文件路径，阅读时原样使用，含嵌套目录。

```powershell
# 在已安装本项目的独立Python环境中执行。
python -m pscad_driver.knowledge search 'source control'
python -m pscad_driver.knowledge component source3
python -m pscad_driver.knowledge component self:MyModule --case my_project
python -m pscad_driver.knowledge api new_workspace
python -m pscad_driver.helpdocs read 'Output_Channel.htm'
python -m pscad_driver.author inspect my_project
```

知识查询联合master、当前工程和其计划声明的外部库；可用`--libraries`补充库文件，`--script`按需读取定义脚本。条件端口随View/Ctrl等参数切换；工程检查返回当前有效端口、方向、ref/id和导线，按当前参数与方向使用有效端口坐标。`editor.inspect`支持page、keyword、offset、limit；不要一次倾倒大型工程全部内容。

API文档里存在某方法，不代表4.6.2实现了对应命令。get-port-location、is-busy、set-volley等已确认不可用，不重复试探。
已有工程检查首次需要元件索引时会自动从配置的master库生成，不必手动执行palette命令。

## 统一任务调用

Python调用便于连续处理结果：

```python
from pscad_driver.tasks import request
t = request('submit', {'op': 'run', 'case_id': 'my_project', 'tag': 'initial'})
observed = request('wait', {'tid': t['id'], 'seconds': 30})
# 若仍running，继续观察同一编号；不要重新提交。
state = request('status', {'tid': t['id']})
```

也可把任务spec写入JSON，用`python -m pscad_driver.tasks submit task.json`提交；`status <id>`、`wait <id> --seconds 30`、`control <id> pause|resume|stop`、`list`管理。author build/patch、workflow和CLI run也提交到同一服务，立即返回任务编号。

本地服务令牌由客户端自动管理，保存在当前用户LOCALAPPDATA的pscad-ai目录；不读取或展示令牌，不将其写入任务spec、配置或交付文件。升级后重启本项目任务服务使新代码生效；close只关闭PSCAD会话。

任务有queued/running/paused/succeeded/failed/stopped/interrupted状态，阶段含建模、编译、求解、收集结果；求解进度以已写出的仿真时间表示。`wait`最多55秒，`observation_timed_out`只表示观察结束；执行超时由run的`timeout`控制，主动取消用stop。编译阶段的stop等当前编译返回后不再启动求解。

任务记录位于工作目录的`tasks/`；CLI或MCP连接断开不会取消任务。服务异常退出后遗留任务标记interrupted，不自动重跑；恢复前检查此前自己的会话/求解器与输出，避免和旧运行同时写同一工程。完成任务后可提交`{'op':'close'}`释放自己的PSCAD会话，保留任务记录与服务。

## 从空工程建模

提交 `{'op':'create','case_id':'my_project','plan':{...}}`，或：

```powershell
python -m pscad_driver.author build my_project plan.json
```

计划包含settings、components、connections、outputs；结构示意：

```json
{
  "settings":{"time_duration":"0.3","time_step":"20","sample_step":"100"},
  "components":[{"ref":"R1","defn":"resistor","x":360,"y":180,"orient":0,"params":{"R":"10"}}],
  "connections":[["A.port","B.port"],{"from":"B.port","to":"C.port","via":[[540,180],[540,360]]}],
  "outputs":[{"signal":"existing_signal_name","name":"Current","units":"A","scale":1}]
}
```

这只是格式，不能原样运行。AI根据任务设计真实电气网络和控制；输出引用模型内真实信号，不会凭空产生电压电流。电压电流测量常用kV/kA，Units只改标签，换算数值需显式Scale。坐标按18网格设置。

创建从仅含Station/Main的空壳开始，用原生API放元件接线。`definition_files`导入原生Definition XML；`pages`按模块页面给出components/connections/outputs；局部定义用`self:Name`；`libraries`加载外部pslx。`orient`支持0～7：0原向，1/2/3顺时针90/180/270度，4水平镜像，5/6/7镜像后再顺时针旋转。

`replace:true`根据完整新计划重建本任务工程，旧版本保存在revisions；增量修改后的当前.pscx是模型依据，原plan加edits.jsonl记录建模过程，不能把原plan当作已包含全部后续结构编辑的完整重建计划。

## 增量编辑

提交 `op:'edit'`，指定case_id、page、changes、settings、ops；或author patch读取同格式JSON。`changes`按稳定ref或实例id改参数，禁止全局同名参数广播：

```json
{"op":"edit","case_id":"my_project","changes":{"R1":{"R":"20"}},"settings":{"time_duration":"0.5"},"ops":[]}
```

ops支持：

- `{"op":"add","ref":"R2","defn":"resistor","x":540,"y":180,"orient":1,"params":{"R":"5"}}`
- `{"op":"clone","source":"R1","ref":"R3","x":720,"y":180}`，可覆盖params/orient。
- `{"op":"move","ref":"R1","x":360,"y":360,"orient":1}` 或 `{"op":"remove","ref":"R1"}`。
- `{"op":"disconnect","id":"导线实例id"}`，`{"op":"connect","from":"R2.A","to":"R3.B","via":[[540,360],[720,360]]}`。
- `{"op":"wire_set","id":"线路实例id","params":{"已存在参数":"值"}}`修改线路对象携带的参数。
- `{"op":"import_definition","file":"my_definition.xml"}`。

已有对象的改动先落盘，之后按列表顺序添加/复制与接线；新元件的参数直接放在add/clone中，后续移动等可另交一次编辑任务。移动、旋转、删除以及改变有效端口的参数不会自动改线：先查实际导线id，显式断开和重接相关线，保留无关元件和导线。编辑前工程副本在revisions，意图记录在edits.jsonl。

特殊原生结构可直接编辑.pscx/.pslx或编写mhrc任务脚本。先等本机队列空闲并关闭其会话，避免并行改同一工程；保存后再通过统一入口运行。

## 数值、判断和持续调整

每次运行保存实际工程、.inf、完整编号.out、CSV及manifest，返回run_dir和数值base。成功只表示编译和数值输出完成，`task_satisfied`保持未代判；执行AI依据模型、物理规律和约定指标给出判断。不满足时定位原因，调整工程后使用新tag运行受影响工况；不要靠放宽目标、替换问题或造数通过。

```python
from pscad_driver.data import query, export
stats = query(base, channels=['Current'], window=[0.2, 0.3])
raw = query(base, ['Current'], [0.25, 0.26], mode='raw', max_points=2000)
preview = query(base, ['Current'], mode='envelope', max_points=1000)
export(base, csv_path, ['Current'], [0.2, 0.3])
```

时间窗统一`start <= t < end`，统计使用全部原始样本；raw超限报错，不能静默截断。envelope保留分箱极值，仅作预览，事件时刻、相位、频谱用原始数据。`reader.read_out(base)`返回完整numpy数组，供AI计算任务特定指标。失败或停止时保留诊断；残留数值不可当作本次完整结果。

CSV为公式前缀和原有前导单引号增加转义；原始.inf/.out及查询中的名称和数值不变。按原始名称选通道，比较CSV时使用其实际完整列名。

截图需显式请求（CLI run的--snapshot或任务snapshot:true），保存前核对剪贴板更新及所属PSCAD进程。来源检查失败时记录错误，可继续分析已有数值；不要改读任意剪贴板内容或清空用户剪贴板绕过检查。独立桌面下的原生截图兼容性仍需验证。

## 长任务

提交 `{'op':'batch','case_id':'my_project','steps':[{'changes':{'R1':{'R':'10'}}},{'changes':{'R1':{'R':'20'}}}]}`。工况列表先固定，每步保存独立运行及检查点；也可在step里指定不同case_id、settings、timeout。批处理不替AI判断物理目标或决定模型修正。

pause在当前工况结束后暂停派发；resume跳过已完成步，继续未完成或失败步；stop停止当前求解和后续派发。恢复沿用原任务编号，勿重新生成随机工况。状态、attempts、completed_steps和各步manifest用于定位，不全量重跑。长任务使用统一队列。

可选MCP：`python -m pscad_mcp.server_sdk`。project_create/model_edit/simulation_execute/sweep_run返回任务编号；task_status/task_wait/task_control观察和控制，knowledge_search/component_schema/api_query/help_query/channels_read/export_csv直接查资料或数值。

## 记录与交付

记录模型、工况和参数的调整及对应结果。交付采用的工程、所需依赖、原始数值与分析结论，说明关键假设和未满足项。修改后验证受影响的工况，探索过程与最终采用结果分开保存。
