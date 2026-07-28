"""Mac 与 ESP32 之间的线格式。

固定 42 字节小端二进制包。不用 JSON 是为了省掉设备端的解析器：C 侧
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

# v3 给本周 / 本月 / 总计各补了一个 Token 字段，并把全部 Token 改成「千 token」
# 计量（见 _to_kilo）。旧固件会因版本号不符整包拒收，屏幕停在 OFFLINE 而不是
# 显示错位的数字 —— 加字段必须升版本号，就是为了这个。
PROTOCOL_VERSION = 3

# < 小端，无填充。ESP32-C3 是小端，显式指定避免两边对齐规则不一致。
#   H magic | B version | B flags
#   I today_milli | I today_kilo_tokens
#   I week_milli  | I week_kilo_tokens
#   I month_milli | I month_kilo_tokens
#   I total_milli | I total_kilo_tokens
#   I epoch | H crc
_PACKET_FORMAT = "<HBBIIIIIIIIIH"
PACKET_SIZE = struct.calcsize(_PACKET_FORMAT)
assert PACKET_SIZE == 42, PACKET_SIZE

# CRC 覆盖除自身以外的全部字节。
_CRC_OFFSET = PACKET_SIZE - 2

FLAG_NONE = 0x00

# 金额用「千分之一美元」的整数传输，避开浮点。u32 上限约 429 万美元。
_MILLI_MAX = 0xFFFFFFFF

# Token 用「千 token」的整数传输，同样是为了塞进 u32。
#
# 不能直接传原始 token 数：总计实测已到 18 亿并以每月约 15 亿的速度增长，u32 的
# 42.9 亿撑不到年底，溢出后会被夹住，屏幕上永远停在 4.29B —— 一个看着合理却
# 不再变化的数，比断连更难发现。
#
# 也没有改成 u64：设备端 State 里的字段是 volatile uint32_t，靠「32 位读写在
# C3 上是原子的」省掉了锁；换成 64 位后 BLE 回调写到一半被主循环读走，会渲染出
# 一个高低半字来自不同包的乱数。屏幕最细只显示到 0.01M（即 10K），千位精度绰绰
# 有余，所以降精度比加宽度划算。
_KILO_MAX = 0xFFFFFFFF


def _to_milli(usd: float) -> int:
    """美元转千分之一美元整数，并夹到 u32 范围内。

    夹取而不是抛异常：数值溢出时屏幕显示一个偏小的数字，比整条链路断掉要好。
    """
    return max(0, min(int(round(usd * 1000)), _MILLI_MAX))


def _to_kilo(tokens: int) -> int:
    """Token 数转千 token 整数，并夹到 u32 范围内。

    四舍五入而不是截断：几百个 token 的零头截成 0 会让「今天用过一点」看起来
    像「今天没用过」。
    """
    return max(0, min((int(tokens) + 500) // 1000, _KILO_MAX))


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE。

    位运算实现而不是查表：包只有 40 字节，省下来的时间无关紧要，而设备端
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
        _to_kilo(snapshot.today_tokens),
        _to_milli(snapshot.week_cost_usd),
        _to_kilo(snapshot.week_tokens),
        _to_milli(snapshot.month_cost_usd),
        _to_kilo(snapshot.month_tokens),
        _to_milli(snapshot.total_cost_usd),
        _to_kilo(snapshot.total_tokens),
        snapshot.updated_at,
    )
    return body + struct.pack("<H", crc16_ccitt(body))


def decode(packet: bytes) -> UsageSnapshot:
    """解码一个包。仅供测试与调试使用，设备端才是真正的消费方。"""
    if len(packet) != PACKET_SIZE:
        raise ValueError(f"expected {PACKET_SIZE} bytes, got {len(packet)}")

    expected = crc16_ccitt(packet[:_CRC_OFFSET])
    (magic, version, _flags, today_milli, today_kilo, week_milli, week_kilo,
     month_milli, month_kilo, total_milli, total_kilo, epoch,
     crc) = struct.unpack(_PACKET_FORMAT, packet)

    if magic != MAGIC:
        raise ValueError(f"bad magic: 0x{magic:04X}")
    if crc != expected:
        raise ValueError(f"bad crc: 0x{crc:04X} != 0x{expected:04X}")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported version: {version}")

    return UsageSnapshot(
        today_cost_usd=today_milli / 1000.0,
        today_tokens=today_kilo * 1000,
        week_cost_usd=week_milli / 1000.0,
        week_tokens=week_kilo * 1000,
        month_cost_usd=month_milli / 1000.0,
        month_tokens=month_kilo * 1000,
        total_cost_usd=total_milli / 1000.0,
        total_tokens=total_kilo * 1000,
        updated_at=epoch,
    )


def fake_snapshot() -> UsageSnapshot:
    """不碰数据库的固定样本，用于单独验证链路。

    总计特意取了超过 10 亿的值：屏幕上只有它会走 B 单位分支，能顺带验证
    「M 放不下自动切 B」这条格式化规则在真机上生效。
    """
    return UsageSnapshot(
        today_cost_usd=12.48,
        today_tokens=9_640_000,
        week_cost_usd=63.21,
        week_tokens=147_250_000,
        month_cost_usd=218.90,
        month_tokens=843_600_000,
        total_cost_usd=1_204.55,
        total_tokens=1_832_640_000,
        updated_at=int(time.time()),
    )
