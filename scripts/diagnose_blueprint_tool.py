"""Diagnose local Blueprint to Code runtime, DevKit path, and asset lookup issues."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import py_compile
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
LOG_ROOT = PROJECT_ROOT / "logs" / "diagnostics"
DEVKIT_CONTENT_ROOT_FILE = PROJECT_ROOT / "devkit_content_root.txt"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.devkit_paths import (  # noqa: E402
    DEFAULT_CONTENT_ROOTS,
    devkit_content_root_candidates,
)

STATUS_LABELS = {
    "pass": "通过",
    "warn": "警告",
    "fail": "失败",
    "info": "信息",
}


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def read_first_config_line(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            value = line.strip().strip("\"'")
            if value and not value.startswith("#"):
                return value
    except OSError:
        return ""
    return ""


def clean_path(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None:
        return None
    text = str(value).strip().strip("\"'")
    if not text:
        return None
    return Path(text).expanduser()


def add_check(
    checks: list[dict[str, Any]],
    category: str,
    name: str,
    status: str,
    detail: str,
    advice: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "category": category,
            "name": name,
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "detail": detail,
            "advice": advice,
            "data": data or {},
        }
    )


def dedupe_paths(items: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    unique: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source, path in items:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append((source, path))
    return unique


def candidate_content_roots(cli_roots: list[str]) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    for value in cli_roots:
        path = clean_path(value)
        if path:
            candidates.append(("命令行 --content-root", path))
    candidates.extend(
        devkit_content_root_candidates(
            config_file=DEVKIT_CONTENT_ROOT_FILE,
            default_roots=DEFAULT_CONTENT_ROOTS,
        )
    )
    return dedupe_paths(candidates)


def first_existing_content_root(candidates: list[tuple[str, Path]]) -> tuple[str, Path] | None:
    for source, path in candidates:
        if path.is_dir():
            return source, path
    return None


def run_command(command: list[str], timeout: int = 8) -> tuple[int | None, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        text = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
        return completed.returncode, text.strip()
    except Exception as exc:
        return None, str(exc)


def check_project_structure(checks: list[dict[str, Any]]) -> None:
    required_files = [
        "START_HERE.bat",
        "scripts/blueprint_tool_server.py",
        "scripts/blueprint_translator/uasset_graphs.py",
        "scripts/bp_clipboard_to_prompt.py",
        "package.json",
        "index.html",
        "devkit_content_root.example.txt",
    ]
    missing = [name for name in required_files if not (PROJECT_ROOT / name).is_file()]
    if missing:
        add_check(
            checks,
            "项目文件",
            "必要文件",
            "fail",
            "缺少文件：" + ", ".join(missing),
            "重新解压完整包，或确认没有只复制了部分文件。",
        )
    else:
        add_check(checks, "项目文件", "必要文件", "pass", "核心文件都存在。")

    for dirname in ("scripts", "src", "dist", "runtime"):
        path = PROJECT_ROOT / dirname
        status = "pass" if path.is_dir() else ("warn" if dirname in {"dist", "runtime"} else "fail")
        advice = ""
        if dirname == "dist" and not path.is_dir():
            advice = "没有 dist 时需要 npm run build；完整环境包通常应自带 dist。"
        elif dirname == "runtime" and not path.is_dir():
            advice = "没有 runtime 时会改用系统 Python；给小白使用时建议使用完整环境包。"
        add_check(checks, "项目文件", dirname, status, str(path), advice)


def check_python_runtime(checks: list[dict[str, Any]]) -> None:
    version = sys.version_info
    if version >= (3, 10):
        add_check(checks, "Python", "版本", "pass", f"{sys.version.split()[0]} ({sys.executable})")
    else:
        add_check(checks, "Python", "版本", "fail", sys.version, "需要 Python 3.10 或更高版本。")

    bundled = PROJECT_ROOT / "runtime" / "python" / "python.exe"
    if bundled.is_file():
        using_bundled = Path(sys.executable).resolve() == bundled.resolve()
        status = "pass" if using_bundled else "info"
        detail = "正在使用内置 Python。" if using_bundled else f"内置 Python 存在，但当前使用：{sys.executable}"
        add_check(checks, "Python", "内置运行时", status, detail)
    else:
        add_check(checks, "Python", "内置运行时", "warn", "未找到 runtime/python/python.exe。", "完整环境包应包含内置 Python。")

    compile_targets = [
        PROJECT_ROOT / "scripts" / "blueprint_tool_server.py",
        PROJECT_ROOT / "scripts" / "build_ark_knowledge_base.py",
        PROJECT_ROOT / "scripts" / "devkit_exporters" / "export_current_blueprint_defaults.py",
        PROJECT_ROOT / "scripts" / "diagnose_blueprint_tool.py",
    ]
    errors: list[str] = []
    for target in compile_targets:
        try:
            py_compile.compile(str(target), doraise=True)
        except Exception as exc:
            errors.append(f"{target.name}: {exc}")
    if errors:
        add_check(checks, "Python", "脚本语法", "fail", "\n".join(errors), "脚本文件可能损坏或 Python 版本过低。")
    else:
        add_check(checks, "Python", "脚本语法", "pass", "核心 Python 脚本可以编译。")

    try:
        sys.path.insert(0, str(SCRIPT_ROOT))
        import blueprint_translator.uasset_graphs as _uasset_graphs  # noqa: F401

        add_check(checks, "Python", "解析模块导入", "pass", "blueprint_translator.uasset_graphs 导入成功。")
    except Exception as exc:
        add_check(checks, "Python", "解析模块导入", "fail", str(exc), "检查 scripts/blueprint_translator 是否完整。")

    try:
        import tkinter  # noqa: F401

        add_check(checks, "Python", "tkinter 辅助窗口", "pass", "tkinter 可用。")
    except Exception:
        add_check(
            checks,
            "Python",
            "tkinter 辅助窗口",
            "warn",
            "tkinter 不可用。",
            "主网页不受影响；旧的 DevKit 导出路径小窗口会退化为文字提示。优先使用 START_HERE.bat 打开的网页。",
        )


def check_frontend(checks: list[dict[str, Any]]) -> None:
    dist_index = PROJECT_ROOT / "dist" / "index.html"
    if dist_index.is_file():
        add_check(checks, "前端", "预构建页面 dist", "pass", str(dist_index))
    else:
        add_check(checks, "前端", "预构建页面 dist", "warn", "dist/index.html 不存在。", "需要 npm run build，或重新使用完整环境包。")

    npm = shutil.which("npm")
    node_modules = PROJECT_ROOT / "node_modules"
    if npm:
        code, output = run_command([npm, "--version"], timeout=6)
        status = "pass" if code == 0 else "warn"
        add_check(checks, "前端", "npm", status, output or npm, "没有 dist 时才必须依赖 npm。")
        if not node_modules.is_dir() and dist_index.is_file():
            add_check(
                checks,
                "前端",
                "node_modules",
                "info",
                "未找到 node_modules，但已有 dist，可以直接运行。",
                "需要修改前端并重新构建时，再运行 npm install。",
            )
    elif dist_index.is_file():
        add_check(checks, "前端", "npm", "info", "未找到 npm，但已有 dist，可以直接运行。")
    else:
        add_check(checks, "前端", "npm", "fail", "未找到 npm，且 dist 不存在。", "安装 Node.js 后运行 npm install 和 npm run build，或使用完整环境包。")

    package_lock = PROJECT_ROOT / "package-lock.json"
    add_check(
        checks,
        "前端",
        "package-lock",
        "pass" if package_lock.is_file() else "warn",
        str(package_lock),
        "" if package_lock.is_file() else "缺少 lock 文件时 npm install 结果可能和打包环境不同。",
    )


def check_devkit_content(checks: list[dict[str, Any]], cli_roots: list[str]) -> tuple[str, Path] | None:
    candidates = candidate_content_roots(cli_roots)
    existing = first_existing_content_root(candidates)
    for index, value in enumerate(cli_roots, start=1):
        cli_path = clean_path(value)
        if cli_path:
            add_check(
                checks,
                "DevKit Content",
                f"命令行 Content 根目录 {index}",
                "pass" if cli_path.is_dir() else "fail",
                str(cli_path),
                "" if cli_path.is_dir() else "手动传入的 --content-root 不存在；请检查盘符和目录拼写。",
            )

    for env_name in ("ARK_DEVKIT_CONTENT_ROOT", "BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT", "ARK_DEVKIT_ROOT", "BLUEPRINT_TO_CODE_DEVKIT_ROOT"):
        raw = os.environ.get(env_name)
        env_path = clean_path(raw)
        if not env_path:
            continue
        check_path = env_path if env_name.endswith("CONTENT_ROOT") else env_path / "Projects" / "ShooterGame" / "Content"
        add_check(
            checks,
            "DevKit Content",
            f"环境变量 {env_name}",
            "pass" if check_path.is_dir() else "fail",
            str(check_path),
            "" if check_path.is_dir() else "这个环境变量指向的 Content 目录不存在；请改正或删除该环境变量。",
        )

    config_value = read_first_config_line(DEVKIT_CONTENT_ROOT_FILE)
    if config_value:
        configured = Path(config_value).expanduser()
        status = "pass" if configured.is_dir() else "fail"
        add_check(
            checks,
            "DevKit Content",
            "devkit_content_root.txt",
            status,
            str(configured),
            "" if configured.is_dir() else "打开 devkit_content_root.txt，把第一行改成真实的 ShooterGame\\Content 目录。",
        )
    else:
        auto_discovered = existing is not None and existing[0] == "Epic Games Launcher manifest"
        add_check(
            checks,
            "DevKit Content",
            "devkit_content_root.txt",
            "info" if auto_discovered else "warn",
            (
                "未配置 devkit_content_root.txt；已通过 Epic Launcher 安装清单自动发现。"
                if auto_discovered
                else "未配置 devkit_content_root.txt。"
            ),
            (
                ""
                if auto_discovered
                else "工具会先读取 Epic Launcher 安装清单；如果自动发现失败，再把 devkit_content_root.example.txt 复制为 devkit_content_root.txt，并写入 Content 目录。"
            ),
        )

    if not existing:
        attempted = "\n".join(f"- {source}: {path}" for source, path in candidates[:12])
        add_check(
            checks,
            "DevKit Content",
            "可用 Content 根目录",
            "fail",
            "没有找到可用的 ShooterGame\\Content。\n" + attempted,
            "确认 ARK DevKit 已安装，并配置 devkit_content_root.txt，例如：G:\\ARKDevkit\\Projects\\ShooterGame\\Content",
        )
        return None

    source, root = existing
    add_check(checks, "DevKit Content", "可用 Content 根目录", "pass", f"{source}: {root}")
    common_dirs = [root / name for name in ("PrimalEarth", "ASA", "Packs")]
    found_dirs = [path.name for path in common_dirs if path.is_dir()]
    if found_dirs:
        add_check(checks, "DevKit Content", "常见资产目录", "pass", "找到：" + ", ".join(found_dirs))
    else:
        add_check(
            checks,
            "DevKit Content",
            "常见资产目录",
            "warn",
            "没有看到 PrimalEarth / ASA / Packs。",
            "这个路径可能不是 ShooterGame\\Content，或者 DevKit 内容不完整。",
        )
    return existing


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def check_port(checks: list[dict[str, Any]], port: int) -> None:
    if not port_is_open(port):
        add_check(checks, "服务端", f"端口 {port}", "pass", "端口空闲，可以启动控制中心。")
        return

    url = f"http://127.0.0.1:{port}/api/state"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        project_root = str(data.get("projectRoot") or "")
        if Path(project_root).resolve() == PROJECT_ROOT.resolve():
            add_check(checks, "服务端", f"端口 {port}", "pass", "本项目控制中心已经在运行。")
        else:
            add_check(
                checks,
                "服务端",
                f"端口 {port}",
                "warn",
                f"端口被另一个 Blueprint Tool 占用：{project_root}",
                "关闭旧窗口，或用 scripts\\launch_blueprint_tool.ps1 -Port 8766 换端口。",
            )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        add_check(
            checks,
            "服务端",
            f"端口 {port}",
            "warn",
            f"端口被占用，但不是可识别的控制中心：{exc}",
            "关闭占用该端口的程序，或换一个端口启动。",
        )


def check_asset_lookup(
    checks: list[dict[str, Any]],
    asset_path: str,
    content_root: tuple[str, Path] | None,
    deep_asset: bool,
    max_graphs: int,
) -> None:
    if not asset_path:
        if content_root:
            sys.path.insert(0, str(SCRIPT_ROOT))
            try:
                from blueprint_translator.uasset_graphs import object_path_to_uasset_path

                sample = "/Game/PrimalEarth/Dinos/Dodo/Dodo_Character_BP.Dodo_Character_BP"
                found, attempted = object_path_to_uasset_path(sample, [content_root[1]])
                if found:
                    add_check(checks, "资产读取", "示例 Dodo 路径", "pass", str(found))
                else:
                    add_check(
                        checks,
                        "资产读取",
                        "示例 Dodo 路径",
                        "info",
                        "没有找到 Dodo 示例资产；这不一定是错误。",
                        "如果某个具体蓝图读不到，请运行 DIAGNOSE.bat \"/Game/.../Asset.Asset\"。",
                        {"attempted": attempted[:8]},
                    )
            except Exception as exc:
                add_check(checks, "资产读取", "示例路径检查", "warn", str(exc))
        return

    try:
        sys.path.insert(0, str(SCRIPT_ROOT))
        from blueprint_translator.uasset_graphs import (
            normalize_blueprint_object_path,
            object_path_to_uasset_path,
            read_uasset_graph_content,
        )
    except Exception as exc:
        add_check(checks, "资产读取", "解析模块", "fail", str(exc), "解析模块导入失败，无法检查具体资产。")
        return

    normalized = normalize_blueprint_object_path(asset_path)
    if not normalized:
        add_check(
            checks,
            "资产读取",
            "Object Path 格式",
            "fail",
            asset_path,
            "请从 ARK DevKit 右键资产 Copy Reference，路径应以 /Game/ 开头。",
        )
        return
    add_check(checks, "资产读取", "Object Path 格式", "pass", normalized)

    extra_roots = [content_root[1]] if content_root else []
    uasset_path, attempted = object_path_to_uasset_path(normalized, extra_roots)
    if not uasset_path:
        add_check(
            checks,
            "资产读取",
            ".uasset 定位",
            "fail",
            "没有找到对应 .uasset。",
            "检查 Content 根目录、devkit_path_mappings.txt 和 Object Path 的目录/资产名是否拼写完全一致。",
            {"attempted_paths": attempted[:20]},
        )
        return

    add_check(checks, "资产读取", ".uasset 定位", "pass", str(uasset_path), data={"attempted_paths": attempted[:20]})
    uexp_path = uasset_path.with_suffix(".uexp")
    if uexp_path.is_file():
        add_check(checks, "资产读取", ".uexp 旁文件", "pass", str(uexp_path))
    else:
        add_check(checks, "资产读取", ".uexp 旁文件", "info", "没有同名 .uexp。", "有些资产不一定有 .uexp；如果解析结果很少，再重点检查。")

    if not deep_asset:
        add_check(checks, "资产读取", "深度解析", "info", "未启用 --deep-asset。", "需要检查解析器本身时再加 --deep-asset。")
        return

    try:
        payload = read_uasset_graph_content(normalized, uasset_path, max_graphs=max_graphs)
        graphs = payload.get("graphs", []) if isinstance(payload, dict) else []
        node_count = sum(len(graph.get("nodes", [])) for graph in graphs if isinstance(graph, dict))
        add_check(
            checks,
            "资产读取",
            "深度解析",
            "pass" if graphs else "warn",
            f"解析图页 {len(graphs)} 个，节点 {node_count} 个。",
            "" if graphs else "资产可能确实没有可恢复图页，或需要补解析规则。",
        )
    except Exception as exc:
        add_check(checks, "资产读取", "深度解析", "fail", str(exc), "把本诊断报告和该 Object Path 发给维护者。")


def summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"pass": 0, "warn": 0, "fail": 0, "info": 0}
    for item in checks:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    if counts.get("fail", 0):
        verdict = "fail"
    elif counts.get("warn", 0):
        verdict = "warn"
    else:
        verdict = "pass"
    return {"verdict": verdict, "counts": counts}


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Blueprint to Code 环境诊断报告",
        "",
        f"- 生成时间：{payload['generated']}",
        f"- 项目目录：`{payload['project_root']}`",
        f"- 操作系统：{payload['system']['platform']}",
        f"- Python：`{payload['system']['python']}`",
        f"- 总体状态：**{STATUS_LABELS.get(summary['verdict'], summary['verdict'])}**",
        f"- 检查数量：通过 {summary['counts'].get('pass', 0)}，警告 {summary['counts'].get('warn', 0)}，失败 {summary['counts'].get('fail', 0)}，信息 {summary['counts'].get('info', 0)}",
        "",
    ]

    for category in sorted({item["category"] for item in payload["checks"]}):
        lines.extend([f"## {category}", "", "| 状态 | 项目 | 结果 | 建议 |", "| --- | --- | --- | --- |"])
        for item in [check for check in payload["checks"] if check["category"] == category]:
            detail = str(item["detail"]).replace("\n", "<br>")
            advice = str(item.get("advice") or "").replace("\n", "<br>")
            lines.append(f"| {item['status_label']} | {item['name']} | {detail} | {advice} |")
        lines.append("")

    failures = [item for item in payload["checks"] if item["status"] == "fail"]
    warnings = [item for item in payload["checks"] if item["status"] == "warn"]
    if failures or warnings:
        lines.extend(["## 优先处理", ""])
        for item in failures + warnings:
            advice = item.get("advice") or "查看上面的结果。"
            lines.append(f"- [{item['status_label']}] {item['category']} / {item['name']}：{advice}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose Blueprint to Code environment and asset lookup.")
    parser.add_argument("asset_path", nargs="?", default="", help="Optional /Game/... Object Path to diagnose.")
    parser.add_argument("--content-root", action="append", default=[], help="Extra ARK DevKit ShooterGame/Content path.")
    parser.add_argument("--port", type=int, default=8765, help="Control-center port to check.")
    parser.add_argument("--deep-asset", action="store_true", help="Parse the target asset after locating its .uasset.")
    parser.add_argument("--max-graphs", type=int, default=3, help="Graph limit for --deep-asset.")
    parser.add_argument("--md-out", type=Path, default=None, help="Optional Markdown output path.")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional JSON output path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    checks: list[dict[str, Any]] = []

    check_project_structure(checks)
    check_python_runtime(checks)
    check_frontend(checks)
    content_root = check_devkit_content(checks, args.content_root)
    check_port(checks, args.port)
    check_asset_lookup(checks, args.asset_path, content_root, args.deep_asset, max(args.max_graphs, 1))

    payload = {
        "schema": "blueprint-to-code.diagnostic.v1",
        "generated": _dt.datetime.now().isoformat(timespec="seconds"),
        "project_root": str(PROJECT_ROOT),
        "system": {
            "platform": platform.platform(),
            "python": sys.version,
            "python_executable": sys.executable,
        },
        "input": {
            "asset_path": args.asset_path,
            "content_root": args.content_root,
            "port": args.port,
            "deep_asset": args.deep_asset,
        },
        "summary": summarize(checks),
        "checks": checks,
    }

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = now_stamp()
    md_out = args.md_out or LOG_ROOT / f"diagnostic_{stamp}.md"
    json_out = args.json_out or LOG_ROOT / f"diagnostic_{stamp}.json"
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text(render_markdown(payload), encoding="utf-8-sig")
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    verdict = payload["summary"]["verdict"]
    counts = payload["summary"]["counts"]
    print("Blueprint to Code 环境诊断完成")
    print(f"总体状态：{STATUS_LABELS.get(verdict, verdict)}")
    print(f"通过 {counts.get('pass', 0)} / 警告 {counts.get('warn', 0)} / 失败 {counts.get('fail', 0)} / 信息 {counts.get('info', 0)}")
    print(f"Markdown 报告：{md_out}")
    print(f"JSON 报告：{json_out}")
    if verdict == "fail":
        print("")
        print("失败项：")
        for item in checks:
            if item["status"] == "fail":
                print(f"- {item['category']} / {item['name']}: {item['advice'] or item['detail']}")
    return 1 if verdict == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
