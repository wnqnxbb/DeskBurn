"""线格式的回归测试。

重点是那些「错了也还是个合理数字」的失误：字段顺序错位、金额倍率错、
校验和覆盖范围错。这类 bug 在屏幕上表现为一个看着正常的数,不容易发现。

跨语言一致性由 test_protocol_parity.py 用真实 C++ 头文件验证。
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ccswitch_agent import protocol  # noqa: E402
from ccswitch_agent.usage import UsageSnapshot  # noqa: E402


def _snapshot(**overrides) -> UsageSnapshot:
    fields = {
        "today_cost_usd": 12.48,
        "today_tokens": 9_640_000,
        "week_cost_usd": 63.21,
        "month_cost_usd": 218.90,
        "total_cost_usd": 1_204.55,
        "updated_at": 1_785_140_000,
    }
    fields.update(overrides)
    return UsageSnapshot(**fields)


class PacketTest(unittest.TestCase):
    def test_packet_is_30_bytes(self) -> None:
        """长度写死在固件的 static_assert 里，两边必须一致。"""
        self.assertEqual(len(protocol.encode(_snapshot())), 30)
        self.assertEqual(protocol.PACKET_SIZE, 30)

    def test_round_trip_preserves_values(self) -> None:
        original = _snapshot()

        decoded = protocol.decode(protocol.encode(original))

        self.assertAlmostEqual(decoded.today_cost_usd, 12.48, places=3)
        self.assertEqual(decoded.today_tokens, 9_640_000)
        self.assertAlmostEqual(decoded.week_cost_usd, 63.21, places=3)
        self.assertAlmostEqual(decoded.month_cost_usd, 218.90, places=3)
        self.assertAlmostEqual(decoded.total_cost_usd, 1_204.55, places=3)
        self.assertEqual(decoded.updated_at, 1_785_140_000)

    def test_field_order_matches_spec(self) -> None:
        """逐字段核对偏移量。

        用互不相同的值,任何两个字段交换位置都会被发现 —— 如果各字段值相近,
        错位后解出来的数依然合理,测试就形同虚设。
        """
        packet = protocol.encode(_snapshot(
            today_cost_usd=1.0, today_tokens=2, week_cost_usd=3.0,
            month_cost_usd=4.0, total_cost_usd=6.0, updated_at=5,
        ))

        magic, version, flags, today, tokens, week, month, total, epoch, _crc = \
            struct.unpack("<HBBIIIIIIH", packet)

        self.assertEqual(magic, protocol.MAGIC)
        self.assertEqual(version, protocol.PROTOCOL_VERSION)
        self.assertEqual(flags, protocol.FLAG_NONE)
        self.assertEqual(today, 1000)
        self.assertEqual(tokens, 2)
        self.assertEqual(week, 3000)
        self.assertEqual(month, 4000)
        self.assertEqual(total, 6000)
        self.assertEqual(epoch, 5)

    def test_total_sits_after_month(self) -> None:
        """总计的偏移量固定在 month 之后、epoch 之前。

        插字段时最容易出错的就是位置:放到 epoch 后面两边都还能自洽跑通,
        但设备端解出来的时间戳会变成金额,表现为一直 OFFLINE。
        """
        packet = protocol.encode(_snapshot(total_cost_usd=6.0, updated_at=5))

        self.assertEqual(struct.unpack_from("<I", packet, 20)[0], 6000)
        self.assertEqual(struct.unpack_from("<I", packet, 24)[0], 5)

    def test_amounts_carried_as_milli_usd(self) -> None:
        """倍率必须是 1000。用 999.99 这种值能暴露截断而不是四舍五入。"""
        packet = protocol.encode(_snapshot(today_cost_usd=999.99))

        today_milli = struct.unpack_from("<I", packet, 4)[0]

        self.assertEqual(today_milli, 999_990)

    def test_corrupted_byte_rejected(self) -> None:
        """任何一位翻转都必须被 CRC 拦住。"""
        packet = bytearray(protocol.encode(_snapshot()))
        packet[6] ^= 0x01

        with self.assertRaises(ValueError):
            protocol.decode(bytes(packet))

    def test_crc_covers_every_payload_byte(self) -> None:
        """逐字节翻转,确认 CRC 的覆盖范围没有漏掉任何字段。

        这条能抓住「CRC 只算了前几个字段」这类实现错误 —— 那种情况下改动
        后面的字节不会被察觉。
        """
        baseline = protocol.encode(_snapshot())

        for index in range(protocol.PACKET_SIZE - 2):
            corrupted = bytearray(baseline)
            corrupted[index] ^= 0xFF
            with self.subTest(byte=index):
                with self.assertRaises(ValueError):
                    protocol.decode(bytes(corrupted))

    def test_wrong_magic_rejected(self) -> None:
        """误连的设备写进来的垃圾数据要能被快速否掉。"""
        packet = bytearray(protocol.encode(_snapshot()))
        packet[0:2] = struct.pack("<H", 0x0000)
        packet[-2:] = struct.pack(
            "<H", protocol.crc16_ccitt(bytes(packet[:-2]))
        )

        with self.assertRaises(ValueError):
            protocol.decode(bytes(packet))

    def test_wrong_version_rejected(self) -> None:
        """升级协议后旧固件必须拒收新包,而不是错位解读。"""
        packet = bytearray(protocol.encode(_snapshot()))
        packet[2] = protocol.PROTOCOL_VERSION + 1
        packet[-2:] = struct.pack(
            "<H", protocol.crc16_ccitt(bytes(packet[:-2]))
        )

        with self.assertRaises(ValueError):
            protocol.decode(bytes(packet))

    def test_short_packet_rejected(self) -> None:
        """截断的包不能进解码路径,固件侧靠长度检查避免越界读。"""
        with self.assertRaises(ValueError):
            protocol.decode(protocol.encode(_snapshot())[:-1])

    def test_oversized_amount_clamped_not_wrapped(self) -> None:
        """超出 u32 的金额应夹住,不能回绕成一个小数字。"""
        packet = protocol.encode(_snapshot(today_cost_usd=1e12))

        today_milli = struct.unpack_from("<I", packet, 4)[0]

        self.assertEqual(today_milli, 0xFFFFFFFF)

    def test_negative_amount_clamped_to_zero(self) -> None:
        """负值夹到 0。u32 打包负数会直接抛 struct.error,连不上就查不出原因。"""
        packet = protocol.encode(_snapshot(today_cost_usd=-5.0))

        today_milli = struct.unpack_from("<I", packet, 4)[0]

        self.assertEqual(today_milli, 0)

    def test_zero_values_round_trip(self) -> None:
        """全零是合法状态(比如当天还没有用量),不能被当成无效包。"""
        decoded = protocol.decode(protocol.encode(_snapshot(
            today_cost_usd=0.0, today_tokens=0,
            week_cost_usd=0.0, month_cost_usd=0.0, total_cost_usd=0.0,
        )))

        self.assertEqual(decoded.today_cost_usd, 0.0)
        self.assertEqual(decoded.today_tokens, 0)
        self.assertEqual(decoded.total_cost_usd, 0.0)

    def test_crc16_ccitt_known_vector(self) -> None:
        """CRC-16/CCITT-FALSE 对 "123456789" 的标准结果是 0x29B1。

        锁住算法本身,而不只是自洽:如果两边都用了同一个错误变种,round-trip
        测试是发现不了的。
        """
        self.assertEqual(protocol.crc16_ccitt(b"123456789"), 0x29B1)


if __name__ == "__main__":
    unittest.main()
