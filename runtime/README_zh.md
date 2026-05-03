# 内置运行环境说明

这个目录用于给完整环境版分发包提供本地运行时。

当前包含：

```text
runtime/python/python.exe
```

来源：

```text
Python 3.13.13 Windows embeddable package (64-bit)
https://www.python.org/ftp/python/3.13.13/python-3.13.13-embed-amd64.zip
```

用途：

- 启动本地 GUI 后端 `scripts/blueprint_tool_server.py`。
- 运行蓝图分析脚本 `scripts/bp_clipboard_to_prompt.py`。

不包含：

- Node.js。普通使用不需要 Node.js，因为包内已有构建好的 `dist/`。
- ARK DevKit。`.uasset` 资产仍然需要来自用户本机的 ARK DevKit Content 目录。
- ARK DevKit 内部 Python。DevKit 导出命令仍然在 ARK DevKit 自己的 Python 环境里运行。

启动脚本 `START_HERE.bat` 和 `scripts/launch_blueprint_tool.ps1` 会优先使用这里的 Python；如果此目录不存在，才回退到系统 `python`。
