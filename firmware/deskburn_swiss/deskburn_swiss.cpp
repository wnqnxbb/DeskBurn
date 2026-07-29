/**
 * @file deskburn_swiss.cpp
 * @brief DeskBurn 的 Swiss Poster（瑞士平面海报）展示版本。
 *
 * 页面按 320x240 横屏设计：暖白纸张背景、黑色大字号今日金额、橙红专色线条，
 * 底部用严格网格展示本周、本月和总计。它与经典版复用完全相同的 BLE 协议、
 * Mac 采集端和 NVS 数据，因此烧录后无需重新配置即可继续接收数据。
 */

#include <Arduino.h>
#include <TFT_eSPI.h>

#include "../common/dashboard_data.h"
#include "../deskburn/link_ble.h"
#include "swiss_assets.h"

namespace Hardware {
// 背光由 GPIO6 控制；SPI 与屏幕控制引脚继续使用 platformio.ini 的实测配置。
constexpr int kBacklightGate = 6;
}  // namespace Hardware

namespace Layout {
constexpr int16_t kBrandX = 18;
constexpr int16_t kBrandY = 9;
constexpr int16_t kOpenAiX = 211;
constexpr int16_t kOpenAiY = 7;
constexpr int16_t kClaudeX = 239;
constexpr int16_t kClaudeY = 7;

constexpr int16_t kStatusFieldX = 265;
constexpr int16_t kStatusFieldY = 7;
constexpr int16_t kStatusFieldWidth = 55;
constexpr int16_t kStatusFieldHeight = 22;
constexpr int16_t kStatusDotCenterX = 271;
constexpr int16_t kStatusDotCenterY = 16;
constexpr int16_t kStatusTextX = 280;
constexpr int16_t kStatusTextY = 10;

constexpr int16_t kTodayTitleX = 18;
constexpr int16_t kTodayTitleY = 42;
constexpr int16_t kAmountFieldX = 16;
constexpr int16_t kAmountFieldY = 64;
constexpr int16_t kAmountFieldWidth = 263;
constexpr int16_t kAmountFieldHeight = 56;
constexpr int16_t kAmountTextX = 18;
constexpr int16_t kAmountTextY = 66;
constexpr int16_t kAccentBarX = 285;
constexpr int16_t kAccentBarY = 64;
constexpr int16_t kAccentBarWidth = 6;
constexpr int16_t kAccentBarHeight = 55;

constexpr int16_t kTodayTokensFieldX = 16;
constexpr int16_t kTodayTokensFieldY = 124;
constexpr int16_t kTodayTokensFieldWidth = 270;
constexpr int16_t kTodayTokensFieldHeight = 22;
constexpr int16_t kTodayTokensX = 18;
constexpr int16_t kTodayTokensY = 127;

constexpr int16_t kTableLeft = 18;
constexpr int16_t kTableRight = 309;
constexpr int16_t kTableDividerX = 202;
constexpr int16_t kRowCenters[] = {171, 199, 227};
constexpr int16_t kRowLabelX = 20;
constexpr int16_t kCostFieldX = 76;
constexpr int16_t kCostFieldWidth = 116;
constexpr int16_t kCostRightX = 188;
constexpr int16_t kTokenFieldX = 211;
constexpr int16_t kTokenFieldWidth = 109;
constexpr int16_t kTokenTextX = 216;
constexpr int16_t kRowFieldHeight = 24;
}  // namespace Layout

namespace {

using SwissAssets::AlphaBitmap;
using SwissAssets::AlphaFont;
using SwissAssets::AlphaGlyph;

TFT_eSPI display = TFT_eSPI();

char g_amountCurrent[16] = "";
char g_todayTokensCurrent[24] = "";
char g_periodCostsCurrent[3][16] = {{""}, {""}, {""}};
char g_periodTokensCurrent[3][12] = {{""}, {""}, {""}};
bool g_statusDrawn = false;
bool g_statusOnline = false;

/**
 * @brief 按 alpha 混合 RGB565 前景与 Swiss Poster 的暖白背景。
 *
 * @param alpha 前景不透明度，0 为纸张色，255 为纯前景色。
 * @param foreground 要绘制的油墨色。
 * @return 混合后的 RGB565 像素。
 */
uint16_t blendWithPaper(uint8_t alpha, uint16_t foreground) {
  const uint16_t background = SwissAssets::kColorPaper;
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
 * 位图逐行混合后再通过 SPI 推送，避免逐像素开关事务。所有资源背景都按暖白纸张
 * 混合，所以局部刷新后的边缘不会出现深色方框。
 *
 * @param bitmap 要绘制的蒙版。
 * @param left 左上角 X 坐标。
 * @param top 左上角 Y 坐标。
 * @param color 前景 RGB565 颜色。
 */
void drawAlphaBitmap(const AlphaBitmap& bitmap,
                     int16_t left,
                     int16_t top,
                     uint16_t color) {
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
      row[x] = blendWithPaper(source[x], color);
    }
    display.pushImage(left, top + y, bitmap.width, 1, row);
  }
  display.setSwapBytes(false);
}

