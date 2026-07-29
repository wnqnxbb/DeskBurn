/**
 * @file deskburn_cosmic.cpp
 * @brief DeskBurn 的 Cosmic Buddy（月夜精灵与星球数据岛）展示版本。
 *
 * 页面按用户接受的 320×240 设计稿实现：左侧是今日月夜主舞台，右侧是本周、
 * 本月、总计三座不规则星球数据岛。复杂插画保存为一张无文字 RGB565 背景，
 * 所有状态、标签和数值仍由固件代码绘制，并复用 Classic、Swiss Poster、
 * Midnight Buddy 相同的 BLE 协议、NVS 和 Mac 采集端。
 */

#include <Arduino.h>
#include <TFT_eSPI.h>

#include <cstring>

#include "../common/dashboard_data.h"
#include "../deskburn/link_ble.h"
#include "cosmic_assets.h"

namespace Hardware {
// 背光与屏幕 SPI 引脚都沿用 platformio.ini 中实机验证过的配置。
constexpr int kBacklightGate = 6;
}  // namespace Hardware

namespace Layout {

struct Rect {
  int16_t left;
  int16_t top;
  int16_t width;
  int16_t height;
};

struct Point {
  int16_t x;
  int16_t y;
};

constexpr Point kBrand = {8, 7};
constexpr Rect kStatusField = {68, 4, 39, 17};
constexpr Point kStatusDotCenter = {73, 12};
constexpr Point kStatusText = {80, 7};
constexpr Point kTodayLabel = {11, 32};

constexpr Rect kTodayCostField = {14, 157, 154, 47};
constexpr Point kTodayCurrency = {18, 168};
constexpr Point kTodayAmount = {38, 159};
constexpr Rect kTodayTokensField = {34, 205, 150, 18};
constexpr Point kTodayTokens = {37, 207};

constexpr Point kPeriodLabels[3] = {
    {226, 17},
    {222, 97},
    {218, 177},
};
constexpr Rect kPeriodAmountFields[3] = {
    {244, 28, 68, 18},
    {244, 108, 68, 18},
    {244, 189, 68, 18},
};
constexpr Rect kPeriodTokenFields[3] = {
    {245, 53, 66, 8},
    {245, 134, 66, 8},
    {245, 211, 66, 8},
};

// 周期金额和 Token 独立刷新；两块恢复区域一旦重叠，金额变化会擦掉 Token 顶部。
static_assert(kPeriodAmountFields[0].top +
                      kPeriodAmountFields[0].height <=
                  kPeriodTokenFields[0].top,
              "week amount and token fields must not overlap");
static_assert(kPeriodAmountFields[1].top +
                      kPeriodAmountFields[1].height <=
                  kPeriodTokenFields[1].top,
              "month amount and token fields must not overlap");
static_assert(kPeriodAmountFields[2].top +
                      kPeriodAmountFields[2].height <=
                  kPeriodTokenFields[2].top,
              "total amount and token fields must not overlap");

}  // namespace Layout

