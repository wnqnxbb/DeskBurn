/**
 * @file deskburn_buddy.cpp
 * @brief DeskBurn 的 Midnight Buddy（深色可爱陪伴型）展示版本。
 *
 * 页面按 320x240 横屏设计，使用非对称左右分屏：左侧展示今日消耗和小精灵，
 * 右侧用三段纵向足迹展示本周、本月、总计。它与 Classic、Swiss Poster 复用
 * 完全相同的 BLE 协议、Mac 采集端和 NVS 数据，烧录后无需重新配置。
 */

#include <Arduino.h>
#include <TFT_eSPI.h>

#include "../common/dashboard_data.h"
#include "../deskburn/link_ble.h"
#include "buddy_assets.h"

namespace Hardware {
// 背光与屏幕 SPI 引脚都沿用 platformio.ini 中实机验证过的配置。
constexpr int kBacklightGate = 6;
}  // namespace Hardware

namespace Layout {
// 左侧是开放的今日主舞台；右侧从 x=194 开始，三个周期段各高 80px。
constexpr int16_t kRightPanelX = 194;
constexpr int16_t kRightPanelWidth = 126;
constexpr int16_t kPanelHeight = 80;

constexpr int16_t kBrandX = 10;
constexpr int16_t kBrandY = 8;
constexpr int16_t kStatusFieldX = 108;
constexpr int16_t kStatusFieldY = 6;
constexpr int16_t kStatusFieldWidth = 77;
constexpr int16_t kStatusFieldHeight = 18;
constexpr int16_t kStatusDotCenterX = 116;
constexpr int16_t kStatusDotCenterY = 14;
constexpr int16_t kStatusTextX = 125;
constexpr int16_t kStatusTextY = 8;

constexpr int16_t kTodayLabelX = 12;
constexpr int16_t kTodayLabelY = 44;
constexpr int16_t kMascotX = 69;
constexpr int16_t kMascotY = 54;

constexpr int16_t kTodayCostFieldX = 8;
constexpr int16_t kTodayCostFieldY = 134;
constexpr int16_t kTodayCostFieldWidth = 178;
constexpr int16_t kTodayCostFieldHeight = 50;
constexpr int16_t kTodayCostTextX = 12;
constexpr int16_t kTodayCostTextY = 137;

constexpr int16_t kTodayTokensFieldX = 8;
constexpr int16_t kTodayTokensFieldY = 190;
constexpr int16_t kTodayTokensFieldWidth = 178;
constexpr int16_t kTodayTokensFieldHeight = 20;
constexpr int16_t kTodayTokensTextX = 18;
constexpr int16_t kTodayTokensTextY = 193;

constexpr int16_t kTrackX = 190;
constexpr int16_t kPeriodLabelX = 205;
constexpr int16_t kPeriodLabelOffsetY = 9;
constexpr int16_t kPeriodCostFieldX = 201;
constexpr int16_t kPeriodCostFieldWidth = 116;
constexpr int16_t kPeriodCostFieldHeight = 32;
constexpr int16_t kPeriodCostTextX = 204;
constexpr int16_t kPeriodCostOffsetY = 30;
constexpr int16_t kPeriodTokensFieldX = 201;
constexpr int16_t kPeriodTokensFieldWidth = 116;
constexpr int16_t kPeriodTokensFieldHeight = 18;
constexpr int16_t kPeriodTokensTextX = 205;
constexpr int16_t kPeriodTokensOffsetY = 61;
}  // namespace Layout

