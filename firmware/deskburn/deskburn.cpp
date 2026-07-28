/**
 * @file deskburn.cpp
 * @brief 3.5 寸 320x240 屏上的 Token 用量仪表盘。
 *
 * 引脚与分辨率由 pin_scanner 固件实测确认：SCLK=GPIO2（不是此前推测的
 * GPIO10，那实际上是板载默认 SPI 的 MISO），面板在横屏下为 320x240。
 *
 * 数据由 Mac 侧 CC Switch 采集服务通过 BLE 推送。屏幕端保存最后一次有效值，
 * 短时断线时继续展示旧数据并标记 OFFLINE。
 */

#include <Arduino.h>
#include <TFT_eSPI.h>

#include "assets.h"
#include "link_ble.h"

namespace Hardware {
// 背光由 GPIO6 控制；屏幕 SPI 引脚在 platformio.ini 中配置。
constexpr int kBacklightGate = 6;
}  // namespace Hardware

namespace Colors {
constexpr uint16_t kBackground = 0x0841;
constexpr uint16_t kPrimaryText = TFT_WHITE;
// 今日金额的绿色（#4ADE80）。深背景上亮度足够，又不像纯绿那样刺眼。
constexpr uint16_t kTodayCost = 0x4EF0;
constexpr uint16_t kSecondaryText = 0xAD55;
constexpr uint16_t kDivider = 0x2945;
// 在线复用今日金额的亮绿色；离线用柔和红色（#F87171），暗背景上醒目但不刺眼。
constexpr uint16_t kStatusOnline = kTodayCost;
constexpr uint16_t kStatusOffline = 0xFB8E;
}  // namespace Colors

namespace Layout {
// 横屏 320x240，主数据整列居中：顶部是「今日消耗」标题行，正下方是今日金额，
// 再往下是今日 Token 数，分隔线之下是本周 / 本月 / 总计三栏，每栏金额下面再挂
// 一行该周期的 Token 数。
//
// 纵向坐标按各元素的实际包围盒排布。周期金额略微下移，拉开它与标签的距离；
// Token 换成 22px SF Pro Bold 后也同步下移，让两组内容的视觉间距更均衡。
// 两种动态文字的擦除矩形不能相交，否则数值刷新时会啃掉相邻行的像素。
constexpr int16_t kCenterX = 160;

constexpr int16_t kHeaderY = 32;         // 图标 34px 高，占 15..49
constexpr int16_t kTodayCostY = 80;      // 字形 38px 高，擦除带占 57..103
constexpr int16_t kTodayTokensY = 124;   // Font4 26px 高，擦除带占 109..139
constexpr int16_t kDividerY = 148;
constexpr int16_t kPeriodLabelY = 168;   // 标签 23px 高，占 157..180
constexpr int16_t kPeriodCostY = 200;    // Font4 擦除带占 185..215
constexpr int16_t kPeriodTokensY = 226;  // 粗体字形擦除带占 216..236

// 标题行里「今日消耗」与两侧图标之间的留白。两枚图标相对屏幕中心对称摆放，
// 具体坐标在 drawStaticLayout 里按各自蒙版宽度算，换图标不用改这里。
constexpr int16_t kHeaderGap = 20;

// 本周 / 本月 / 总计三栏的水平中心，320 宽三等分后取各栏中点。
// 栏距 107px，而 Font4 下 "$999999" 只有 89px，最宽的标签 46px，都放得下。
constexpr int16_t kWeekColumnX = 53;
constexpr int16_t kMonthColumnX = 160;
constexpr int16_t kTotalColumnX = 267;

// 状态文字放在左上角的空白处，不与任何数据相邻。
constexpr int16_t kFootnoteX = 26;
constexpr int16_t kFootnoteY = 12;

// 金额与美元符号不再用内置字体，改用 assets.h 里烘焙的 SF Pro Bold 字形。
constexpr uint8_t kTokensFont = 4;
constexpr uint8_t kPeriodCostFont = 4;
constexpr uint8_t kFootnoteFont = 1;
}  // namespace Layout

/// 一次刷新所需的全部展示数据。接入 CC Switch 后由 Mac 端填充。
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

TFT_eSPI display = TFT_eSPI();

