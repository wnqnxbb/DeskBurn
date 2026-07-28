"""聚合口径的回归测试。

每个用例对应一个实测过的坑。这三处最容易在重构时静默算错，因为错误结果依然
是个合理的数字，屏幕上看不出问题。
"""

from __future__ import annotations

import sqlite3
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ccswitch_agent.usage import read_snapshot  # noqa: E402

_SCHEMA = """
CREATE TABLE proxy_request_logs (
    request_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL DEFAULT '',
    app_type TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd TEXT NOT NULL DEFAULT '0',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    status_code INTEGER NOT NULL DEFAULT 200,
    created_at INTEGER NOT NULL,
    data_source TEXT NOT NULL DEFAULT 'proxy',
    input_token_semantics INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE usage_daily_rollups (
    date TEXT NOT NULL,
    app_type TEXT NOT NULL,
    provider_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    request_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd TEXT NOT NULL DEFAULT '0',
    PRIMARY KEY (date, app_type, provider_id, model)
);
"""


def _epoch(*, days_ago: int = 0, hour: int = 12) -> int:
    """构造本地时区某天指定小时的时间戳。

    固定用 12 点，避开 00:00 和 23:59 附近因夏令时或边界判定造成的偶发失败。
    """
    now = time.localtime()
    day = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                       hour, 0, 0, 0, 0, -1))
    return int(day) - days_ago * 86400


class UsageAggregationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(self.enterTempDir()) / "cc-switch.db"
        connection = sqlite3.connect(self.db_path)
        connection.executescript(_SCHEMA)
        connection.commit()
        connection.close()

    def enterTempDir(self) -> str:
        import tempfile
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return directory.name

    def insert(self, request_id: str, app_type: str, created_at: int, *,
               cost: str = "0", input_tokens: int = 0, output_tokens: int = 0,
               cache_read: int = 0, cache_creation: int = 0,
               data_source: str = "proxy", semantics: int = 0,
               status_code: int = 200) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            "INSERT INTO proxy_request_logs (request_id, app_type, input_tokens,"
            " output_tokens, cache_read_tokens, cache_creation_tokens,"
            " total_cost_usd, status_code, created_at, data_source,"
            " input_token_semantics)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (request_id, app_type, input_tokens, output_tokens, cache_read,
             cache_creation, cost, status_code, created_at, data_source,
             semantics),
        )
        connection.commit()
        connection.close()

    def insert_rollup(self, date: str, app_type: str = "codex", *,
                      cost: str = "0", request_count: int = 1,
                      provider_id: str = "_session") -> None:
        """写一条归档记录。date 为 'YYYY-MM-DD' 本地日期。"""
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            "INSERT INTO usage_daily_rollups (date, app_type, provider_id,"
            " request_count, total_cost_usd) VALUES (?,?,?,?,?)",
            (date, app_type, provider_id, request_count, cost),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _date(days_ago: int) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(_epoch(days_ago=days_ago)))

    def test_codex_tokens_exclude_cache(self) -> None:
        """codex 的 input_tokens 已含缓存，四项全加会虚高。"""
        self.insert("a", "codex", _epoch(), cost="1.00",
                    input_tokens=1000, output_tokens=100,
                    cache_read=800, cache_creation=50)

        snapshot = read_snapshot(self.db_path)

        self.assertEqual(snapshot.today_tokens, 1100)

    def test_claude_tokens_include_cache(self) -> None:
        """claude 的四项互斥，必须全部累加。"""
        self.insert("a", "claude", _epoch(), cost="1.00",
                    input_tokens=1000, output_tokens=100,
                    cache_read=800, cache_creation=50)

        snapshot = read_snapshot(self.db_path)

        self.assertEqual(snapshot.today_tokens, 1950)

    def test_semantics_field_does_not_override_app_type(self) -> None:
        """口径由 app_type 决定，semantics 字段不参与判断。

        实测数据里 claude 的行 semantics 全是 0，但成本对应「扣除缓存」的公式，
        所以这个字段不可信。这里用一条 semantics=1 的 claude 记录锁住行为：
        如果有人把判断改回 semantics，token 数会变成 1100。
        """
        self.insert("a", "claude", _epoch(), cost="1.00",
                    input_tokens=1000, output_tokens=100,
                    cache_read=800, cache_creation=50, semantics=1)

        snapshot = read_snapshot(self.db_path)

        self.assertEqual(snapshot.today_tokens, 1950)

    def test_cross_source_duplicates_counted_once(self) -> None:
        """同一次请求会同时落在 proxy 和 *_session 日志里。"""
        stamp = _epoch()
        for request_id, source in (("a", "proxy"), ("b", "codex_session")):
            self.insert(request_id, "codex", stamp, cost="2.50",
                        input_tokens=1000, output_tokens=100,
                        data_source=source)

        snapshot = read_snapshot(self.db_path)

        self.assertAlmostEqual(snapshot.today_cost_usd, 2.50)
        self.assertEqual(snapshot.today_tokens, 1100)

    def test_distinct_requests_with_same_shape_both_counted(self) -> None:
        """去重指纹含 created_at，不同时刻的相同用量必须各算一次。

        防止去重条件放得太宽，把连续的重复请求误判成同一次。
        """
        for offset, request_id in ((0, "a"), (1, "b")):
            self.insert(request_id, "codex", _epoch() + offset, cost="2.50",
                        input_tokens=1000, output_tokens=100)

        snapshot = read_snapshot(self.db_path)

        self.assertAlmostEqual(snapshot.today_cost_usd, 5.00)

    def test_yesterday_excluded_from_today(self) -> None:
        self.insert("a", "codex", _epoch(), cost="1.00", input_tokens=10)
        self.insert("b", "codex", _epoch(days_ago=1), cost="4.00",
                    input_tokens=20)

        snapshot = read_snapshot(self.db_path)

        self.assertAlmostEqual(snapshot.today_cost_usd, 1.00)
        self.assertEqual(snapshot.today_tokens, 10)
        self.assertAlmostEqual(snapshot.month_cost_usd, 5.00)

    def test_week_includes_today_on_monday(self) -> None:
        """周一当天本周花费不能为空。

        SQLite 的 'weekday 1' 修饰符返回下一个周一，配合 '-7 days' 会在周一
        当天退到上周一，把今天整个排除掉。
        """
        self.insert("a", "codex", _epoch(), cost="7.00", input_tokens=10)

        snapshot = read_snapshot(self.db_path)

        self.assertAlmostEqual(snapshot.week_cost_usd, 7.00)

    def test_total_spans_logs_and_archive(self) -> None:
        """总计要把归档表加进来。

        proxy_request_logs 只留最近约 30 天，更早的数据在 usage_daily_rollups
        里。只算日志会让总计随保留窗口滑动而缩水，越久越少。
        """
        self.insert("a", "codex", _epoch(), cost="10.00", input_tokens=10)
        self.insert_rollup(self._date(200), cost="90.00")

        snapshot = read_snapshot(self.db_path)

        self.assertAlmostEqual(snapshot.total_cost_usd, 100.00)
        # 归档数据不该渗进本月，那是另一套时间窗。
        self.assertAlmostEqual(snapshot.month_cost_usd, 10.00)

    def test_archive_days_covered_by_logs_are_skipped(self) -> None:
        """日志和归档同时有某天时只算日志，不能双计。

        两张表实测是日期互斥的，但 CC Switch 迁移期间出现过重叠（一份 07-23 的
        备份里有 122 行）。真重叠时归档那份是没去重的，得让日志优先。
        """
        today = self._date(0)
        self.insert("a", "codex", _epoch(), cost="10.00", input_tokens=10)
        self.insert_rollup(today, cost="999.00")

        snapshot = read_snapshot(self.db_path)

        self.assertAlmostEqual(snapshot.total_cost_usd, 10.00)

    def test_total_equals_month_when_no_archive(self) -> None:
        """没有归档数据时，总计就等于日志的全部。"""
        self.insert("a", "codex", _epoch(), cost="3.00", input_tokens=10)
        self.insert("b", "codex", _epoch(days_ago=1), cost="4.00",
                    input_tokens=10)

        snapshot = read_snapshot(self.db_path)

        self.assertAlmostEqual(snapshot.total_cost_usd, 7.00)

    def test_total_dedupes_cross_source_rows(self) -> None:
        """总计走的是全表扫描，去重不能漏掉。"""
        stamp = _epoch()
        for request_id, source in (("a", "proxy"), ("b", "codex_session")):
            self.insert(request_id, "codex", stamp, cost="2.50",
                        input_tokens=1000, data_source=source)

        snapshot = read_snapshot(self.db_path)

        self.assertAlmostEqual(snapshot.total_cost_usd, 2.50)

    def test_missing_rollups_table_falls_back_to_logs(self) -> None:
        """归档表不存在时只算日志，不能让整次采样失败。

        usage_daily_rollups 是 CC Switch 的内部结构，将来改名或删表都有可能。
        那时候屏幕上少一个准确的总计可以接受，但今日 / 本周 / 本月必须还在。
        """
        connection = sqlite3.connect(self.db_path)
        connection.execute("DROP TABLE usage_daily_rollups")
        connection.commit()
        connection.close()

        self.insert("a", "codex", _epoch(), cost="5.00", input_tokens=10)

        snapshot = read_snapshot(self.db_path)

        self.assertAlmostEqual(snapshot.today_cost_usd, 5.00)
        self.assertAlmostEqual(snapshot.total_cost_usd, 5.00)

    def test_empty_database_returns_zeros(self) -> None:
        """没有数据时返回 0 而不是 None，否则格式化会抛异常。"""
        snapshot = read_snapshot(self.db_path)

        self.assertEqual(snapshot.today_cost_usd, 0.0)
        self.assertEqual(snapshot.today_tokens, 0)
        self.assertEqual(snapshot.month_cost_usd, 0.0)
        self.assertEqual(snapshot.total_cost_usd, 0.0)

    def test_database_opened_read_only(self) -> None:
        """连接必须拒绝写入，避免影响 CC Switch 自己的库。"""
        from ccswitch_agent.usage import _connect

        connection = _connect(self.db_path)
        self.addCleanup(connection.close)

        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("DELETE FROM proxy_request_logs")

    def test_missing_database_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_snapshot(self.db_path.parent / "nope.db")


if __name__ == "__main__":
    unittest.main()
