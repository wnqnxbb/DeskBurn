/**
 * @file link_ble.h
 * @brief BLE 从机：接收 Mac 推送的聚合数据，并把最后一次有效值存进 NVS。
 *
 * 用 NimBLE 而不是 Arduino 自带的 Bluedroid：后者要多占约 100KB RAM 和大量
 * flash，而这里只需要一个 writable characteristic。
 *
 * Mac 作 central 主动连接。设备侧只管持续广播和等写入，不主动发起任何连接，
 * 逻辑简单，断线后靠 Mac 重连即可。
 */

#pragma once

#include <NimBLEDevice.h>
#include <Preferences.h>

#include "link_protocol.h"

namespace Link {

/// 超过这个时长没收到有效包就认为离线。取推送周期的 3 倍，容忍偶发丢包。
constexpr uint32_t kStaleTimeoutMs = 90000;

/// 最后一次有效数据的 NVS 命名空间与键名。
constexpr char kNvsNamespace[] = "tokendisp";
constexpr char kNvsKeyPacket[] = "last";

/**
 * @brief 收包状态。
 *
 * 只有 BLE 回调会写、只有主循环会读，且 32 位对齐字段在 C3 上是原子的，
 * 因此不需要额外加锁。volatile 是必要的：两者跑在不同任务上。
 *
 * Token 存的是千 token（与线格式一致）。这里不还原成原始 token 数，就是为了
 * 让每个字段都留在 32 位内 —— 换成 uint64_t 会让上面的原子性假设失效。
 */
struct State {
  volatile bool hasData = false;
  volatile uint32_t lastPacketMs = 0;
  volatile uint32_t todayMilliUsd = 0;
  volatile uint32_t todayKiloTokens = 0;
  volatile uint32_t weekMilliUsd = 0;
  volatile uint32_t weekKiloTokens = 0;
  volatile uint32_t monthMilliUsd = 0;
  volatile uint32_t monthKiloTokens = 0;
  volatile uint32_t totalMilliUsd = 0;
  volatile uint32_t totalKiloTokens = 0;
};

inline State g_state;
inline Preferences g_prefs;

/**
 * @brief 把当前值写入 NVS。
 *
 * 只在数值真的变化时调用。NVS 是 flash，写入次数有限，每 30 秒无条件写一次
 * 会显著缩短寿命。
 */
inline void persist() {
  UsagePacket saved{};
  saved.magic = kMagic;
  saved.version = kProtocolVersion;
  saved.todayMilliUsd = g_state.todayMilliUsd;
  saved.todayKiloTokens = g_state.todayKiloTokens;
  saved.weekMilliUsd = g_state.weekMilliUsd;
  saved.weekKiloTokens = g_state.weekKiloTokens;
  saved.monthMilliUsd = g_state.monthMilliUsd;
  saved.monthKiloTokens = g_state.monthKiloTokens;
  saved.totalMilliUsd = g_state.totalMilliUsd;
  saved.totalKiloTokens = g_state.totalKiloTokens;

  g_prefs.putBytes(kNvsKeyPacket, &saved, sizeof(saved));
}

/**
 * @brief 从 NVS 恢复上次的值。
 *
 * 上电后先显示历史数据加 OFFLINE，比显示 $0.00 更有意义 —— 后者看起来像
 * 「今天没用过」，而事实是「还没连上」。
 *
 * @return 是否恢复出了有效数据。
 */
inline bool restore() {
  UsagePacket saved{};
  // 长度和版本一起校验，所以旧固件留下的短记录会被直接判为无效：首次刷上 v3
  // 后开机是干净的 $0.00 + OFFLINE，而不是把旧字节按新布局错位读出来。
  // 下一轮推送就会覆盖成真实值。
  const size_t read = g_prefs.getBytes(kNvsKeyPacket, &saved, sizeof(saved));
  if (read != sizeof(saved) || saved.magic != kMagic ||
      saved.version != kProtocolVersion) {
    return false;
  }

  g_state.todayMilliUsd = saved.todayMilliUsd;
  g_state.todayKiloTokens = saved.todayKiloTokens;
  g_state.weekMilliUsd = saved.weekMilliUsd;
  g_state.weekKiloTokens = saved.weekKiloTokens;
  g_state.monthMilliUsd = saved.monthMilliUsd;
  g_state.monthKiloTokens = saved.monthKiloTokens;
  g_state.totalMilliUsd = saved.totalMilliUsd;
  g_state.totalKiloTokens = saved.totalKiloTokens;
  // 不设 hasData：恢复出来的是历史值，还没有建立过连接，应显示 OFFLINE。
  return true;
}

/// 处理 Mac 的写入。
class UsageCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* characteristic) override {
    const std::string value = characteristic->getValue();

