"""测试人类可读 CLI 与屏幕约定一致的格式。"""

import unittest

from ccswitch_agent.__main__ import format_tokens


class FormatTokensTest(unittest.TestCase):
    """覆盖 Token 数值与单位之间的空格约定。"""

    def test_raw_tokens_have_no_unit(self) -> None:
        """不足一千时没有单位，也不应追加多余空格。"""
        self.assertEqual(format_tokens(999), "999")

    def test_compact_units_are_separated_from_values(self) -> None:
        """K、M、B 三档都用一个 ASCII 空格分隔数值和单位。"""
        self.assertEqual(format_tokens(1_500), "1.5 K")
        self.assertEqual(format_tokens(1_940_000), "1.94 M")
        self.assertEqual(format_tokens(1_940_000_000), "1.94 B")


if __name__ == "__main__":
    unittest.main()