/**
 * @brief 一块可独立重绘的居中文字区域。
 *
 * 记住上一次渲染的字符串，只有内容变化时才擦除旧包围盒并重画，
 * 避免每次刷新都整屏重绘导致的闪烁。bold 为 true 时额外透明叠绘 1px，
 * 给不带粗体字重的 TFT_eSPI 内置字体加粗。
 */
struct TextSlot {
  int16_t centerX;
  int16_t centerY;
  uint8_t font;
  uint16_t color;
  bool bold;
  char current[24];
};

/**
 * @brief 一块使用自定义粗体字形的周期 Token 区域。
 *
 * 除了缓存字符串，还记录上一次实际占用的横向范围，保证数值变短时只擦掉旧字，
 * 不碰同一行的其他栏位。
 */
struct PeriodTokenSlot {
  int16_t centerX;
  int16_t centerY;
  uint16_t color;
  char current[12];
  int16_t left;
  int16_t width;
};

TextSlot g_todayTokensSlot{Layout::kCenterX, Layout::kTodayTokensY,
                           Layout::kTokensFont, Colors::kSecondaryText, true, ""};
TextSlot g_weekCostSlot{Layout::kWeekColumnX, Layout::kPeriodCostY,
                        Layout::kPeriodCostFont, Colors::kPrimaryText, false, ""};
TextSlot g_monthCostSlot{Layout::kMonthColumnX, Layout::kPeriodCostY,
                         Layout::kPeriodCostFont, Colors::kPrimaryText, false, ""};
TextSlot g_totalCostSlot{Layout::kTotalColumnX, Layout::kPeriodCostY,
                         Layout::kPeriodCostFont, Colors::kPrimaryText, false, ""};
PeriodTokenSlot g_weekTokensSlot{Layout::kWeekColumnX, Layout::kPeriodTokensY,
                                 Colors::kSecondaryText, "", 0, 0};
PeriodTokenSlot g_monthTokensSlot{Layout::kMonthColumnX, Layout::kPeriodTokensY,
                                  Colors::kSecondaryText, "", 0, 0};
PeriodTokenSlot g_totalTokensSlot{Layout::kTotalColumnX, Layout::kPeriodTokensY,
                                  Colors::kSecondaryText, "", 0, 0};
TextSlot g_footnoteSlot{Layout::kFootnoteX, Layout::kFootnoteY,
                        Layout::kFootnoteFont, Colors::kStatusOffline, false, ""};

// 今日金额横跨两种字体，单独缓存数字部分和上一次占用的矩形。
char g_todayCostCurrent[16] = "";
int16_t g_todayCostLeft = 0;
int16_t g_todayCostWidth = 0;

/**
 * @brief 按 alpha 混合两个 RGB565 颜色。
 *
 * TFT_eSPI 2.5.0 里三参数版 alphaBlend 虽然在头文件中声明，实现却是 .cpp 内的
 * inline 函数，外部链接不到；自带一份也顺便省掉了带抖动版本的额外开销。
 *
 * @param alpha 前景不透明度，0 为全背景，255 为全前景。
 * @param foreground 前景色。
 * @param background 背景色。
 */
uint16_t blendRgb565(uint8_t alpha, uint16_t foreground, uint16_t background) {
  const uint16_t inverse = 255 - alpha;
  const uint16_t red =
      ((foreground >> 11) & 0x1F) * alpha + ((background >> 11) & 0x1F) * inverse;
  const uint16_t green =
      ((foreground >> 5) & 0x3F) * alpha + ((background >> 5) & 0x3F) * inverse;
  const uint16_t blue =
      (foreground & 0x1F) * alpha + (background & 0x1F) * inverse;
  return static_cast<uint16_t>(((red / 255) << 11) | ((green / 255) << 5) |
                               (blue / 255));
}

/**
 * @brief 绘制一张 alpha 蒙版位图，按指定颜色与背景混合。
 *
 * 图标和中文都以蒙版形式烘焙进固件（见 tools/generate_assets.py）。这里逐行
 * 混合后整行推送，既保留抗锯齿边缘，又不必逐像素发起 SPI 事务。
 *
 * @param bitmap 蒙版位图。
 * @param centerX 水平中心坐标。
 * @param centerY 垂直中心坐标。
 * @param color 前景色。
 */