    UsagePacket packet{};
    if (!decode(reinterpret_cast<const uint8_t*>(value.data()), value.size(),
                &packet)) {
      // 校验不过就整包丢弃，保留上一次的有效值。射程内任何设备都能往这里写，
      // 不能让一个畸形包把屏幕上的数字冲掉。
      Serial.printf("[ble] rejected %u bytes\n",
                    static_cast<unsigned>(value.size()));
      return;
    }

    const bool changed = packet.todayMilliUsd != g_state.todayMilliUsd ||
                         packet.todayKiloTokens != g_state.todayKiloTokens ||
                         packet.weekMilliUsd != g_state.weekMilliUsd ||
                         packet.weekKiloTokens != g_state.weekKiloTokens ||
                         packet.monthMilliUsd != g_state.monthMilliUsd ||
                         packet.monthKiloTokens != g_state.monthKiloTokens ||
                         packet.totalMilliUsd != g_state.totalMilliUsd ||
                         packet.totalKiloTokens != g_state.totalKiloTokens;

    g_state.todayMilliUsd = packet.todayMilliUsd;
    g_state.todayKiloTokens = packet.todayKiloTokens;
    g_state.weekMilliUsd = packet.weekMilliUsd;
    g_state.weekKiloTokens = packet.weekKiloTokens;
    g_state.monthMilliUsd = packet.monthMilliUsd;
    g_state.monthKiloTokens = packet.monthKiloTokens;
    g_state.totalMilliUsd = packet.totalMilliUsd;
    g_state.totalKiloTokens = packet.totalKiloTokens;
    g_state.lastPacketMs = millis();
    g_state.hasData = true;

    if (changed) {
      persist();
    }

    // 收包日志是排障的主要依据：Mac 侧只知道「写成功」，无法判断设备是否
    // 真的收下了，两边日志对照才能定位问题。
    Serial.printf("[ble] accepted today=%u.%03u ktokens=%u%s\n",
                  packet.todayMilliUsd / 1000, packet.todayMilliUsd % 1000,
                  packet.todayKiloTokens, changed ? " (persisted)" : "");
  }
};

/// 断线后必须重新开广播，否则 Mac 再也找不到设备。
class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer*) override {
    Serial.println("[ble] central connected");
  }

  void onDisconnect(NimBLEServer* server) override {
    Serial.println("[ble] central disconnected, advertising again");
    server->startAdvertising();
  }
};

inline UsageCallbacks g_usageCallbacks;
inline ServerCallbacks g_serverCallbacks;

/**
 * @brief 启动 BLE 从机并恢复上次的数据。
 *
 * 安全上的取舍：这里没有开配对加密，射程内任何设备都能写入。传输内容只是几个
 * 聚合数字，写入的唯一后果是屏幕显示错误数值，且下一轮推送就会覆盖回来。
 * 要收紧的话可以开 NimBLEDevice::setSecurityPasskey 加静态 passkey 绑定，
 * 代价是首次配对需要人工确认。
 */
inline void begin() {
  g_prefs.begin(kNvsNamespace, /*readOnly=*/false);
  if (restore()) {
    Serial.println("[ble] restored last values from nvs");
  }

  NimBLEDevice::init(kDeviceName);
  // 包只有 42 字节，用最低发射功率省电即可。
  NimBLEDevice::setPower(ESP_PWR_LVL_P3);

  NimBLEServer* server = NimBLEDevice::createServer();
  server->setCallbacks(&g_serverCallbacks);

  NimBLEService* service = server->createService(kServiceUuid);
  NimBLECharacteristic* usage = service->createCharacteristic(
      kUsageCharUuid, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  usage->setCallbacks(&g_usageCallbacks);
  service->start();

  NimBLEAdvertising* advertising = NimBLEDevice::getAdvertising();
  advertising->addServiceUUID(kServiceUuid);
  // 带上名字，Mac 端才能用 find_device_by_name 找到。
  advertising->setScanResponse(true);
  advertising->start();

  Serial.printf("[ble] advertising as %s\n", kDeviceName);
}

/// 距最后一个有效包是否已超时。
inline bool isStale() {
  if (!g_state.hasData) {
    return true;
  }
  return (millis() - g_state.lastPacketMs) > kStaleTimeoutMs;
}

}  // namespace Link