namespace {

using CosmicAssets::AlphaBitmap;
using CosmicAssets::AlphaFont;
using CosmicAssets::AlphaGlyph;

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
 * @param background 接受稿背景在当前像素的原始颜色。
 * @return 混合后的 RGB565 像素。
 */
uint16_t blendRgb565(uint8_t alpha,
                     uint16_t foreground,
                     uint16_t background) {
  const uint32_t inverse = 255 - alpha;
  const uint32_t red =
      ((foreground >> 11) & 0x1F) * alpha +
      ((background >> 11) & 0x1F) * inverse;
  const uint32_t green =
      ((foreground >> 5) & 0x3F) * alpha +
      ((background >> 5) & 0x3F) * inverse;
  const uint32_t blue =
      (foreground & 0x1F) * alpha + (background & 0x1F) * inverse;
  return static_cast<uint16_t>(((red / 255) << 11) |
                               ((green / 255) << 5) | (blue / 255));
}

/** @brief 返回无文字背景在指定屏幕坐标的 RGB565 像素。 */
uint16_t backgroundPixel(int16_t x, int16_t y) {
  if (x < 0 || y < 0 || x >= CosmicAssets::kBackground.width ||
      y >= CosmicAssets::kBackground.height) {
    return 0;
  }
  const uint32_t index =
      static_cast<uint32_t>(y) * CosmicAssets::kBackground.width + x;
  return CosmicAssets::kBackground.pixels[index];
}

/**
 * @brief 从完整背景资源恢复一个屏幕矩形。
 *
 * 动态字段覆盖云岛渐变和星空纹理，更新前不能用纯色 fillRect 擦除。这里从唯一
 * 一份背景数组逐行恢复对应像素，既保持原画完整，也避免为每个字段重复保存切片。
 */
void restoreBackground(const Layout::Rect& rect) {
  constexpr uint16_t kMaxRowPixels = 320;
  if (rect.left < 0 || rect.top < 0 || rect.width <= 0 ||
      rect.height <= 0 || rect.width > kMaxRowPixels ||
      rect.left + rect.width > CosmicAssets::kBackground.width ||
      rect.top + rect.height > CosmicAssets::kBackground.height) {
    return;
  }

  static uint16_t row[kMaxRowPixels];
  display.setSwapBytes(true);
  for (int16_t y = 0; y < rect.height; ++y) {
    const uint32_t offset =
        static_cast<uint32_t>(rect.top + y) *
            CosmicAssets::kBackground.width +
        rect.left;
    memcpy(row, CosmicAssets::kBackground.pixels + offset,
           rect.width * sizeof(uint16_t));
    display.pushImage(rect.left, rect.top + y, rect.width, 1, row);
  }
  display.setSwapBytes(false);
}

/** @brief 一次绘制整张无文字月夜背景。 */
void drawBackground() {
  const Layout::Rect fullScreen = {
      0,
      0,
      static_cast<int16_t>(CosmicAssets::kBackground.width),
      static_cast<int16_t>(CosmicAssets::kBackground.height),
  };
  restoreBackground(fullScreen);
}

/**
 * @brief 在指定坐标绘制抗锯齿 alpha 蒙版。
 *
 * 每个半透明边缘像素都与背景资源中的同坐标像素混合，标签覆盖云岛或星空时不会
 * 出现一圈错误的纯色底，也能在后续背景恢复后得到完全一致的结果。
 */
void drawAlphaBitmap(const AlphaBitmap& bitmap,
                     int16_t left,
                     int16_t top,
                     uint16_t foreground) {
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
      row[x] = blendRgb565(source[x], foreground,
                           backgroundPixel(left + x, top + y));
    }
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
 * @param text 仅包含生成器声明过的 ASCII 字符。
 * @param left 文本 advance 区域左边界。
 * @param top 统一字形包围盒顶边。
 * @param foreground 文字颜色。
 */
void drawAlphaText(const AlphaFont& font,
                   const char* text,
                   int16_t left,
                   int16_t top,
                   uint16_t foreground) {
  int16_t cursor = left;
  for (const char* character = text; *character != '\0'; ++character) {
    const AlphaGlyph* glyph = findGlyph(font, *character);
    if (glyph == nullptr) {
      continue;
    }
    if (glyph->bitmap.alpha != nullptr) {
      drawAlphaBitmap(glyph->bitmap, cursor, top, foreground);
    }
    cursor += glyph->advance;
  }
}

/**
 * @brief 绘制今日金额数字，并用粉色四角星替代普通小数点。
 *
 * 四角星跟随格式化后的字符串位置移动，因此金额位数变化时不会像固定背景装饰那样
 * 与数字重叠；没有小数位的 K/M 格式也不会额外出现星标。
 */
void drawTodayAmount(const char* text) {
  int16_t cursor = Layout::kTodayAmount.x;
  for (const char* character = text; *character != '\0'; ++character) {
    const AlphaGlyph* glyph =
        findGlyph(CosmicAssets::kTodayAmountFont, *character);
    if (glyph == nullptr) {
      continue;
    }
    if (*character == '.') {
      drawAlphaBitmap(CosmicAssets::kTodayDecimalSparkle, cursor,
                      Layout::kTodayAmount.y + 25,
                      CosmicAssets::kColorPeach);
    } else if (glyph->bitmap.alpha != nullptr) {
      drawAlphaBitmap(glyph->bitmap, cursor, Layout::kTodayAmount.y,
                      CosmicAssets::kColorPrimary);
    }
    cursor += glyph->advance;
  }
}

/**
 * @brief 把 Token 数格式化为适合小屏的 K / M / B / T 写法。
 *
 * 协议传输上限可超过一万亿 Token，因此补充 T 档以保证极端值仍落在数据岛内。
 */
void formatTokensCompact(uint64_t tokens, char* out, size_t size) {
  if (tokens >= 1000000000000ULL) {
    snprintf(out, size, "%.2f T", tokens / 1000000000000.0);
  } else if (tokens >= 1000000000ULL) {
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
 * @brief 根据量级压缩今日金额，返回不含货币符号的数字部分。
 *
 * 美元符号在接受稿中更小且为紫色，因此独立绘制；数字使用奶油白大字。十万美元
 * 起切换 M，防止四舍五入后的四位 K 数字侵入右侧数据岛。
 */
void formatTodayCost(float costUsd, char* out, size_t size) {
  const float value = costUsd < 0.0f ? 0.0f : costUsd;
  if (value < 100.0f) {
    snprintf(out, size, "%.2f", value);
  } else if (value < 1000.0f) {
    snprintf(out, size, "%.1f", value);
  } else if (value < 10000.0f) {
    snprintf(out, size, "%.0f", value);
  } else if (value < 100000.0f) {
    snprintf(out, size, "%.0fK", value / 1000.0f);
  } else {
    snprintf(out, size, "%.2fM", value / 1000000.0f);
  }
}

/** @brief 压缩右侧周期金额，并保留设计稿中的美元符号。 */
void formatPeriodCost(float costUsd, char* out, size_t size) {
  const float value = costUsd < 0.0f ? 0.0f : costUsd;
  if (value < 10000.0f) {
    snprintf(out, size, "$%.0f", value);
  } else if (value < 999500.0f) {
    snprintf(out, size, "$%.0fK", value / 1000.0f);
  } else if (value < 10000000.0f) {
    snprintf(out, size, "$%.1fM", value / 1000000.0f);
  } else {
    snprintf(out, size, "$%.0fM", value / 1000000.0f);
  }
}

/**
 * @brief 绘制开机后不再变化的品牌和中文标签。
 *
 * 月牙、精灵、云岛、行星与轨迹已经包含在背景资源中；这里只叠加必须由代码
 * 原生渲染的文案，保证数据层与美术层解耦。
 */
void drawStaticLabels() {
  drawAlphaBitmap(CosmicAssets::kBrandDeskBurn, Layout::kBrand.x,
                  Layout::kBrand.y, CosmicAssets::kColorLavender);
  drawAlphaBitmap(CosmicAssets::kLabelToday, Layout::kTodayLabel.x,
                  Layout::kTodayLabel.y, CosmicAssets::kColorLavender);
  // 接受稿在“今日”下方使用短线和圆点作为固定章节标记。
  display.fillRect(12, 55, 17, 2, CosmicAssets::kColorLavender);
  display.fillCircle(34, 56, 2, CosmicAssets::kColorLavender);

  const AlphaBitmap* labels[] = {
      &CosmicAssets::kLabelWeek,
      &CosmicAssets::kLabelMonth,
      &CosmicAssets::kLabelTotal,
  };
  for (uint8_t row = 0; row < 3; ++row) {
    drawAlphaBitmap(*labels[row], Layout::kPeriodLabels[row].x,
                    Layout::kPeriodLabels[row].y,
                    CosmicAssets::kColorLavenderLight);
  }
}

/** @brief 只在在线状态变化时重绘品牌右侧的状态。 */
void updateStatus(bool online) {
  if (g_statusDrawn && g_statusOnline == online) {
    return;
  }
  g_statusDrawn = true;
  g_statusOnline = online;

  restoreBackground(Layout::kStatusField);
  const uint16_t color =
      online ? CosmicAssets::kColorMint : CosmicAssets::kColorOffline;
  display.fillCircle(Layout::kStatusDotCenter.x,
                     Layout::kStatusDotCenter.y, 3, color);
  const AlphaBitmap& label =
      online ? CosmicAssets::kStatusLive : CosmicAssets::kStatusOff;
  drawAlphaBitmap(label, Layout::kStatusText.x, Layout::kStatusText.y,
                  color);
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

  restoreBackground(Layout::kTodayCostField);
  drawAlphaBitmap(CosmicAssets::kTodayCurrency,
                  Layout::kTodayCurrency.x, Layout::kTodayCurrency.y,
                  CosmicAssets::kColorLavender);
  drawTodayAmount(text);
}

/** @brief 只在今日 Token 字符串变化时重绘主金额下方说明行。 */
void updateTodayTokens(uint64_t tokens) {
  char text[24];
  formatTodayTokens(tokens, text, sizeof(text));
  if (strcmp(g_todayTokensCurrent, text) == 0) {
    return;
  }
  strncpy(g_todayTokensCurrent, text,
          sizeof(g_todayTokensCurrent) - 1);
  g_todayTokensCurrent[sizeof(g_todayTokensCurrent) - 1] = '\0';

  restoreBackground(Layout::kTodayTokensField);
  drawAlphaText(CosmicAssets::kTodayTokenFont, text,
                Layout::kTodayTokens.x, Layout::kTodayTokens.y,
                CosmicAssets::kColorLavender);
}

/**
 * @brief 更新一座星球数据岛中的金额和 Token。
 *
 * 两个字段分别缓存与恢复。金额和 Token 都从各自字段的左边缘开始绘制，使三座
 * 数据岛的数值形成稳定的左侧基准线，同时给较长 Token 留出完整的右侧空间。
 * 背景恢复只覆盖岛内预留空白，不会碰到左侧行星、顶部标签胶囊、云岛轮廓或
 * 三岛之间的星点轨迹。
 */
void updatePeriodIsland(uint8_t row,
                        float costUsd,
                        uint64_t tokens) {
  char cost[16];
  formatPeriodCost(costUsd, cost, sizeof(cost));
  if (strcmp(g_periodCostsCurrent[row], cost) != 0) {
    strncpy(g_periodCostsCurrent[row], cost,
            sizeof(g_periodCostsCurrent[row]) - 1);
    g_periodCostsCurrent[row][sizeof(g_periodCostsCurrent[row]) - 1] =
        '\0';

    restoreBackground(Layout::kPeriodAmountFields[row]);
    // 以字段左边缘为固定锚点，金额位数变化时三行仍保持纵向对齐。
    drawAlphaText(CosmicAssets::kPeriodAmountFont, cost,
                  Layout::kPeriodAmountFields[row].left,
                  Layout::kPeriodAmountFields[row].top,
                  CosmicAssets::kColorPrimary);
  }

  char tokenText[12];
  formatTokensCompact(tokens, tokenText, sizeof(tokenText));
  if (strcmp(g_periodTokensCurrent[row], tokenText) != 0) {
    strncpy(g_periodTokensCurrent[row], tokenText,
            sizeof(g_periodTokensCurrent[row]) - 1);
    g_periodTokensCurrent[row][sizeof(g_periodTokensCurrent[row]) - 1] =
        '\0';

    restoreBackground(Layout::kPeriodTokenFields[row]);
    // Token 同样左对齐，避免 B/M 等较长格式贴住屏幕右边缘而被裁切。
    drawAlphaText(CosmicAssets::kPeriodTokenFont, tokenText,
                  Layout::kPeriodTokenFields[row].left,
                  Layout::kPeriodTokenFields[row].top,
                  CosmicAssets::kColorLavender);
  }
}

/** @brief 按最新数据刷新 Cosmic Buddy 的全部动态区域。 */
void renderDashboard(const DashboardData& data) {
  updateStatus(data.online);
  updateTodayCost(data.todayCostUsd);
  updateTodayTokens(data.todayTokens);
  updatePeriodIsland(0, data.weekCostUsd, data.weekTokens);
  updatePeriodIsland(1, data.monthCostUsd, data.monthTokens);
  updatePeriodIsland(2, data.totalCostUsd, data.totalTokens);
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
  Serial.println("[boot] DeskBurn Cosmic Buddy starting");

  pinMode(Hardware::kBacklightGate, OUTPUT);
  digitalWrite(Hardware::kBacklightGate, HIGH);

  display.init();
  display.setRotation(1);
  display.setTextWrap(false);
  Serial.printf("[display] initialized, %dx%d\n", display.width(),
                display.height());

  drawBackground();
  drawStaticLabels();
  Serial.println("[display] Cosmic Buddy layout drawn");
  Link::begin();
}

/**
 * @brief 每秒读取一次数据；各字段按字符串缓存，无变化时不会重复刷屏。
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
