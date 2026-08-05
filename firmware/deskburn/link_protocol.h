/**
 * @file link_protocol.h
 * @brief Mac 与本机之间的线格式。
 *
 * 必须与 tools/ccswitch_agent/protocol.py 保持字节级一致。改动任何字段都要
 * 同步改那个文件，并升 kProtocolVersion。
 *
 * 固定 42 字节小端包，没有变长字段，因此可以直接 memcpy 到结构体，不需要
 * 解析器，也不会因为畸形输入导致越界。
 */

#pragma once

#include <stdint.h>
#include <string.h>

namespace Link {

// 随机生成的私有 UUID，不与标准 GATT 服务冲突。
constexpr char kServiceUuid[] = "9d4e01ea-b59c-40a9-b128-c96b9989b633";
constexpr char kUsageCharUuid[] = "d00f3234-c721-4f01-bcff-a01e663303ce";

// 广播名前缀。完整名称在启动时拼成 DeskBurn-<12 位芯片 ID>，同一份固件烧到
// 多块板子也不会同名。Mac 端保存这个稳定名称，不保存 macOS 随机化的外设地址。
constexpr char kDeviceNamePrefix[] = "DeskBurn-";

constexpr uint16_t kMagic = 0xCC57;

/// v3 给本周 / 本月 / 总计各补了一个 Token 字段，并把全部 Token 改成千 token
/// 计量。版本号不符的包整个丢弃，因此新旧两端混用时屏幕显示 OFFLINE，而不是
/// 把字段错位读成乱数。
constexpr uint8_t kProtocolVersion = 3;

/// 金额以千分之一美元的整数传输，Token 以千 token 的整数传输，两者都避开了
/// 设备端的浮点运算和字节序问题。
///
/// Token 不传原始数：总计实测已到 18 亿并每月增长约 15 亿，u32 的 42.9 亿撑不
/// 了多久；而换成 u64 会让下面 State 里「32 位读写原子」的假设失效，BLE 回调
/// 写到一半被主循环读走就会渲染出乱数。屏幕最细只显示到 0.01M，千位精度够用。
struct __attribute__((packed)) UsagePacket {
  uint16_t magic;
  uint8_t version;
  uint8_t flags;
  uint32_t todayMilliUsd;
  uint32_t todayKiloTokens;
  uint32_t weekMilliUsd;
  uint32_t weekKiloTokens;
  uint32_t monthMilliUsd;
  uint32_t monthKiloTokens;
  uint32_t totalMilliUsd;
  uint32_t totalKiloTokens;
  uint32_t epoch;
  uint16_t crc;
};

constexpr size_t kPacketSize = 42;
static_assert(sizeof(UsagePacket) == kPacketSize,
              "包结构与 protocol.py 不一致");

/**
 * @brief CRC-16/CCITT-FALSE。
 *
 * 用位运算而不是查表：包只有 40 字节，算得快慢无关紧要，省下 512 字节的
 * 表更值。
 */
inline uint16_t crc16Ccitt(const uint8_t* data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint16_t>(data[i]) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                           : static_cast<uint16_t>(crc << 1);
    }
  }
  return crc;
}

/**
 * @brief 校验并解出一个包。
 *
 * 任何一项不符就整包丢弃，让调用方保留上一次的有效值。宁可屏幕上的数字旧一轮，
 * 也不要显示一个被截断或损坏的数。
 *
 * @param data 收到的原始字节。
 * @param length 字节数，必须正好等于 kPacketSize。
 * @param out 校验通过时写入解出的包。
 * @return 是否为一个有效包。
 */
inline bool decode(const uint8_t* data, size_t length, UsagePacket* out) {
  if (length != kPacketSize) {
    return false;
  }

  UsagePacket packet;
  memcpy(&packet, data, kPacketSize);

  if (packet.magic != kMagic || packet.version != kProtocolVersion) {
    return false;
  }
  // CRC 覆盖除自身两字节以外的全部内容。
  if (packet.crc != crc16Ccitt(data, kPacketSize - sizeof(uint16_t))) {
    return false;
  }

  *out = packet;
  return true;
}

}  // namespace Link
