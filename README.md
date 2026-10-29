# PSCAD AI

**让 AI 在 PSCAD 4.6.2 中建模、运行，并根据结果继续调整。**

`PSCAD 4.6.2` · `Windows` · `Python / CLI / MCP` · `MIT`

适用于仍在使用 **PSCAD 4.6.2**、希望接入 AI 工作流的工程师、研究者和工具开发者。

已有模型、部分完成的工程，或一个空白项目，都可以成为起点。PSCAD AI 为 AI 提供工程编辑、仿真执行、数据读取和资料查询工具，让它能够接着你的工程往下做，并交付可继续编辑的原生模型与实际仿真数值。

[能做什么](#从新建到接续建模) · [快速开始](#快速开始) · [接入-ai](#接入-ai) · [兼容性与已知问题](docs/compatibility.md)

## 从新建到接续建模

仿真工作常常从一个已有工程开始：模型已经搭好，需要调整工况；拓扑完成了一半，需要补充控制和测量；第一次结果不理想，需要修改结构后再算。

| 你的起点 | AI 可以进行的操作 |
| --- | --- |
| **从空工程开始** | 放置元件、设置参数、连接网络，添加模块、控制与测量输出 |
| **接着半成品继续建** | 查看当前结构，补充元件、模块和接线，分阶段完成模型 |
| **调整已经建好的模型** | 修改参数与运行条件，增删或移动元件，重接导线、调整拓扑，再编译运行 |

增量编辑以当前 `.pscx` 为依据，按稳定的组件引用或实例 ID 定位对象，保留无关结构。编辑前保存工程副本，修改过程留有记录；移动或删除元件时，相关接线由 AI 显式调整。

保存的是 PSCAD 原生工程，可继续在 PSCAD 中查看和编辑。

## 把建模、仿真与结果连起来

驱动通过官方 `mhrc.automation` 与原生工程文件操作 PSCAD，由 PSCAD 生成 Fortran、GFortran 编译链接、EMTDC 求解。AI 读取原始输出和诊断，决定下一步修改。

### 为持续工作准备的工具

- **模型可以逐步完善**：支持新建、参数编辑和结构编辑，也可导入自定义定义与外部库。
- **长任务可以持续观察**：后台会话复用、任务编号和状态查询，让客户端等待结束与仿真执行分开；批量工况支持暂停、继续和停止。
- **数值可以继续分析**：保留 `.inf`、完整编号 `.out` 和 CSV，支持选通道、选时间窗、统计及原始样本查询。
- **遇到问题可以查资料**：检索本地官方 Help、实际元件参数与条件端口，以及已安装自动化 API 的文档。
- **每轮修改有对应产物**：工程修订、编辑记录、分次运行结果与诊断，便于对照模型变化和仿真结果。

工程与运行产物保存在本地工作目录。AI 负责模型选择和结果判断，数值计算由 PSCAD/EMTDC 完成。

## 快速开始

准备 Windows、PSCAD 4.6.2 x64、GFortran 4.6.2 和 Python 3.11 或以上版本，确保 PSCAD 已获得所需授权。使用前请查看[支持环境、验证范围与已知问题](docs/compatibility.md)。

将仓库放在 ASCII 路径，在仓库目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

准备脚本会建立独立 `.venv`、安装项目依赖、查找 PSCAD、获取官方自动化库、生成配置，并从本地安装提取 Help。完成后会提供 `config.yaml` 和可导入的 `mcp.local.json`。

首次获取依赖需要网络。再次执行会复用已有环境、配置和 Help；准备过程不启动仿真。

### 手动指定安装位置或官方 wheel

无法自动确定安装位置时，附加 `-PscadHome C:\PSCAD`。已有官方 wheel 时，可附加 `-AutomationWheel C:\Downloads\mhrc_automation-1.2.4-py3-none-any.whl`。

也可在独立虚拟环境中手动安装：`pip install -e .` 提供 Python/CLI，`pip install -e ".[mcp]"` 加装 MCP。配置示例见 [config.example.yaml](config.example.yaml)。

## 接入 AI

选择具备本地命令执行能力或支持 stdio MCP 的 AI 客户端，将仿真需求、已有工程和关注的指标提供给它。

| 使用方式 | 入口 |
| --- | --- |
| **通过 MCP 调用工具** | 将准备脚本生成的 `mcp.local.json` 配置导入客户端 |
| **通过终端调用 CLI** | 使用 `.\.venv\Scripts\pscad-ai.exe` 及 `python -m pscad_driver.*` 模块入口 |
| **在仓库中使用编程 AI** | 按 [AGENTS.md](AGENTS.md) 读取配套 [skill](skills/pscad-simulation/SKILL.md) |
| **编写自己的自动化流程** | 在独立环境中导入 `pscad_driver` 的任务、编辑和数据接口 |

skill 指导 AI 使用工具；CLI、Python 和 MCP 调用同一套核心能力。你可以按客户端选择入口。

准备完成后，在仓库目录查看命令帮助：

```powershell
.\.venv\Scripts\pscad-ai.exe --help
.\.venv\Scripts\python.exe -m pscad_driver.author --help
.\.venv\Scripts\python.exe -m pscad_driver.tasks --help
```

## 工程、数据与资料

默认工作目录为 `work/`：

| 目录 | 内容 |
| --- | --- |
| `cases/` | 原生工程、组件引用、工程修订和编辑记录 |
| `runs/` | 每次采用的工程、原始数值、CSV 与运行记录 |
| `tasks/`、`logs/` | 任务状态与执行诊断 |
| `_help/` | 从本机安装准备的官方帮助 |

在仓库目录运行时默认读取 `config.yaml`，也可通过 `PSCAD_AI_CONFIG` 指定配置。任务服务默认监听 `127.0.0.1:46263`；同时使用多套安装时，为每套配置独立工作目录和端口。

本地任务服务的访问令牌由 CLI/MCP 自动管理，保存在当前 Windows 用户目录，无需额外配置。运行默认收集数值，截图通过 `--snapshot` 显式启用；具体行为见[兼容性说明](docs/compatibility.md)。

官方 Help、元件定义与 API 的查询方式见 [Help 使用说明](docs/help.md)。官方软件、主元件库与 Help 原文由用户的本地安装提供。

运行文件默认由 `.gitignore` 排除。分享文件前，请确认其中的环境信息和模型参数适合公开。

## 许可

版本 `0.1.0`。自有代码、skill 和文档使用 [MIT 许可证](LICENSE)，外部依赖遵循各自许可，见[第三方说明](THIRD_PARTY_NOTICES.md)。PSCAD 相关名称和商标归各自权利人所有，本项目为独立工具。