void drawAlphaBitmap(
    const AlphaBitmap& bitmap,
    int16_t centerX,
    int16_t centerY,
    uint16_t color
) {
  constexpr uint16_t kMaxRowPixels = 128;
  if (bitmap.width > kMaxRowPixels) {
    return;
  }

  static uint16_t row[kMaxRowPixels];
  const int16_t left = centerX - bitmap.width / 2;
  const int16_t top = centerY - bitmap.height / 2;

  // pushImage 会把缓冲区按内存字节直接推给屏幕，而 ESP32 是小端、面板要求高
  // 字节在前。不开字节交换的话 RGB565 高低位会反过来：纯白因为对称看不出问题，
  // 但背景色和品牌橙会变成完全不同的颜色，表现为图标周围出现一个彩色方框。
  display.setSwapBytes(true);
  for (uint16_t y = 0; y < bitmap.height; ++y) {
    const uint8_t* source = bitmap.alpha + static_cast<uint32_t>(y) * bitmap.width;
    for (uint16_t x = 0; x < bitmap.width; ++x) {
      row[x] = blendRgb565(source[x], color, Colors::kBackground);
    }
    display.pushImage(left, top + y, bitmap.width, 1, row);
  }
  display.setSwapBytes(false);
}

/**
 * @brief 把 Token 数格式化为 K / M / B 紧凑写法，不带单位后缀。
 *
 * 只有真的到了十亿量级才切 B：234 M 写成 0.23 B 会丢掉一位有效数字，而三栏
 * 的宽度放得下 "999.99 M"，没有必要提前换单位。数值和单位之间保留一个空格，
 * 让今日行与三栏的量纲更容易识别。
 *
 * @param tokens 原始 Token 数。
 * @param out 输出缓冲区。
 * @param size 缓冲区长度。
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

/**
 * @brief 今日 Token 数：紧凑写法加 "tokens" 后缀。
 *
 * 后缀只出现在今日这一行。它替整屏的四个 Token 数做了图例，三栏里再各写一遍
 * 既挤不下，也只是重复。
 *
 * @param tokens 原始 Token 数。
 * @param out 输出缓冲区。
 * @param size 缓冲区长度。
 */
void formatTokens(uint64_t tokens, char* out, size_t size) {
  char compact[12];
  formatTokensCompact(tokens, compact, sizeof(compact));
  snprintf(out, size, "%s tokens", compact);
}

/**
 * @brief 把周期金额格式化为整元。
 *
 * 三栏并排后每栏只有约 107px，而 Font4 下 "$1651.86" 要 105px，留不出栏间距，
 * 上万之后更会直接顶到隔栏。周期金额是用来看量级的，分位没有意义，抹掉之后
 * "$999999" 也只占 89px，按当前用量足够用几十年。
 *
 * 今日金额不走这里：它是屏幕的视觉重心，仍然保留分位。
 *
 * @param costUsd 金额。
 * @param out 输出缓冲区。
 * @param size 缓冲区长度。
 */
void formatPeriodCost(float costUsd, char* out, size_t size) {
  // 负数在这里没有物理意义（成本不会为负），但真出现时截到 0 比显示一个
  // 负号更不容易让人误判成故障。
  const float clamped = costUsd < 0.0f ? 0.0f : costUsd;
  snprintf(out, size, "$%.0f", clamped);
}

/**
 * @brief 只在内容变化时重绘一块文字区域。
 *
 * 先按上一次的字符串宽度擦除旧包围盒，再画新内容，因此数字变短时不会留下
 * 残影，也不需要重绘整屏。粗体槽位会在右侧多占 1px，擦除时同步覆盖这部分。
 */