/**
 * @brief 在生成字体中查找一个字符。
 *
 * @return 对应字形；字体未包含该字符时返回 nullptr。
 */
const AlphaGlyph* findGlyph(const AlphaFont& font, char character) {
  for (uint16_t index = 0; index < font.count; ++index) {
    if (font.glyphs[index].character == character) {
      return &font.glyphs[index];
    }
  }
  return nullptr;
}

/** @brief 计算生成字体下一段文字的像素宽度。 */
int16_t alphaTextWidth(const AlphaFont& font, const char* text) {
  int16_t width = 0;
  for (const char* character = text; *character != '\0'; ++character) {
    const AlphaGlyph* glyph = findGlyph(font, *character);
    if (glyph != nullptr) {
      width += glyph->advance;
    }
  }
  return width;
}

/**
 * @brief 使用离线生成的字形绘制动态文本。
 *
 * @param font 字体及字符映射。
 * @param text 要绘制的 ASCII 文本。
 * @param left 文字 advance 区域的左边界。
 * @param top 字形统一垂直包围盒的顶边。
 * @param color 前景颜色。
 */
void drawAlphaText(const AlphaFont& font,
                   const char* text,
                   int16_t left,
                   int16_t top,
                   uint16_t color) {
  int16_t cursor = left;
  for (const char* character = text; *character != '\0'; ++character) {
    const AlphaGlyph* glyph = findGlyph(font, *character);
    if (glyph == nullptr) {
      continue;
    }
    // 空格的 alpha 指针为空，只推进 advance，不发起无意义的 SPI 推送。
    if (glyph->bitmap.alpha != nullptr) {
      drawAlphaBitmap(glyph->bitmap, cursor, top, color);
    }
    cursor += glyph->advance;
  }
}

/** @brief 把 Token 数格式化为适合海报等宽列的 K / M / B 写法。 */
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

/** @brief 格式化今日 Token，并增加海报参考图中的 tokens 图例。 */
void formatTodayTokens(uint64_t tokens, char* out, size_t size) {
  char compact[12];
  formatTokensCompact(tokens, compact, sizeof(compact));
  snprintf(out, size, "%s tokens", compact);
}

/**
 * @brief 格式化今日金额。
 *
 * 三位以内保留美分；达到四位数后去掉小数，避免文字侵入右侧橙色强调线。这个规则
 * 只改变显示精度，不改变底层收到和保存的金额数据。
 */
void formatTodayCost(float costUsd, char* out, size_t size) {
  const float clamped = costUsd < 0.0f ? 0.0f : costUsd;
  if (clamped < 1000.0f) {
    snprintf(out, size, "$%.2f", clamped);
  } else {
    snprintf(out, size, "$%.0f", clamped);
  }
}

/** @brief 格式化底部表格金额；与参考图一致，只显示整美元。 */
void formatPeriodCost(float costUsd, char* out, size_t size) {
  const float clamped = costUsd < 0.0f ? 0.0f : costUsd;
  snprintf(out, size, "$%.0f", clamped);
}

/**
 * @brief 绘制开机后保持不变的海报网格和文字。
 *
 * 左上角严格按用户要求只保留 DeskBurn 文字，不绘制参考图中的小图标。
 */
void drawStaticLayout() {
  display.fillScreen(SwissAssets::kColorPaper);
  display.setTextWrap(false);

  drawAlphaBitmap(SwissAssets::kBrandDeskBurn, Layout::kBrandX,
                  Layout::kBrandY, SwissAssets::kColorAccent);
  drawAlphaBitmap(SwissAssets::kLogoOpenAi, Layout::kOpenAiX,
                  Layout::kOpenAiY, SwissAssets::kColorInk);
  drawAlphaBitmap(SwissAssets::kLogoClaude, Layout::kClaudeX,
                  Layout::kClaudeY, SwissAssets::kColorClaude);
  drawAlphaBitmap(SwissAssets::kTitleToday, Layout::kTodayTitleX,
                  Layout::kTodayTitleY, SwissAssets::kColorInk);

  display.fillRect(Layout::kAccentBarX, Layout::kAccentBarY,
                   Layout::kAccentBarWidth, Layout::kAccentBarHeight,
                   SwissAssets::kColorAccent);

  display.drawFastHLine(Layout::kTableLeft, 184,
                        Layout::kTableRight - Layout::kTableLeft + 1,
                        SwissAssets::kColorInk);
  display.drawFastHLine(Layout::kTableLeft, 212,
                        Layout::kTableRight - Layout::kTableLeft + 1,
                        SwissAssets::kColorInk);
  display.fillRect(Layout::kTableDividerX, 158, 2, 80,
                   SwissAssets::kColorAccent);

  const AlphaBitmap* labels[] = {
      &SwissAssets::kLabelWeek,
      &SwissAssets::kLabelMonth,
      &SwissAssets::kLabelTotal,
  };
  for (uint8_t row = 0; row < 3; ++row) {
    drawAlphaBitmap(*labels[row], Layout::kRowLabelX,
                    Layout::kRowCenters[row] - labels[row]->height / 2,
                    SwissAssets::kColorInk);
  }
}

