"""只读聚合 CC Switch 用量数据。

只做一件事：打开 ~/.cc-switch/cc-switch.db，算出今日、本周、本月、总计各自的
花费和 Token 数。绝不写库 —— 连接以 SQLite URI 只读模式打开，写操作会直接报错。

口径上有四个反直觉的地方，都是拿 model_pricing 反算成本验证过的，改动前先看
docs/踩坑记录.md 里的对账记录：

1. Token 累加规则由 app_type 决定，不是 input_token_semantics 字段。
2. 必须跨 data_source 去重，代理日志和会话导入日志会记录同一次请求。
3. 库不是 WAL 模式，CC Switch 运行时必须带 busy_timeout 才能读。
4. 总计要跨两张表：proxy_request_logs 只留最近约 30 天，更早的在
   usage_daily_rollups 里。两张表按日期互斥，相加即可。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path.home() / ".cc-switch" / "cc-switch.db"

# CC Switch 写入期间会持有写锁。库是 journal_mode=delete 而不是 WAL，
# 读事务也会被阻塞，所以必须等待而不是立刻失败。
BUSY_TIMEOUT_MS = 8000

# app_type='codex' 的行里，input_tokens 已经包含了 cache_read_tokens 和
# cache_creation_tokens；claude 的行里这几项互斥。
#
# 这条规则是实测出来的，不能用 input_token_semantics 字段代替：拿 model_pricing
# 反算 13315 行有缓存的记录，按 app_type 分类的命中率是 100%，而按 semantics
# 分类会把全部 2105 行 claude 记录判错（semantics=0 但成本对应「扣除」公式）。
# 有一组跨源重复记录更直接地证伪了该字段：同一次请求在两个 data_source 下成本
# 完全相同，semantics 也相同，但只有「扣除」公式能对上存储的成本。
_TOKENS_EXPR = """
    CASE WHEN app_type = 'codex'
         THEN input_tokens + output_tokens
         ELSE input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens
    END
"""

# 本地时区的本周一 00:00。SQLite 的 'weekday 1' 修饰符不能直接用：它返回的是
# 下一个周一，配合 '-7 days' 在周一当天会退到上周一。改用星期序号手工回退。
_WEEK_START = "date('now','localtime','-' || ((strftime('%w','now','localtime') + 6) % 7) || ' days')"

_MONTH_START = "date('now','localtime','start of month')"
_DAY_START = "date('now','localtime')"

# 同一次请求可能同时出现在代理日志和会话导入日志里。指纹用四个 token 字段加
# created_at，实测近 30 天有 33 组重复，全部是 proxy 与 *_session 的成对记录。
# 保留 proxy 那条：它带 pricing_model 和 cost_multiplier，字段更完整。
_DEDUP_RANK = """
    ROW_NUMBER() OVER (
        PARTITION BY app_type, input_tokens, output_tokens,
                     cache_read_tokens, cache_creation_tokens, created_at
        ORDER BY CASE data_source WHEN 'proxy' THEN 0 ELSE 1 END, request_id
    )
"""

# 今日 / 本周 / 本月只扫本月的数据。created_at 上有索引，范围很小，不需要额外
# 的物化或缓存。
_QUERY = f"""
WITH deduped AS (
    SELECT *, {_DEDUP_RANK} AS dup_rank
      FROM proxy_request_logs
     WHERE created_at >= strftime('%s', {_MONTH_START})
),
usage AS (
    SELECT created_at,
           CAST(total_cost_usd AS REAL) AS cost,
           {_TOKENS_EXPR} AS tokens
      FROM deduped
     WHERE dup_rank = 1
)
SELECT
    COALESCE(SUM(CASE WHEN created_at >= strftime('%s', {_DAY_START})
                      THEN cost END), 0.0)   AS today_cost,
    COALESCE(SUM(CASE WHEN created_at >= strftime('%s', {_DAY_START})
                      THEN tokens END), 0)   AS today_tokens,
    COALESCE(SUM(CASE WHEN created_at >= strftime('%s', {_WEEK_START})
                      THEN cost END), 0.0)   AS week_cost,
    COALESCE(SUM(CASE WHEN created_at >= strftime('%s', {_WEEK_START})
                      THEN tokens END), 0)   AS week_tokens,
    COALESCE(SUM(cost), 0.0)                 AS month_cost,
    COALESCE(SUM(tokens), 0)                 AS month_tokens
  FROM usage