namespace {

using BuddyAssets::AlphaBitmap;
using BuddyAssets::AlphaFont;
using BuddyAssets::AlphaGlyph;
using BuddyAssets::RgbBitmap;

TFT_eSPI display = TFT_eSPI();

char g_todayCostCurrent[16] = "";
char g_todayTokensCurrent[24] = "";
char g_periodCostsCurrent[3][16] = {{""}, {""}, {""}};
char g_periodTokensCurrent[3][12] = {{""}, {""}, {""}};
bool g_statusDrawn = false;
bool g_statusOnline = false;

/**
 * @brief 按 alpha 混合两个 RGB565 颜色。
 *
 * @param alpha 前景不透明度，0 为背景，255 为前景。
 * @param foreground 前景颜色。
 * @param background 当前字段的背景颜色。
 * @return 混合后的 RGB565 像素。
 */
uint16_t blendRgb565(uint8_t alpha,
                     uint16_t foreground,
                     uint16_t background) {
  const uint16_t inverse = 255 - alpha;
  const uint16_t red =
      ((foreground >> 11) & 0x1F) * alpha +
      ((background >> 11) & 0x1F) * inverse;
  const uint16_t green =
      ((foreground >> 5) & 0x3F) * alpha +
      ((background >> 5) & 0x3F) * inverse;
  const uint16_t blue =
      (foreground & 0x1F) * alpha + (background & 0x1F) * inverse;
  return static_cast<uint16_t>(((red / 255) << 11) |
                               ((green / 255) << 5) | (blue / 255));
}

/**
 * @brief 在指定左上角绘制一张抗锯齿 alpha 蒙版。
 *
 * @param bitmap 固件内的 alpha 蒙版。
 * @param left 左上角 X 坐标。
 * @param top 左上角 Y 坐标。
 * @param foreground 文字或图形颜色。
 * @param background 当前区域背景；右侧三段会传入不同面板色。
 */
void drawAlphaBitmap(const AlphaBitmap& bitmap,
                     int16_t left,
                     int16_t top,
                     uint16_t foreground,
                     uint16_t background) {
  constexpr uint16_t kMaxRowPixels = 128;
  if (bitmap.alpha == nullptr || bitmap.width > kMaxRowPixels) {
    return;
  }

  static uint16_t row[kMaxRowPixels];
  display.setSwapBytes(true);
  for (uint16_t y = 0; y < bitmap.height; ++y) {
    const uint8_t* source =
        bitmap.alpha + static_cast<uint32_t>(y) * bitmap.width;
    for (uint16_t x = 0; x < bitmap.width; ++x) {
      row[x] = blendRgb565(source[x], foreground, background);
    }
    display.pushImage(left, top + y, bitmap.width, 1, row);
  }
  display.setSwapBytes(false);
}

/**
 * @brief 绘制生成器烘焙的 RGB565 小精灵。
 *
 * 资源已经与左侧背景合成，因此按行推送即可；复制到工作缓冲区是为了兼容
 * TFT_eSPI 2.5.0 的非 const pushImage 参数。
 */
void drawRgbBitmap(const RgbBitmap& bitmap, int16_t left, int16_t top) {
  constexpr uint16_t kMaxRowPixels = 128;
  if (bitmap.pixels == nullptr || bitmap.width > kMaxRowPixels) {
    return;
  }

  static uint16_t row[kMaxRowPixels];
  display.setSwapBytes(true);
  for (uint16_t y = 0; y < bitmap.height; ++y) {
    memcpy(row,
           bitmap.pixels + static_cast<uint32_t>(y) * bitmap.width,
           bitmap.width * sizeof(uint16_t));
    display.pushImage(left, top + y, bitmap.width, 1, row);
  }
  display.setSwapBytes(false);
}

/** @brief 在离线生成的动态字体中查找一个字符。 */
const AlphaGlyph* findGlyph(const AlphaFont& font, char character) {
  for (uint16_t index = 0; index < font.count; ++index) {
    if (font.glyphs[index].character == character) {
      return &font.glyphs[index];
    }
  }
  return nullptr;
}

/**
 * @brief 使用离线生成的圆体字形绘制动态文本。
 *
 * @param font 字体映射。
 * @param text 仅包含该字体生成器声明过的 ASCII 字符。
 * @param left advance 区域左边界。
 * @param top 字形统一垂直包围盒顶边。
 * @param foreground 文字颜色。
 * @param background 当前字段背景。
 */
void drawAlphaText(const AlphaFont& font,
                   const char* text,
                   int16_t left,
                   int16_t top,
                   uint16_t foreground,
                   uint16_t background) {
  int16_t cursor = left;
  for (const char* character = text; *character != '\0'; ++character) {
    const AlphaGlyph* glyph = findGlyph(font, *character);
    if (glyph == nullptr) {
      continue;
    }
    // 空格没有 alpha 数据，只推进原字体 advance，避免多余 SPI 操作。
    if (glyph->bitmap.alpha != nullptr) {
      drawAlphaBitmap(glyph->bitmap, cursor, top, foreground, background);
    }
    cursor += glyph->advance;
  }
}

/**
 * @brief 把 Token 数格式化为适合小屏的 K / M / B 写法。
 *
 * 数值和单位之间保留一个空格，所有周期共用同一量纲规则。
 */
void formatTokensCompact(uint64_t tokens, char* out, size_t size) {
  if (tokens >= 1000000000ULL) {
    snprintf(out, size, "%.2f B", tokens / 1000000000.0);
  } else if (tokens >= 1000000ULL) {
    snprintf(out, size, "%.2f M", tokens / 1000000.0);
  } else if (tokens >= 1000ULL) {
    snprintf(out, size, "%.1f K", tokens / 1000.0);
  } else {
    snprintf(out, size, "%llu", static_cast<unsigned long long>(tokens));
  }
}

/** @brief 格式化今日 Token，并补充一次 tokens 图例。 */
void formatTodayTokens(uint64_t tokens, char* out, size_t size) {
  char compact[12];
  formatTokensCompact(tokens, compact, sizeof(compact));
  snprintf(out, size, "%s tokens", compact);
}

/**
 * @brief 根据量级压缩今日金额，保证圆体大字始终留在 178px 主舞台内。
 *
 * 低于 100 美元保留美分；三位整数保留一位小数；四位金额显示整元；再大则
 * 提前切换 K / M，确保最宽字符串也不会侵入中间足迹。
 */
void formatTodayCost(float costUsd, char* out, size_t size) {
  const float value = costUsd < 0.0f ? 0.0f : costUsd;
  if (value < 100.0f) {
    snprintf(out, size, "$%.2f", value);
  } else if (value < 1000.0f) {
    snprintf(out, size, "$%.1f", value);
  } else if (value < 10000.0f) {
    snprintf(out, size, "$%.0f", value);
  } else if (value < 100000.0f) {
    snprintf(out, size, "$%.0fK", value / 1000.0f);
  } else {
    snprintf(out, size, "$%.2fM", value / 1000000.0f);
  }
}

/** @brief 压缩右侧周期金额，避免高位数穿过 126px 面板右边界。 */
void formatPeriodCost(float costUsd, char* out, size_t size) {
  const float value = costUsd < 0.0f ? 0.0f : costUsd;
  if (value < 10000.0f) {
    snprintf(out, size, "$%.0f", value);
  } else if (value < 1000000.0f) {
    snprintf(out, size, "$%.0fK", value / 1000.0f);
  } else {
    snprintf(out, size, "$%.2fM", value / 1000000.0f);
  }
}

/** @brief 返回指定周期段的交替背景色。 */
uint16_t periodBackground(uint8_t row) {
  return row == 1 ? BuddyAssets::kColorPanelB : BuddyAssets::kColorPanelA;
}

/** @brief 在中间轨道上绘制一枚与周期中心对齐的十字星。 */
void drawTrackMarker(int16_t centerY) {
  display.fillRect(Layout::kTrackX, centerY - 5, 2, 11,
                   BuddyAssets::kColorPrimary);
  display.fillRect(Layout::kTrackX - 4, centerY - 1, 10, 2,
                   BuddyAssets::kColorPrimary);
}

/**
 * @brief 绘制开机后不再变化的分屏、标签、小精灵和足迹。
 *
 * 右侧数据带占满屏幕高度，浏览顺序从左侧今日主舞台转向右侧纵向周期足迹，
 * 结构上不复用 Classic 的底部三栏或 Swiss Poster 的底部三行表格。
 */
void drawStaticLayout() {
  display.fillScreen(BuddyAssets::kColorBackground);
  display.setTextWrap(false);

  display.fillRect(Layout::kRightPanelX, 0, Layout::kRightPanelWidth,
                   Layout::kPanelHeight, BuddyAssets::kColorPanelA);
  display.fillRect(Layout::kRightPanelX, Layout::kPanelHeight,
                   Layout::kRightPanelWidth, Layout::kPanelHeight,
                   BuddyAssets::kColorPanelB);
  display.fillRect(Layout::kRightPanelX, Layout::kPanelHeight * 2,
                   Layout::kRightPanelWidth, Layout::kPanelHeight,
                   BuddyAssets::kColorPanelA);
  display.drawFastHLine(Layout::kRightPanelX, 79, Layout::kRightPanelWidth,
                        BuddyAssets::kColorPanelLine);
  display.drawFastHLine(Layout::kRightPanelX, 159, Layout::kRightPanelWidth,
                        BuddyAssets::kColorPanelLine);

  drawAlphaBitmap(BuddyAssets::kBrandDeskBurn, Layout::kBrandX,
                  Layout::kBrandY, BuddyAssets::kColorLavender,
                  BuddyAssets::kColorBackground);
  drawAlphaBitmap(BuddyAssets::kLabelToday, Layout::kTodayLabelX,
                  Layout::kTodayLabelY, BuddyAssets::kColorLavender,
                  BuddyAssets::kColorBackground);

  const AlphaBitmap* labels[] = {
      &BuddyAssets::kLabelWeek,
      &BuddyAssets::kLabelMonth,
      &BuddyAssets::kLabelTotal,
  };
  for (uint8_t row = 0; row < 3; ++row) {
    drawAlphaBitmap(*labels[row], Layout::kPeriodLabelX,
                    row * Layout::kPanelHeight +
                        Layout::kPeriodLabelOffsetY,
                    BuddyAssets::kColorLavender, periodBackground(row));
  }

  drawRgbBitmap(BuddyAssets::kMascot, Layout::kMascotX, Layout::kMascotY);
  drawAlphaBitmap(BuddyAssets::kSleepZ, 151, 69,
                  BuddyAssets::kColorLavender,
                  BuddyAssets::kColorBackground);
  drawAlphaBitmap(BuddyAssets::kSleepZ, 159, 58,
                  BuddyAssets::kColorLavenderMuted,
                  BuddyAssets::kColorBackground);

  // 两枚平面十字星给角色留出呼吸感；尺寸固定，避免与金额刷新区域相交。
  display.fillRect(61, 75, 3, 11, BuddyAssets::kColorPrimary);
  display.fillRect(59, 79, 7, 3, BuddyAssets::kColorPrimary);
  display.fillRect(163, 100, 3, 11, BuddyAssets::kColorPeach);
  display.fillRect(161, 104, 7, 3, BuddyAssets::kColorPeach);

  // 足迹由低对比点线和三个高对比星标组成，分别指向三段周期数据。
  for (int16_t y = 13; y < 240; y += 12) {
    display.fillRect(Layout::kTrackX, y, 2, 3,
                     BuddyAssets::kColorLavenderMuted);
  }
  drawTrackMarker(40);
  drawTrackMarker(120);
  drawTrackMarker(200);
}

/** @brief 只在在线状态变化时重绘品牌行右侧状态。 */
void updateStatus(bool online) {
  if (g_statusDrawn && g_statusOnline == online) {
    return;
  }
  g_statusDrawn = true;
  g_statusOnline = online;

  display.fillRect(Layout::kStatusFieldX, Layout::kStatusFieldY,
                   Layout::kStatusFieldWidth, Layout::kStatusFieldHeight,
                   BuddyAssets::kColorBackground);
  const uint16_t color =
      online ? BuddyAssets::kColorMint : BuddyAssets::kColorOffline;
  display.fillCircle(Layout::kStatusDotCenterX, Layout::kStatusDotCenterY, 4,
                     color);
  const AlphaBitmap& label =
      online ? BuddyAssets::kStatusLive : BuddyAssets::kStatusOff;
  drawAlphaBitmap(label, Layout::kStatusTextX, Layout::kStatusTextY, color,
                  BuddyAssets::kColorBackground);
}

/** @brief 只在今日金额格式化结果变化时重绘左侧主金额。 */
void updateTodayCost(float costUsd) {
  char text[16];
  formatTodayCost(costUsd, text, sizeof(text));
  if (strcmp(g_todayCostCurrent, text) == 0) {
    return;
  }
  strncpy(g_todayCostCurrent, text, sizeof(g_todayCostCurrent) - 1);
  g_todayCostCurrent[sizeof(g_todayCostCurrent) - 1] = '\0';

  display.fillRect(Layout::kTodayCostFieldX, Layout::kTodayCostFieldY,
                   Layout::kTodayCostFieldWidth,
                   Layout::kTodayCostFieldHeight,
                   BuddyAssets::kColorBackground);
  drawAlphaText(BuddyAssets::kTodayAmountFont, text,
                Layout::kTodayCostTextX, Layout::kTodayCostTextY,
                BuddyAssets::kColorPrimary,
                BuddyAssets::kColorBackground);
}

/** @brief 只在今日 Token 字符串变化时重绘金额下方的紫色说明行。 */
void updateTodayTokens(uint64_t tokens) {
  char text[24];
  formatTodayTokens(tokens, text, sizeof(text));
  if (strcmp(g_todayTokensCurrent, text) == 0) {
    return;
  }
  strncpy(g_todayTokensCurrent, text, sizeof(g_todayTokensCurrent) - 1);
  g_todayTokensCurrent[sizeof(g_todayTokensCurrent) - 1] = '\0';

  display.fillRect(Layout::kTodayTokensFieldX, Layout::kTodayTokensFieldY,
                   Layout::kTodayTokensFieldWidth,
                   Layout::kTodayTokensFieldHeight,
                   BuddyAssets::kColorBackground);
  drawAlphaText(BuddyAssets::kTokenFont, text,
                Layout::kTodayTokensTextX, Layout::kTodayTokensTextY,
                BuddyAssets::kColorLavender,
                BuddyAssets::kColorBackground);
}

/**
 * @brief 更新右侧一个周期段的金额和 Token。
 *
 * 金额与 Token 使用两个互不相交的矩形，擦除时不会碰到静态中文标签、面板边界
 * 或中间足迹轨道。三个周期分别缓存，单个字段变化不会重绘其他两段。
 */
void updatePeriodRow(uint8_t row, float costUsd, uint64_t tokens) {
  const int16_t top = row * Layout::kPanelHeight;
  const uint16_t background = periodBackground(row);

  char cost[16];
  formatPeriodCost(costUsd, cost, sizeof(cost));
  if (strcmp(g_periodCostsCurrent[row], cost) != 0) {
    strncpy(g_periodCostsCurrent[row], cost,
            sizeof(g_periodCostsCurrent[row]) - 1);
    g_periodCostsCurrent[row][sizeof(g_periodCostsCurrent[row]) - 1] = '\0';

    display.fillRect(Layout::kPeriodCostFieldX, top + 27,
                     Layout::kPeriodCostFieldWidth,
                     Layout::kPeriodCostFieldHeight, background);
    drawAlphaText(BuddyAssets::kPeriodAmountFont, cost,
                  Layout::kPeriodCostTextX,
                  top + Layout::kPeriodCostOffsetY,
                  BuddyAssets::kColorPrimary, background);
  }

  char tokenText[12];
  formatTokensCompact(tokens, tokenText, sizeof(tokenText));
  if (strcmp(g_periodTokensCurrent[row], tokenText) != 0) {
    strncpy(g_periodTokensCurrent[row], tokenText,
            sizeof(g_periodTokensCurrent[row]) - 1);
    g_periodTokensCurrent[row][sizeof(g_periodTokensCurrent[row]) - 1] = '\0';

    display.fillRect(Layout::kPeriodTokensFieldX, top + 58,
                     Layout::kPeriodTokensFieldWidth,
                     Layout::kPeriodTokensFieldHeight, background);
    drawAlphaText(BuddyAssets::kTokenFont, tokenText,
                  Layout::kPeriodTokensTextX,
                  top + Layout::kPeriodTokensOffsetY,
                  BuddyAssets::kColorLavender, background);
  }
}

/** @brief 按最新数据刷新 Midnight Buddy 的全部动态区域。 */
void renderDashboard(const DashboardData& data) {
  updateStatus(data.online);
  updateTodayCost(data.todayCostUsd);
  updateTodayTokens(data.todayTokens);
  updatePeriodRow(0, data.weekCostUsd, data.weekTokens);
  updatePeriodRow(1, data.monthCostUsd, data.monthTokens);
  updatePeriodRow(2, data.totalCostUsd, data.totalTokens);
}

/**
 * @brief 把共享 BLE 状态转换成页面使用的统一数据模型。
 *
 * 链路字段使用千 Token 和千分之一美元，这里恢复成展示层需要的原始量级。
 */
DashboardData currentData() {
  DashboardData data{};
  data.todayCostUsd = Link::g_state.todayMilliUsd / 1000.0f;
  data.todayTokens = Link::g_state.todayKiloTokens * 1000ULL;
  data.weekCostUsd = Link::g_state.weekMilliUsd / 1000.0f;
  data.weekTokens = Link::g_state.weekKiloTokens * 1000ULL;
  data.monthCostUsd = Link::g_state.monthMilliUsd / 1000.0f;
  data.monthTokens = Link::g_state.monthKiloTokens * 1000ULL;
  data.totalCostUsd = Link::g_state.totalMilliUsd / 1000.0f;
  data.totalTokens = Link::g_state.totalKiloTokens * 1000ULL;
  data.online = !Link::isStale();
  return data;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(800);
  Serial.println();
  Serial.println("[boot] DeskBurn Midnight Buddy starting");

  pinMode(Hardware::kBacklightGate, OUTPUT);
  digitalWrite(Hardware::kBacklightGate, HIGH);

  display.init();
  display.setRotation(1);
  Serial.printf("[display] initialized, %dx%d\n", display.width(),
                display.height());

  drawStaticLayout();
  Serial.println("[display] Midnight Buddy layout drawn");
  Link::begin();
}

/**
 * @brief 每秒读取一次数据；各动态字段按字符串缓存，无变化时不会重复刷屏。
 */
void loop() {
  const DashboardData data = currentData();

  static bool lastOnline = false;
  static bool firstPass = true;
  if (firstPass || data.online != lastOnline) {
    Serial.printf("[link] %s\n", data.online ? "LIVE" : "OFFLINE");
    lastOnline = data.online;
    firstPass = false;
  }

  renderDashboard(data);
  delay(1000);
}
