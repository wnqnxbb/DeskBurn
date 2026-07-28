"""Mac 与 ESP32 之间的线格式。

固定 30 字节小端二进制包。不用 JSON 是为了省掉设备端的解析器：C 侧
memcpy 到结构体就能用，没有动态内存，也不会因为一个畸形字符串把固件搞崩。

改动这里必须同步改 firmware/deskburn/link_protocol.h，两边的字段顺序
和偏移量必须一致。改了包结构就要升 PROTOCOL_VERSION。
"""

from __future__ import annotations

import struct
import time

from .usage import UsageSnapshot

# 随机生成的私有 UUID，不与任何标准 GATT 服务冲突。
SERVICE_UUID = "9d4e01ea-b59c-40a9-b128-c96b9989b633"
USAGE_CHAR_UUID = "d00f3234-c721-4f01-bcff-a01e663303ce"

# 广播名。Mac 端靠它筛选设备，不依赖 MAC 地址 —— macOS 上拿到的是随机化的
# UUID 而不是真实 MAC，换机器或重装系统后地址会变。
DEVICE_NAME = "DeskBurn"

# 包头魔数，用来快速否掉误连设备写进来的垃圾数据。
MAGIC = 0xCC57

# v2 在 month 之后插入了 total_milli。旧固件会因版本号不符整包拒收，屏幕停在
# OFFLINE 而不是显示错位的数字 —— 加字段必须升版本号，就是为了这个。
PROTOCOL_VERSION = 2

# < 小端，无填充。ESP32-C3 是小端，显式指定避免两边对齐规则不一致。
#   H magic | B version | B flags | I today_milli | I today_tokens
#   I week_milli | I month_milli | I total_milli | I epoch | H crc
_PACKET_FORMAT = "<HBBIIIIIIH"
PACKET_SIZE = struct.calcsize(_PACKET_FORMAT)
assert PACKET_SIZE == 30, PACKET_SIZE

# CRC 覆盖除自身以外的全部字节。
_CRC_OFFSET = PACKET_SIZE - 2

FLAG_NONE = 0x00

# 金额用「千分之一美元」的整数传输，避开浮点。u32 上限约 429 万美元，
# 而 token 数按当前用量每天 1 亿量级，u32 的 42.9 亿也够用。
_MILLI_MAX = 0xFFFFFFFF


def _to_milli(usd: float) -> int:
    """美元转千分之一美元整数，并夹到 u32 范围内。

    夹取而不是抛异常：数值溢出时屏幕显示一个偏小的数字，比整条链路断掉要好。
    """
    return max(0, min(int(round(usd * 1000)), _MILLI_MAX))


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE。

    位运算实现而不是查表：包只有 24 字节，省下来的时间无关紧要，而设备端
    少一张 512 字节的表更划算。
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode(snapshot: UsageSnapshot, *, flags: int = FLAG_NONE) -> bytes:
    """把一次采样编码成设备端可直接消费的包。"""
    body = struct.pack(
        _PACKET_FORMAT[:-1],
        MAGIC,
        PROTOCOL_VERSION,
        flags,
        _to_milli(snapshot.today_cost_usd),
        max(0, min(snapshot.today_tokens, _MILLI_MAX)),
        _to_milli(snapshot.week_cost_usd),
        _to_milli(snapshot.month_cost_usd),
        _to_milli(snapshot.total_cost_usd),
        snapshot.updated_at,
    )
    return body + struct.pack("<H", crc16_ccitt(body))


def decode(packet: bytes) -> UsageSnapshot:
    """解码一个包。仅供测试与调试使用，设备端才是真正的消费方。"""
    if len(packet) != PACKET_SIZE:
        raise ValueError(f"expected {PACKET_SIZE} bytes, got {len(packet)}")

    expected = crc16_ccitt(packet[:_CRC_OFFSET])
    (magic, version, _flags, today_milli, today_tokens, week_milli,
     month_milli, total_milli, epoch, crc) = struct.unpack(_PACKET_FORMAT, packet)

    if magic != MAGIC:
        raise ValueError(f"bad magic: 0x{magic:04X}")
    if crc != expected:
        raise ValueError(f"bad crc: 0x{crc:04X} != 0x{expected:04X}")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported version: {version}")

    return UsageSnapshot(
        today_cost_usd=today_milli / 1000.0,
        today_tokens=today_tokens,
        week_cost_usd=week_milli / 1000.0,
        month_cost_usd=month_milli / 1000.0,
        total_cost_usd=total_milli / 1000.0,
        updated_at=epoch,
    )


def fake_snapshot() -> UsageSnapshot:
    """不碰数据库的固定样本，用于单独验证链路。"""
    return UsageSnapshot(
        today_cost_usd=12.48,
        today_tokens=9_640_000,
        week_cost_usd=63.21,
        month_cost_usd=218.90,
        total_cost_usd=1_204.55,
        updated_at=int(time.time()),
    )
