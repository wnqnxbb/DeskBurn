"""用真实固件头文件验证跨语言一致性。

protocol.py 和 link_protocol.h 各自实现了同一套线格式。只测 Python 侧的
round-trip 无法发现两边不一致 —— 那种情况下 Mac 编出来的包设备端全都拒收,
表现为「连上了但屏幕不刷新」,很难定位。

做法是用宿主机的编译器直接编译 link_protocol.h,让它解 Python 编出的包。
编译器不可用时跳过,不让本地缺工具阻塞其余测试。
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ccswitch_agent import protocol  # noqa: E402
from ccswitch_agent.usage import UsageSnapshot  # noqa: E402

_HEADER_DIR = (Path(__file__).resolve().parents[2]
               / "firmware" / "deskburn")

# 头文件为固件写的,包含 Arduino 和 NimBLE 的依赖。这里只取协议部分:
# link_protocol.h 本身只依赖 stdint.h 和 string.h,可以在宿主机上直接编译。
_HARNESS = r"""
#include <cstdio>
#include "link_protocol.h"

int main(int argc, char** argv) {
  if (argc != 2) return 2;

  // 入参是十六进制字符串,避免二进制数据经管道时被换行符或编码破坏。
  const char* hex = argv[1];
  uint8_t buffer[64];
  size_t length = 0;
  while (hex[0] && hex[1] && length < sizeof(buffer)) {
    unsigned byte = 0;
    if (sscanf(hex, "%2x", &byte) != 1) return 2;
    buffer[length++] = static_cast<uint8_t>(byte);
    hex += 2;
  }

  Link::UsagePacket packet{};
  if (!Link::decode(buffer, length, &packet)) {
    printf("{\"accepted\": false}\n");
    return 0;
  }

  printf("{\"accepted\": true, \"today_milli\": %u, \"today_kilo\": %u,"
         " \"week_milli\": %u, \"week_kilo\": %u,"
         " \"month_milli\": %u, \"month_kilo\": %u,"
         " \"total_milli\": %u, \"total_kilo\": %u,"
         " \"epoch\": %u, \"version\": %u,"
         " \"struct_size\": %u, \"crc_check\": %u}\n",
         packet.todayMilliUsd, packet.todayKiloTokens,
         packet.weekMilliUsd, packet.weekKiloTokens,
         packet.monthMilliUsd, packet.monthKiloTokens,
         packet.totalMilliUsd, packet.totalKiloTokens,
         packet.epoch, packet.version,
         static_cast<unsigned>(sizeof(Link::UsagePacket)),
         Link::crc16Ccitt(reinterpret_cast<const uint8_t*>("123456789"), 9));
  return 0;
}
"""


def _compiler() -> str | None:
    for candidate in ("c++", "clang++", "g++"):
        if shutil.which(candidate):
            return candidate
    return None


@unittest.skipIf(_compiler() is None, "no host C++ compiler available")
class ProtocolParityTest(unittest.TestCase):
    """让固件的解码器去解 Python 编出来的包。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        workdir = Path(cls._tmp.name)
        source = workdir / "harness.cpp"
        source.write_text(_HARNESS)
        cls._binary = workdir / "harness"

        result = subprocess.run(
            [_compiler(), "-std=c++17", "-I", str(_HEADER_DIR),
             str(source), "-o", str(cls._binary)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"failed to compile link_protocol.h harness:\n{result.stderr}"
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _decode_in_cpp(self, packet: bytes) -> dict:
        result = subprocess.run(
            [str(self._binary), packet.hex()],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    def test_struct_size_matches_python(self) -> None:
        """packed 属性生效,C 结构体没有被插入对齐填充。"""
        decoded = self._decode_in_cpp(protocol.encode(protocol.fake_snapshot()))

        self.assertEqual(decoded["struct_size"], protocol.PACKET_SIZE)

    def test_crc_implementations_agree(self) -> None:
        """两边的 CRC 是同一个变种。"""
        decoded = self._decode_in_cpp(protocol.encode(protocol.fake_snapshot()))

        self.assertEqual(decoded["crc_check"], 0x29B1)
        self.assertEqual(protocol.crc16_ccitt(b"123456789"), 0x29B1)

    def test_firmware_accepts_python_packet(self) -> None:
        """字段值逐个核对,能抓出字节序或偏移量不一致。

        总计特意取了比本月大一个数量级的值:如果两边字段顺序不一致,month 和
        total 会互换,而两个都是「合理的金额」,只看屏幕分辨不出来。
        """
        snapshot = UsageSnapshot(
            today_cost_usd=137.68, today_tokens=105_898_081,
            week_cost_usd=137.68, week_tokens=147_245_847,
            month_cost_usd=1651.86, month_tokens=1_465_673_026,
            total_cost_usd=3006.39, total_tokens=12_832_641_256,
            updated_at=1_785_147_325,
        )

        decoded = self._decode_in_cpp(protocol.encode(snapshot))

        self.assertTrue(decoded["accepted"])
        self.assertEqual(decoded["today_milli"], 137_680)
        self.assertEqual(decoded["today_kilo"], 105_898)
        self.assertEqual(decoded["week_milli"], 137_680)
        self.assertEqual(decoded["week_kilo"], 147_246)
        self.assertEqual(decoded["month_milli"], 1_651_860)
        self.assertEqual(decoded["month_kilo"], 1_465_673)
        self.assertEqual(decoded["total_milli"], 3_006_390)
        # 原始值超过 u32,只有千 token 计量才能原样送到设备端。
        self.assertEqual(decoded["total_kilo"], 12_832_641)
        self.assertEqual(decoded["epoch"], 1_785_147_325)

    def test_firmware_rejects_v2_packet(self) -> None:
        """v2 的 30 字节包必须被拒收。

        加字段时升了版本号,就是为了让旧包在新固件上整包丢弃。万一漏升,v2 包
        会因长度不符被拒,但同长度的畸形包就可能被按新布局错位读出来。
        """
        snapshot = protocol.fake_snapshot()
        body = struct.pack(
            "<HBBIIIIII", protocol.MAGIC, 2, protocol.FLAG_NONE,
            137_680, 105_898_081, 137_680, 1_651_860, 3_006_390,
            snapshot.updated_at,
        )
        v2_packet = body + struct.pack("<H", protocol.crc16_ccitt(body))
        self.assertEqual(len(v2_packet), 30)

        self.assertFalse(self._decode_in_cpp(v2_packet)["accepted"])

    def test_firmware_reports_expected_version(self) -> None:
        """两边的版本号常量必须一致,否则设备会拒收全部包。"""
        decoded = self._decode_in_cpp(protocol.encode(protocol.fake_snapshot()))

        self.assertEqual(decoded["version"], protocol.PROTOCOL_VERSION)

    def test_firmware_rejects_corrupted_packet(self) -> None:
        packet = bytearray(protocol.encode(protocol.fake_snapshot()))
        packet[8] ^= 0x01

        self.assertFalse(self._decode_in_cpp(bytes(packet))["accepted"])

    def test_firmware_rejects_truncated_packet(self) -> None:
        """长度检查必须在 memcpy 之前,否则会读到缓冲区外。"""
        packet = protocol.encode(protocol.fake_snapshot())[:-1]

        self.assertFalse(self._decode_in_cpp(packet)["accepted"])

    def test_firmware_rejects_empty_write(self) -> None:
        """空写入是 BLE 上真实会出现的情况,不能让它进解码路径。"""
        self.assertFalse(self._decode_in_cpp(b"")["accepted"])

    def test_firmware_rejects_wrong_magic(self) -> None:
        packet = bytearray(protocol.encode(protocol.fake_snapshot()))
        packet[0:2] = (0x00, 0x00)
        crc = protocol.crc16_ccitt(bytes(packet[:-2]))
        packet[-2:] = crc.to_bytes(2, "little")

        self.assertFalse(self._decode_in_cpp(bytes(packet))["accepted"])


if __name__ == "__main__":
    unittest.main()
