# 官方帮助检索

支持搜索本地官方 Help、分页阅读正文，以及查询元件定义和已安装自动化 API。Help 正文与图片来自已安装的 PSCAD。

## 准备资料

运行仓库的 `setup.ps1` 后，Help 会从安装目录的 `help/ol-help.chm` 提取到工作目录 `_help/`。已配置安装路径但尚未提取时，首次查询会自动准备资料。

也可使用独立环境提前准备：

```powershell
.\.venv\Scripts\python.exe -m pscad_driver.helpdocs prepare
```

添加 `--source` 可指定已有 CHM 或 HTML 目录。已有资料会复用；更换 Help 版本时，可改用新的工作目录，或移走旧 `_help/` 后重新准备。

CHM 提取使用 Windows 自带的 `hh.exe`，隐藏窗口，不启动 PSCAD。

## 搜索与阅读

在仓库目录执行：

```powershell
.\.venv\Scripts\python.exe -m pscad_driver.helpdocs search 'output channel'
```

搜索结果中的 `file` 是可阅读的相对路径，可能包含子目录。将该值传给阅读命令：

```powershell
.\.venv\Scripts\python.exe -m pscad_driver.helpdocs read '<file>'
```

`read` 支持 `--start` 和 `--limit` 分页。搜索覆盖 `.htm` 与 `.html`，阅读路径限定在 Help 目录内。MCP 的 `help_query` 使用同一套资料和实现。

## 资料分发

仓库提供准备与检索代码，不附官方 Help 全文。官方软件文档按其许可在本地使用。提取资料、下载缓存和本地配置默认由 `.gitignore` 排除。

## 官方资源

- [X4 Automation Library 1.2.4 安装教程](https://www.pscad.com/webhelp/al-help/tutorial/index.html)
- [Automation Library 下载入口](https://www.pscad.com/software/pscad/automation-library)
- [X4 自动化库安装包](https://www.pscad.com/uploads/public/files/AutomationLibrary_463_124.zip)
- [自动化 API 文档](https://www.pscad.com/webhelp/al-help/index.html)

安装包名称中的 463 是官方打包版本。准备脚本只安装其中的自动化库 1.2.4，不会安装或升级 PSCAD。官方库兼容说明包含 PSCAD 4.6.1 及以后版本，本项目的目标版本为 4.6.2。
