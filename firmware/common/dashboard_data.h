/**
 * @file dashboard_data.h
 * @brief 所有屏幕展示版本共享的仪表盘数据模型。
 *
 * BLE 传输和页面渲染通过这个结构解耦。新增页面只能读取这些展示字段，不需要
 * 了解 CC Switch 数据库、BLE 包字节序或 NVS 的保存方式。
 */

#pragma once

#include <stdint.h>

/// 一次刷新所需的全部展示数据。
struct DashboardData {
  float todayCostUsd;
  uint64_t todayTokens;
  float weekCostUsd;
  uint64_t weekTokens;
  float monthCostUsd;
  uint64_t monthTokens;
  float totalCostUsd;
  uint64_t totalTokens;
  bool online;
};