void updateSlot(TextSlot& slot, const char* text) {
  if (strcmp(slot.current, text) == 0) {
    return;
  }

  if (slot.current[0] != '\0') {
    const int16_t previousWidth = display.textWidth(slot.current, slot.font);
    const int16_t height = display.fontHeight(slot.font);
    const int16_t boldExtraWidth = slot.bold ? 1 : 0;
    display.fillRect(
        slot.centerX - previousWidth / 2 - 2,
        slot.centerY - height / 2 - 2,
        previousWidth + 4 + boldExtraWidth,
        height + 4,
        Colors::kBackground
    );
  }

  strncpy(slot.current, text, sizeof(slot.current) - 1);
  slot.current[sizeof(slot.current) - 1] = '\0';

  display.setTextDatum(MC_DATUM);
  display.setTextColor(slot.color, Colors::kBackground);
  display.drawString(text, slot.centerX, slot.centerY, slot.font);

  if (slot.bold) {
    // 第二遍必须透明绘制；若继续带背景色，会先擦掉第一遍右侧刚加出的笔画。
    display.setTextColor(slot.color);
    display.drawString(text, slot.centerX + 1, slot.centerY, slot.font);
  }
}

/**
 * @brief 取得周期 Token 中单个字符对应的 alpha 字形。
 *
 * @param character formatTokensCompact 可能输出的数字、小数点或单位字符。
 * @return 对应字形；字符不在紧凑格式集合中时返回 nullptr。
 */
const AlphaBitmap* periodTokenGlyph(char character) {
  if (character >= '0' && character <= '9') {
    return Assets::kPeriodTokenDigits[character - '0'];
  }

  switch (character) {
    case '.':
      return &Assets::kPeriodTokenGlyphDot;
    case 'K':
      return &Assets::kPeriodTokenGlyphK;
    case 'M':
      return &Assets::kPeriodTokenGlyphM;
    case 'B':
      return &Assets::kPeriodTokenGlyphB;
    default:
      return nullptr;
  }
}

/**
 * @brief 取得周期 Token 单个字符的横向前进宽度。
 *
 * 空格不需要位图，只推进生成器按同一字体量出的 4px；其他字符使用实际字形宽度。
 *
 * @param character 紧凑 Token 文本中的字符。
 * @return 字符占用的像素宽度，未知字符返回 0。
 */
int16_t periodTokenCharacterWidth(char character) {
  if (character == ' ') {
    return Assets::kPeriodTokenSpaceWidth;
  }

  const AlphaBitmap* glyph = periodTokenGlyph(character);
  return glyph == nullptr ? 0 : glyph->width;
}

/**
 * @brief 计算一段紧凑 Token 文本使用自定义字形时的像素宽度。
 *
 * @param text formatTokensCompact 生成的字符串。
 * @return 所有有效字形宽度之和。
 */
int16_t periodTokenTextWidth(const char* text) {
  int16_t width = 0;
  for (const char* character = text; *character != '\0'; ++character) {
    width += periodTokenCharacterWidth(*character);
  }
  return width;
}

/**
 * @brief 只在内容变化时重绘一栏粗体周期 Token。
 *
 * 新字形是 22px SF Pro Bold 的抗锯齿蒙版，实际笔画高 16px。擦除区域按上一次
 * 的真实宽度加 2px 外边距计算，既能清掉抗锯齿边缘，也不会覆盖相邻金额行。
 *
 * @param slot 要更新的周期 Token 区域。
 * @param text formatTokensCompact 生成的紧凑文本。
 */
void updatePeriodTokenSlot(PeriodTokenSlot& slot, const char* text) {
  if (strcmp(slot.current, text) == 0) {
    return;
  }

  if (slot.width > 0) {
    const int16_t bandHeight = Assets::kPeriodTokenGlyphHeight + 4;
    display.fillRect(slot.left - 2, slot.centerY - bandHeight / 2,
                     slot.width + 4, bandHeight, Colors::kBackground);
  }

  strncpy(slot.current, text, sizeof(slot.current) - 1);
  slot.current[sizeof(slot.current) - 1] = '\0';
  slot.width = periodTokenTextWidth(slot.current);
  slot.left = slot.centerX - slot.width / 2;

  int16_t cursor = slot.left;
  for (const char* character = slot.current; *character != '\0'; ++character) {
    // 空格只移动游标，不推送透明位图，减少一次无意义的 SPI 绘制。
    if (*character == ' ') {
      cursor += Assets::kPeriodTokenSpaceWidth;
      continue;
    }

    const AlphaBitmap* glyph = periodTokenGlyph(*character);
    if (glyph == nullptr) {
      continue;
    }

    // 每个字形按自己的格子中心绘制，数字等宽、单位保持自然宽度。
    drawAlphaBitmap(*glyph, cursor + glyph->width / 2, slot.centerY, slot.color);
    cursor += glyph->width;
  }
}

