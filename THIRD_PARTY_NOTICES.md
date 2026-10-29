# 第三方软件与依赖

本项目的 MIT 许可证适用于自有代码、skill 和文档，不改变外部依赖的许可。

| 软件或库 | 用途 | 获取方式 |
| --- | --- | --- |
| PSCAD / EMTDC | 工程处理与电磁暂态求解 | 用户自行安装并取得所需授权 |
| mhrc.automation 1.2.4 | PSCAD X4 官方 Python 接口 | 准备脚本从本地安装材料或官方渠道获取 |
| GFortran | 编译与链接 | 用户另行安装 |
| MCP Python SDK | 可选 AI 工具协议支持 | 安装项目的 MCP 依赖 |
| NumPy / PyYAML / Pillow / pywin32 | 数值、配置、图像和 Windows 接口 | 随项目依赖安装 |

使用上述软件时，请遵循各自许可。官方安装包、master 元件库、Help 全文和第三方 Python 包源码不随仓库分发。

`src/pscad_driver/assets/blank.pscx` 是只有 Station/Main 的空壳工程，用于创建新项目，不含电气模型或 master 组件实现。

`setup.ps1` 将获取的官方 wheel 安装到独立环境，包缓存与 Help 提取文件保存在本地工作目录。资料准备方式见 [Help 使用说明](docs/help.md)。
