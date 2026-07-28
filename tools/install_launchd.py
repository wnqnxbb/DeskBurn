#!/usr/bin/env python3
"""安装或卸载 DeskBurn 的 macOS launchd 登录自启服务。

安装器使用当前 Python 解释器和当前仓库位置生成 plist。这样仓库可以放在任意
目录，也不会把维护者本机的用户名和路径提交到公开仓库。

用法：
    python tools/install_launchd.py install
    python tools/install_launchd.py uninstall
"""

from __future__ import annotations

import argparse
import html
import os
import subprocess
import sys
from pathlib import Path

LABEL = "com.deskburn.agent"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
TEMPLATE_PATH = TOOLS_DIR / "launchd" / f"{LABEL}.plist.example"
TARGET_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "deskburn-agent.log"


def _launchctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    """在当前图形会话中运行 launchctl，并统一处理文本输出。"""
    return subprocess.run(
        ["launchctl", *arguments],
        check=check,
        text=True,
        capture_output=True,
    )


def _render_plist() -> str:
    """把模板占位符替换为当前解释器、仓库和日志的绝对路径。"""
    replacements = {
        "__PYTHON_EXECUTABLE__": sys.executable,
        "__TOOLS_DIRECTORY__": str(TOOLS_DIR),
        "__LOG_PATH__": str(LOG_PATH),
    }
    rendered = TEMPLATE_PATH.read_text()
    for placeholder, value in replacements.items():
        # plist 是 XML；转义路径能正确处理目录名中的 & 等保留字符。
        rendered = rendered.replace(placeholder, html.escape(value))
    return rendered


def install() -> None:
    """生成 plist，替换已加载的旧服务，并启动新服务。"""
    TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGET_PATH.write_text(_render_plist())

    service = f"gui/{os.getuid()}/{LABEL}"
    # 服务第一次安装时 bootout 会失败，这是预期状态，不能阻止后续 bootstrap。
    _launchctl("bootout", service, check=False)
    _launchctl("bootstrap", f"gui/{os.getuid()}", str(TARGET_PATH))
    print(f"已安装并启动 {LABEL}")
    print(f"plist: {TARGET_PATH}")
    print(f"日志:  {LOG_PATH}")
    print("首次使用 BLE 时，请在 macOS 弹窗中允许当前 Python 使用蓝牙。")


def uninstall() -> None:
    """停止服务并删除安装器生成的 plist，保留日志供排障。"""
    service = f"gui/{os.getuid()}/{LABEL}"
    _launchctl("bootout", service, check=False)
    if TARGET_PATH.exists():
        TARGET_PATH.unlink()
    print(f"已卸载 {LABEL}")
    print(f"日志仍保留在 {LOG_PATH}")


def main(argv: list[str] | None = None) -> int:
    """解析子命令并执行安装或卸载。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    args = parser.parse_args(argv)

    if args.action == "install":
        install()
    else:
        uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