/**
 * @brief 重绘今日美元金额。
 *
 * 内置 Font 6 是 1 位点阵、没有粗体字重，所以金额改用 tools/generate_assets.py
 * 离线烘焙的 SF Pro Bold 字形（绿色、抗锯齿）。数字被统一到等宽格子里，先累加
 * 出总宽度再整体居中，位数变化时也不会跑偏、刷新时也不会左右跳动。
 */
void updateTodayCost(float costUsd) {
  char amount[16];
  snprintf(amount, sizeof(amount), "%.2f", costUsd);
  if (strcmp(g_todayCostCurrent, amount) == 0) {
    return;
  }
  strncpy(g_todayCostCurrent, amount, sizeof(g_todayCostCurrent) - 1);
  g_todayCostCurrent[sizeof(g_todayCostCurrent) - 1] = '\0';

  const int16_t bandHeight = Assets::kCostGlyphHeight + 8;
  const int16_t bandTop = Layout::kTodayCostY - bandHeight / 2;

  // 只擦掉上一次金额真正占用的矩形，位数变少时也不会留残影。金额上方的标题行
  // 靠 kHeaderY 与 kTodayCostY 的间距避让，这里不需要按整行清屏。
  if (g_todayCostWidth > 0) {
    display.fillRect(g_todayCostLeft, bandTop, g_todayCostWidth, bandHeight,
                     Colors::kBackground);
  }

  // 先按字形格子累加出金额宽度：数字用统一格子宽，小数点用自己的窄格子。
  constexpr int16_t kGap = 6;
  int16_t amountWidth = 0;
  for (const char* c = amount; *c != '\0'; ++c) {
    amountWidth += (*c == '.') ? Assets::kCostGlyphDot.width
                               : Assets::kCostDigitCellWidth;
  }
  const int16_t totalWidth = Assets::kCostCurrency.width + kGap + amountWidth;
  const int16_t startX = (display.width() - totalWidth) / 2;

  g_todayCostLeft = startX;
  g_todayCostWidth = totalWidth;

  // 美元符号比数字小一档，垂直居中对齐到金额中心。
  drawAlphaBitmap(Assets::kCostCurrency,
                  startX + Assets::kCostCurrency.width / 2,
                  Layout::kTodayCostY, Colors::kTodayCost);

  // 逐个字形按格子平铺，每个格子内居中，位数不变时每位的位置都固定。
  int16_t cursor = startX + Assets::kCostCurrency.width + kGap;
  for (const char* c = amount; *c != '\0'; ++c) {
    const bool isDot = (*c == '.');
    const AlphaBitmap& glyph =
        isDot ? Assets::kCostGlyphDot : *Assets::kCostDigits[*c - '0'];
    const int16_t cell =
        isDot ? Assets::kCostGlyphDot.width : Assets::kCostDigitCellWidth;
    drawAlphaBitmap(glyph, cursor + cell / 2, Layout::kTodayCostY,
                    Colors::kTodayCost);
    cursor += cell;
  }
}

/**
 * @brief 绘制开机后不再变化的静态部分：背景、图标、中文标签和分隔线。
 */