"""


# 总计要跨两张表。proxy_request_logs 只保留最近约 30 天，更早的数据被归档进
# usage_daily_rollups，两者按日期严格互斥（实测 10 份历史备份里重叠都是 0 行），
# 所以直接相加，不会重复计数。
#
# 这一点纠正了早前「rollups 已停更」的判断：它最后一条停在一个月前，看着像死表，
# 其实是归档层，会随日志过期同步往前推。三份备份的边界可以看出这个咬合关系：
#
#     备份 07-22   rollups 截止 06-22   logs 起于 06-23
#     备份 07-24   rollups 截止 06-24   logs 起于 06-25
#     当前库       rollups 截止 06-26   logs 起于 06-29
#
# 归档层的口径有个已知偏差：它的成本和 Token 都等于日志的**原始**求和，没有做
# 跨源去重，因此比现算路径略高。实测量级是 0.14%（$1713 里多算 $2.44），且原始
# 行已经不在库里，无法追溯修正。总计本来就是个量级参考，这个偏差可以接受。
#
# 用 date NOT IN (日志覆盖的日期) 而不是写死一个分界日期：两张表的边界随保留
# 窗口每天移动，查出来比猜稳。
#
# 归档表的 token 列名与日志表完全一致，所以 _TOKENS_EXPR 可以直接复用 ——
# 归档层同样区分 app_type，不能对它另立一套累加规则。
_ALL_TIME_QUERY = f"""
WITH deduped AS (
    SELECT *, {_DEDUP_RANK} AS dup_rank
      FROM proxy_request_logs
),
live AS (
    SELECT COALESCE(SUM(CAST(total_cost_usd AS REAL)), 0.0) AS cost,
           COALESCE(SUM({_TOKENS_EXPR}), 0)                 AS tokens
      FROM deduped
     WHERE dup_rank = 1
),
archived AS (
    SELECT COALESCE(SUM(CAST(total_cost_usd AS REAL)), 0.0) AS cost,
           COALESCE(SUM({_TOKENS_EXPR}), 0)                 AS tokens
      FROM usage_daily_rollups
     WHERE date NOT IN (
               SELECT DISTINCT date(created_at, 'unixepoch', 'localtime')
                 FROM proxy_request_logs
           )
)
SELECT (SELECT cost FROM live) + (SELECT cost FROM archived),
       (SELECT tokens FROM live) + (SELECT tokens FROM archived)
"""


@dataclass(frozen=True)
class UsageSnapshot:
    """一次采样的聚合结果，对应屏幕上的四组「金额 + Token」。"""

    today_cost_usd: float
    today_tokens: int
    week_cost_usd: float
    week_tokens: int
    month_cost_usd: float
    month_tokens: int
    total_cost_usd: float
    total_tokens: int
    updated_at: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _connect(db_path: Path) -> sqlite3.Connection:
    """以只读模式打开数据库。

    用 SQLite 的 URI 形式而不是普通路径：mode=ro 由 SQLite 自己保证，任何写
    语句都会抛 OperationalError，比靠调用方自觉更可靠。CC Switch 是这个库的
    唯一写入方，我们不能干扰它。
    """
    if not db_path.exists():
        raise FileNotFoundError(f"CC Switch database not found: {db_path}")

    connection = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return connection


def _read_all_time(connection: sqlite3.Connection) -> tuple[float, int]:
    """读总计花费与总计 Token。归档表缺失时退化为只算日志覆盖的部分。

    单独 catch 而不是让异常冒到 read_snapshot：归档表是 CC Switch 的内部结构，
    将来版本改名或删表都有可能。那种情况下屏幕上少一个总计数字可以接受，但不该
    把今日 / 本周 / 本月一起拖下水。
    """
    try:
        row = connection.execute(_ALL_TIME_QUERY).fetchone()
    except sqlite3.OperationalError:
        row = connection.execute(f"""
            WITH deduped AS (
                SELECT *, {_DEDUP_RANK} AS dup_rank FROM proxy_request_logs
            )
            SELECT COALESCE(SUM(CAST(total_cost_usd AS REAL)), 0.0),
                   COALESCE(SUM({_TOKENS_EXPR}), 0)
              FROM deduped WHERE dup_rank = 1
        """).fetchone()
    return float(row[0] or 0.0), int(row[1] or 0)


def read_snapshot(db_path: Path | str = DEFAULT_DB_PATH) -> UsageSnapshot:
    """读一次聚合结果。

    调用方负责处理异常：库被锁死或文件缺失时应保留上一次的有效值，而不是让
    屏幕清零。
    """
    connection = _connect(Path(db_path))
    try:
        row = connection.execute(_QUERY).fetchone()
        total_cost, total_tokens = _read_all_time(connection)
    finally:
        connection.close()

    (today_cost, today_tokens, week_cost, week_tokens,
     month_cost, month_tokens) = row
    return UsageSnapshot(
        today_cost_usd=round(float(today_cost), 2),
        today_tokens=int(today_tokens),
        week_cost_usd=round(float(week_cost), 2),
        week_tokens=int(week_tokens),
        month_cost_usd=round(float(month_cost), 2),
        month_tokens=int(month_tokens),
        total_cost_usd=round(total_cost, 2),
        total_tokens=total_tokens,
        updated_at=int(time.time()),
    )
