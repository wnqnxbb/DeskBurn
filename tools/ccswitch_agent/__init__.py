"""Mac 端 CC Switch 用量采集与推送。

usage.py 负责只读聚合，__main__.py 提供对账用的命令行入口。
BLE 链路层在数据口径验证通过后加入。
"""

from .usage import DEFAULT_DB_PATH, UsageSnapshot, read_snapshot

__all__ = ["DEFAULT_DB_PATH", "UsageSnapshot", "read_snapshot"]