void drawStaticLayout() {
  const int16_t screenWidth = display.width();

  display.fillScreen(Colors::kBackground);
  display.setTextWrap(false);

  // 标题行：「今日消耗」居中，OpenAI 在左、Claude 在右，三者共用一条基线。
  // 图标中心由标签与图标的实际宽度推出，蒙版尺寸变了也仍然对称。
  const int16_t labelHalf = Assets::kTextToday.width / 2;
  const int16_t openAiOffset =
      labelHalf + Layout::kHeaderGap + Assets::kLogoOpenAi.width / 2;
  const int16_t claudeOffset =
      labelHalf + Layout::kHeaderGap + Assets::kLogoClaude.width / 2;

  drawAlphaBitmap(Assets::kTextToday, Layout::kCenterX, Layout::kHeaderY,
                  Colors::kSecondaryText);

  // OpenAI 图标用次级灰而不是纯白，避免与今日金额争夺注意力；
  // Claude 图标沿用 SVG 自带的品牌橙。
  drawAlphaBitmap(Assets::kLogoOpenAi, Layout::kCenterX - openAiOffset,
                  Layout::kHeaderY, Colors::kSecondaryText);
  drawAlphaBitmap(Assets::kLogoClaude, Layout::kCenterX + claudeOffset,
                  Layout::kHeaderY, Assets::kLogoClaudeColor);

  // 一条细分隔线明确主次层级，同时保持没有图标之外的朴素风格。
  display.drawFastHLine(24, Layout::kDividerY, screenWidth - 48,
                        Colors::kDivider);

  drawAlphaBitmap(Assets::kTextWeek, Layout::kWeekColumnX,
                  Layout::kPeriodLabelY, Colors::kSecondaryText);
  drawAlphaBitmap(Assets::kTextMonth, Layout::kMonthColumnX,
                  Layout::kPeriodLabelY, Colors::kSecondaryText);
  drawAlphaBitmap(Assets::kTextTotal, Layout::kTotalColumnX,
                  Layout::kPeriodLabelY, Colors::kSecondaryText);
}

/**
 * @brief 按最新数据刷新所有动态数值。
 */
void renderDashboard(const DashboardData& data) {
  updateTodayCost(data.todayCostUsd);

  char tokens[24];
  formatTokens(data.todayTokens, tokens, sizeof(tokens));
  updateSlot(g_todayTokensSlot, tokens);

  char periodCost[16];
  formatPeriodCost(data.weekCostUsd, periodCost, sizeof(periodCost));
  updateSlot(g_weekCostSlot, periodCost);

  formatPeriodCost(data.monthCostUsd, periodCost, sizeof(periodCost));
  updateSlot(g_monthCostSlot, periodCost);

  formatPeriodCost(data.totalCostUsd, periodCost, sizeof(periodCost));
  updateSlot(g_totalCostSlot, periodCost);

  char periodTokens[12];
  formatTokensCompact(data.weekTokens, periodTokens, sizeof(periodTokens));
  updatePeriodTokenSlot(g_weekTokensSlot, periodTokens);

  formatTokensCompact(data.monthTokens, periodTokens, sizeof(periodTokens));
  updatePeriodTokenSlot(g_monthTokensSlot, periodTokens);

  formatTokensCompact(data.totalTokens, periodTokens, sizeof(periodTokens));
  updatePeriodTokenSlot(g_totalTokensSlot, periodTokens);

  // 状态文字翻转时 updateSlot 会重绘；先切颜色即可让在线和离线有明确色彩提示。
  g_footnoteSlot.color =
      data.online ? Colors::kStatusOnline : Colors::kStatusOffline;
  updateSlot(g_footnoteSlot, data.online ? "LIVE" : "OFFLINE");
}

void setup() {
  Serial.begin(115200);
  delay(800);
  Serial.println();
  Serial.println("[boot] DeskBurn starting");

  pinMode(Hardware::kBacklightGate, OUTPUT);
  digitalWrite(Hardware::kBacklightGate, HIGH);

  display.init();
  display.setRotation(1);
  Serial.printf("[display] initialized, %dx%d\n", display.width(),
                display.height());

  drawStaticLayout();
  Serial.println("[display] static layout drawn");

  Link::begin();
}

/// 把链路状态转成一次渲染所需的数据。
///
/// 链路上传的是千 token（见 link_protocol.h），这里乘回原始量级再交给格式化，
/// 显示层就不必知道传输精度。屏幕最细只显示到 0.01M，千位以下的零头看不出来。
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

/**
 * @brief 渲染最新的链路数据。
 *
 * 每秒轮询一次而不是在 BLE 回调里直接画：SPI 刷屏在回调里跑会拖住蓝牙栈，
 * 而且 OFFLINE 状态的切换是超时驱动的，没有对应的回调可挂。
 *
 * renderDashboard 内部按区域比对，数值没变就不重绘，因此这个轮询频率不会
 * 造成多余的 SPI 流量或闪烁。
 */
void loop() {
  const DashboardData data = currentData();

  // 只在状态翻转时打一行日志。每秒都打会把收包日志淹掉。
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
