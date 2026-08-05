"""命令行入口，用于跟 CC Switch 界面对账。

    python3 -m ccswitch_agent            # 人类可读
    python3 -m ccswitch_agent --json     # 机器可读
    python3 -m ccswitch_agent --watch    # 每 30 秒采样一次

数字与 CC Switch 页面不一致时，先用这里的输出定位是聚合口径的问题还是链路的
问题，不要直接改固件。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .usage import DEFAULT_DB_PATH, UsageSnapshot, read_snapshot

# 与固件保持一致的推送周期。
POLL_INTERVAL_SECONDS = 30

# 与 launchd plist 中的 StandardOutPath 保持一致。不放 /tmp：那里会被系统
# 按存活时长清理，重启后可能恰好丢掉排障最需要的那一段。
LOG_PATH = Path.home() / "Library" / "Logs" / "deskburn-agent.log"


def format_tokens(tokens: int) -> str:
    """按屏幕上的紧凑格式显示 Token 数，便于逐字符核对固件的实现。

    分档必须与 firmware/deskburn/deskburn.cpp 的 formatTokensCompact 一致：
    只有到十亿量级才切 B，几亿仍然写成 M。
    """
    if tokens >= 1_000_000_000:
        return f"{tokens / 1_000_000_000:.2f} B"
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f} M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f} K"
    return str(tokens)


def format_human(snapshot: UsageSnapshot) -> str:
    stamp = time.strftime("%H:%M:%S", time.localtime(snapshot.updated_at))
    # 对账时看的是分位，所以这里保留两位小数，不套用屏幕上的整元格式。
    # 屏幕显示 $1652 而 CLI 显示 $1651.86 是预期的，不是两边算错了。
    #
    # Token 数也和屏幕有个预期内的差异：链路按千 token 传输，屏幕上是取整过的，
    # 这里打的是原始值。
    return (
        f"TODAY       ${snapshot.today_cost_usd:.2f}"
        f"  ({format_tokens(snapshot.today_tokens)} tokens)\n"
        f"THIS WEEK   ${snapshot.week_cost_usd:.2f}"
        f"  ({format_tokens(snapshot.week_tokens)} tokens)\n"
        f"THIS MONTH  ${snapshot.month_cost_usd:.2f}"
        f"  ({format_tokens(snapshot.month_tokens)} tokens)\n"
        f"ALL TIME    ${snapshot.total_cost_usd:.2f}"
        f"  ({format_tokens(snapshot.total_tokens)} tokens)\n"
        f"updated     {stamp}"
    )


def show_status() -> int:
    """打印自启服务状态和最近日志。

    把「服务在不在跑」和「最近推了什么」放在一个命令里，省得每次去记
    launchctl 的语法和日志路径。
    """
    import subprocess

    label = "com.deskburn.agent"
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        print(f"服务未安装或未加载（{label}）")
        print("安装：")
        print("  .venv/bin/python tools/install_launchd.py install")
        return 1

    # 只取顶层字段。launchctl 的输出里嵌套小节也有 state =，缩进更深，
    # 全都打出来会混进无关的 "state = active"。
    wanted = ("state = ", "pid = ", "runs = ", "last exit code = ")
    for line in result.stdout.splitlines():
        if line.startswith("\t") and not line.startswith("\t\t"):
            stripped = line.strip()
            if stripped.startswith(wanted):
                print(stripped)

    print(f"\n日志：{LOG_PATH}")
    if LOG_PATH.exists():
        lines = LOG_PATH.read_text(errors="replace").splitlines()
        for line in lines[-5:]:
            print(f"  {line}")
    else:
        print("  （还没有日志）")
    return 0


def follow_logs() -> int:
    """跟踪日志。直接交给 tail，Ctrl-C 退出。"""
    import subprocess

    if not LOG_PATH.exists():
        print(f"日志不存在：{LOG_PATH}")
        print("服务可能还没启动过，用 --status 检查。")
        return 1

    try:
        subprocess.run(["tail", "-f", str(LOG_PATH)])
    except KeyboardInterrupt:
        pass
    return 0


def emit(snapshot: UsageSnapshot, as_json: bool) -> None:
    if as_json:
        print(json.dumps(snapshot.to_dict(), ensure_ascii=False))
    else:
        print(format_human(snapshot))
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ccswitch_agent")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help="CC Switch 数据库路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--watch", action="store_true",
                        help=f"每 {POLL_INTERVAL_SECONDS} 秒采样一次")
    parser.add_argument("--serve", action="store_true",
                        help="通过 BLE 持续推送给屏幕")
    parser.add_argument("--fake", action="store_true",
                        help="配合 --serve，推固定假数据（不读数据库）")
    parser.add_argument("--list-devices", action="store_true",
                        help="扫描并列出附近可绑定的 DeskBurn")
    parser.add_argument("--bind", metavar="DEVICE",
                        help="绑定指定设备，例如 DeskBurn-70AF0986B648")
    parser.add_argument("--forget-device", action="store_true",
                        help="删除已保存的设备绑定")
    parser.add_argument("--status", action="store_true",
                        help="查看自启服务状态与最近日志")
    parser.add_argument("--logs", action="store_true",
                        help="跟踪自启服务日志（相当于 tail -f）")
    args = parser.parse_args(argv)

    if args.status:
        return show_status()

    if args.logs:
        return follow_logs()

    if args.forget_device:
        from .binding import clear_binding

        print("已删除设备绑定" if clear_binding() else "当前没有设备绑定")
        return 0

    if args.list_devices or args.bind:
        import asyncio

        from .link import bind_device, list_device_names

        try:
            if args.bind:
                asyncio.run(bind_device(args.bind))
                print(f"已绑定 {args.bind}")
            else:
                names = asyncio.run(list_device_names())
                if names:
                    for name in names:
                        print(name)
                else:
                    print("没有发现可绑定的 DeskBurn")
                    return 1
        except Exception as error:
            print(f"绑定失败：{error}", file=sys.stderr)
            return 1
        return 0

    if args.serve:
        import asyncio
        import logging

        from .binding import BindingError
        from .link import run

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
        try:
            asyncio.run(run(args.db, fake=args.fake))
        except KeyboardInterrupt:
            return 0
        except BindingError as error:
            print(f"启动失败：{error}", file=sys.stderr)
            return 1
        return 0

    while True:
        try:
            emit(read_snapshot(args.db), args.json)
        except Exception as error:
            # 单次读取失败不该终止长跑进程：CC Switch 写库时可能短暂锁住，
            # 下一轮通常就恢复了。
            print(f"read failed: {error}", file=sys.stderr)
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