/** @brief 只在在线状态改变时重绘顶栏右侧状态。 */
void updateStatus(bool online) {
  if (g_statusDrawn && g_statusOnline == online) {
    return;
  }
  g_statusDrawn = true;
  g_statusOnline = online;

  display.fillRect(Layout::kStatusFieldX, Layout::kStatusFieldY,
                   Layout::kStatusFieldWidth, Layout::kStatusFieldHeight,
                   SwissAssets::kColorPaper);
  const uint16_t color =
      online ? SwissAssets::kColorLive : SwissAssets::kColorOffline;
  display.fillCircle(Layout::kStatusDotCenterX, Layout::kStatusDotCenterY, 4,
                     color);
  const AlphaBitmap& status =
      online ? SwissAssets::kStatusLive : SwissAssets::kStatusOff;
  drawAlphaBitmap(status, Layout::kStatusTextX, Layout::kStatusTextY, color);
}

/** @brief 只在金额变化时擦除并重绘主视觉金额。 */
void updateTodayCost(float costUsd) {
  char amount[16];
  formatTodayCost(costUsd, amount, sizeof(amount));
  if (strcmp(g_amountCurrent, amount) == 0) {
    return;
  }
  strncpy(g_amountCurrent, amount, sizeof(g_amountCurrent) - 1);
  g_amountCurrent[sizeof(g_amountCurrent) - 1] = '\0';

  display.fillRect(Layout::kAmountFieldX, Layout::kAmountFieldY,
                   Layout::kAmountFieldWidth, Layout::kAmountFieldHeight,
                   SwissAssets::kColorPaper);
  drawAlphaText(SwissAssets::kAmountFont, amount, Layout::kAmountTextX,
                Layout::kAmountTextY, SwissAssets::kColorInk);
}

/** @brief 只在今日 Token 变化时重绘金额下方的等宽说明行。 */
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
                   SwissAssets::kColorPaper);
  drawAlphaText(SwissAssets::kMonoFont, text, Layout::kTodayTokensX,
                Layout::kTodayTokensY, SwissAssets::kColorInk);
}

/**
 * @brief 更新表格中的一个数据行。
 *
 * 擦除矩形刻意避开红色竖线和黑色横线，所以局部刷新不会把海报网格啃出缺口。
 */
void updatePeriodRow(uint8_t row,
                     float costUsd,
                     uint64_t tokens) {
  char cost[16];
  formatPeriodCost(costUsd, cost, sizeof(cost));
  if (strcmp(g_periodCostsCurrent[row], cost) != 0) {
    strncpy(g_periodCostsCurrent[row], cost,
            sizeof(g_periodCostsCurrent[row]) - 1);
    g_periodCostsCurrent[row][sizeof(g_periodCostsCurrent[row]) - 1] = '\0';

    const int16_t fieldTop =
        Layout::kRowCenters[row] - Layout::kRowFieldHeight / 2;
    display.fillRect(Layout::kCostFieldX, fieldTop, Layout::kCostFieldWidth,
                     Layout::kRowFieldHeight, SwissAssets::kColorPaper);
    const int16_t width = alphaTextWidth(SwissAssets::kTableCostFont, cost);
    drawAlphaText(
        SwissAssets::kTableCostFont, cost, Layout::kCostRightX - width,
        Layout::kRowCenters[row] - SwissAssets::kTableCostFont.height / 2,
        SwissAssets::kColorInk);
  }

  char tokenText[12];
  formatTokensCompact(tokens, tokenText, sizeof(tokenText));
  if (strcmp(g_periodTokensCurrent[row], tokenText) != 0) {
    strncpy(g_periodTokensCurrent[row], tokenText,
            sizeof(g_periodTokensCurrent[row]) - 1);
    g_periodTokensCurrent[row][sizeof(g_periodTokensCurrent[row]) - 1] = '\0';

    const int16_t fieldTop =
        Layout::kRowCenters[row] - Layout::kRowFieldHeight / 2;
    display.fillRect(Layout::kTokenFieldX, fieldTop, Layout::kTokenFieldWidth,
                     Layout::kRowFieldHeight, SwissAssets::kColorPaper);
    drawAlphaText(
        SwissAssets::kMonoFont, tokenText, Layout::kTokenTextX,
        Layout::kRowCenters[row] - SwissAssets::kMonoFont.height / 2,
        SwissAssets::kColorInk);
  }
}

/** @brief 按最新数据刷新 Swiss Poster 的全部动态区域。 */
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
 * 链路字段使用千 token 和千分之一美元，这里恢复成展示层使用的原始量级。
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
  Serial.println("[boot] DeskBurn Swiss Poster starting");

  pinMode(Hardware::kBacklightGate, OUTPUT);
  digitalWrite(Hardware::kBacklightGate, HIGH);

  display.init();
  display.setRotation(1);
  Serial.printf("[display] initialized, %dx%d\n", display.width(),
                display.height());

  drawStaticLayout();
  Serial.println("[display] Swiss Poster layout drawn");
  Link::begin();
}

/**
 * @brief 每秒读取一次数据；各字段内部按字符串缓存，所以无变化时不会重复刷屏。
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
