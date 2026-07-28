/**
 * @file pin_scanner.cpp
 * @brief 运行时扫描 LCD 控制器型号与 SPI 时钟引脚的诊断固件。
 *
 * TFT_eSPI 的引脚和驱动都是编译期常量，每验证一种组合就要重新烧录一次。
 * 这里改为自己实现最小 SPI 驱动，把引脚和初始化序列都放到运行时决定，
 * 于是一次烧录就能轮流试完所有候选组合。
 *
 * 每种组合会先刷一屏纯红确认像素通路，再画出写死的 Token 仪表盘，
 * 并在左上角标出组合编号。屏幕一旦出现画面，编号即对应正确配置。
 */

#include <Arduino.h>
#include <SPI.h>

namespace {

// 这几个引脚在此前的排障中一直没有变化，本轮只扫描时钟引脚和控制器型号。
constexpr int8_t kPinMosi = 3;
constexpr int8_t kPinCs = 7;
constexpr int8_t kPinDc = 4;
constexpr int8_t kPinRst = 5;
constexpr int8_t kPinBacklight = 6;

// 长排线上先用保守时钟，排除信号完整性带来的干扰。
constexpr uint32_t kSpiWriteHz = 20000000;
// 多数控制器的寄存器回读上限远低于写入频率，读 ID 时单独降速。
constexpr uint32_t kSpiReadHz = 4000000;

constexpr uint32_t kHoldMillis = 4000;

enum class Controller : uint8_t { kSt7796, kIli9488, kSt7789 };

/// 一组待验证的「控制器型号 + 时钟引脚 + 分辨率」组合。
struct Candidate {
  Controller controller;
  int8_t sclk;
  // 板载默认 SPI 把 GPIO2/GPIO10 分别用作 SCK 和 MISO，两者互换即另一种假设。
  int8_t miso;
  int16_t width;
  int16_t height;
  // 整屏底色和巨大字母，用来在几步之外也能分清当前是哪一组。
  uint16_t bannerColor;
  const char* banner;
  const char* label;
};

// 第一轮六组扫描已确认走通用初始化序列（登记为 ST7789）的那一组能出画面，
// 只剩时钟引脚没看清。这里只保留两种时钟假设，用底色区分。
constexpr Candidate kCandidates[] = {
    {Controller::kSt7789, 2, 10, 320, 240, 0x001F, "A", "ST7789 CLK2"},
    {Controller::kSt7789, 10, 2, 320, 240, 0x07E0, "B", "ST7789 CLK10"},
};

constexpr size_t kCandidateCount = sizeof(kCandidates) / sizeof(kCandidates[0]);

// ---------------------------------------------------------------------------
// 初始化序列
//
// 统一编码为字节流：命令, 参数个数, 参数...
// 参数个数的 bit7 置位表示该命令后还跟一个字节的延时（毫秒）。
// ---------------------------------------------------------------------------

constexpr uint8_t kDelayFlag = 0x80;

const uint8_t kSt7796Init[] = {
    0x01, kDelayFlag | 0, 120,  // SWRESET
    0x11, kDelayFlag | 0, 120,  // SLPOUT
    0xF0, 1, 0xC3,              // 打开厂商命令集
    0xF0, 1, 0x96,
    0x36, 1, 0x28,  // MADCTL：横屏 + BGR
    0x3A, 1, 0x55,  // COLMOD：16 位
    0xB4, 1, 0x01,
    0xB6, 3, 0x80, 0x02, 0x3B,
    0xE8, 8, 0x40, 0x8A, 0x00, 0x00, 0x29, 0x19, 0xA5, 0x33,
    0xC1, 1, 0x06,
    0xC2, 1, 0xA7,
    0xC5, 1, 0x18,
    0xE0, 14, 0xF0, 0x09, 0x0B, 0x06, 0x04, 0x15, 0x2F, 0x54, 0x42, 0x3C, 0x17,
    0x14, 0x18, 0x1B,
    0xE1, 14, 0xE0, 0x09, 0x0B, 0x06, 0x04, 0x03, 0x2B, 0x43, 0x42, 0x3B, 0x16,
    0x14, 0x17, 0x1B,
    0xF0, 1, 0x3C,  // 关闭厂商命令集
    0xF0, 1, 0x69,
    0x29, kDelayFlag | 0, 120,  // DISPON
};

const uint8_t kIli9488Init[] = {
    0x01, kDelayFlag | 0, 120,  // SWRESET
    0xE0, 15, 0x00, 0x03, 0x09, 0x08, 0x16, 0x0A, 0x3F, 0x78, 0x4C, 0x09, 0x0A,
    0x08, 0x16, 0x1A, 0x0F,
    0xE1, 15, 0x00, 0x16, 0x19, 0x03, 0x0F, 0x05, 0x32, 0x45, 0x46, 0x04, 0x0E,
    0x0D, 0x35, 0x37, 0x0F,
    0xC0, 2, 0x17, 0x15,
    0xC1, 1, 0x41,
    0xC5, 3, 0x00, 0x12, 0x80,
    0x36, 1, 0x28,  // MADCTL：横屏 + BGR
    0x3A, 1, 0x66,  // ILI9488 在 SPI 下只支持 18 位色
    0xB0, 1, 0x00,
    0xB1, 1, 0xA0,
    0xB4, 1, 0x02,
    0xB6, 2, 0x02, 0x02,
    0xE9, 1, 0x00,
    0xF7, 4, 0xA9, 0x51, 0x2C, 0x82,
    0x11, kDelayFlag | 0, 120,  // SLPOUT
    0x29, kDelayFlag | 0, 120,  // DISPON
};

const uint8_t kSt7789Init[] = {
    0x01, kDelayFlag | 0, 150,  // SWRESET
    0x11, kDelayFlag | 0, 120,  // SLPOUT
    0x3A, 1, 0x55,              // COLMOD：16 位
    0x36, 1, 0x60,              // MADCTL：横屏
    0x21, kDelayFlag | 0, 10,   // INVON，ST7789 面板默认需要反显
    0x13, kDelayFlag | 0, 10,   // NORON
    0x29, kDelayFlag | 0, 120,  // DISPON
};

// ---------------------------------------------------------------------------
// 5x7 点阵字体，覆盖 ASCII 0x20-0x5F，够画金额、Token 数和标签。
// 每字符 5 字节，按列存储，最低位在上。
// ---------------------------------------------------------------------------
const uint8_t kFont5x7[][5] = {
    {0x00, 0x00, 0x00, 0x00, 0x00},  // ' '
    {0x00, 0x00, 0x5F, 0x00, 0x00},  // '!'
    {0x00, 0x07, 0x00, 0x07, 0x00},  // '"'
    {0x14, 0x7F, 0x14, 0x7F, 0x14},  // '#'
    {0x24, 0x2A, 0x7F, 0x2A, 0x12},  // '$'
    {0x23, 0x13, 0x08, 0x64, 0x62},  // '%'
    {0x36, 0x49, 0x55, 0x22, 0x50},  // '&'
    {0x00, 0x05, 0x03, 0x00, 0x00},  // '''
    {0x00, 0x1C, 0x22, 0x41, 0x00},  // '('
    {0x00, 0x41, 0x22, 0x1C, 0x00},  // ')'
    {0x14, 0x08, 0x3E, 0x08, 0x14},  // '*'
    {0x08, 0x08, 0x3E, 0x08, 0x08},  // '+'
    {0x00, 0x50, 0x30, 0x00, 0x00},  // ','
    {0x08, 0x08, 0x08, 0x08, 0x08},  // '-'
    {0x00, 0x60, 0x60, 0x00, 0x00},  // '.'
    {0x20, 0x10, 0x08, 0x04, 0x02},  // '/'
    {0x3E, 0x51, 0x49, 0x45, 0x3E},  // '0'
    {0x00, 0x42, 0x7F, 0x40, 0x00},  // '1'
    {0x42, 0x61, 0x51, 0x49, 0x46},  // '2'
    {0x21, 0x41, 0x45, 0x4B, 0x31},  // '3'
    {0x18, 0x14, 0x12, 0x7F, 0x10},  // '4'
    {0x27, 0x45, 0x45, 0x45, 0x39},  // '5'
    {0x3C, 0x4A, 0x49, 0x49, 0x30},  // '6'
    {0x01, 0x71, 0x09, 0x05, 0x03},  // '7'
    {0x36, 0x49, 0x49, 0x49, 0x36},  // '8'
    {0x06, 0x49, 0x49, 0x29, 0x1E},  // '9'
    {0x00, 0x36, 0x36, 0x00, 0x00},  // ':'
    {0x00, 0x56, 0x36, 0x00, 0x00},  // ';'
    {0x08, 0x14, 0x22, 0x41, 0x00},  // '<'
    {0x14, 0x14, 0x14, 0x14, 0x14},  // '='
    {0x00, 0x41, 0x22, 0x14, 0x08},  // '>'
    {0x02, 0x01, 0x51, 0x09, 0x06},  // '?'
    {0x32, 0x49, 0x79, 0x41, 0x3E},  // '@'
    {0x7E, 0x11, 0x11, 0x11, 0x7E},  // 'A'
    {0x7F, 0x49, 0x49, 0x49, 0x36},  // 'B'
    {0x3E, 0x41, 0x41, 0x41, 0x22},  // 'C'
    {0x7F, 0x41, 0x41, 0x22, 0x1C},  // 'D'
    {0x7F, 0x49, 0x49, 0x49, 0x41},  // 'E'
    {0x7F, 0x09, 0x09, 0x09, 0x01},  // 'F'
    {0x3E, 0x41, 0x49, 0x49, 0x7A},  // 'G'
    {0x7F, 0x08, 0x08, 0x08, 0x7F},  // 'H'
    {0x00, 0x41, 0x7F, 0x41, 0x00},  // 'I'
    {0x20, 0x40, 0x41, 0x3F, 0x01},  // 'J'
    {0x7F, 0x08, 0x14, 0x22, 0x41},  // 'K'
    {0x7F, 0x40, 0x40, 0x40, 0x40},  // 'L'
    {0x7F, 0x02, 0x0C, 0x02, 0x7F},  // 'M'
    {0x7F, 0x04, 0x08, 0x10, 0x7F},  // 'N'
    {0x3E, 0x41, 0x41, 0x41, 0x3E},  // 'O'
    {0x7F, 0x09, 0x09, 0x09, 0x06},  // 'P'
    {0x3E, 0x41, 0x51, 0x21, 0x5E},  // 'Q'
    {0x7F, 0x09, 0x19, 0x29, 0x46},  // 'R'
    {0x46, 0x49, 0x49, 0x49, 0x31},  // 'S'
    {0x01, 0x01, 0x7F, 0x01, 0x01},  // 'T'
    {0x3F, 0x40, 0x40, 0x40, 0x3F},  // 'U'
    {0x1F, 0x20, 0x40, 0x20, 0x1F},  // 'V'
    {0x3F, 0x40, 0x38, 0x40, 0x3F},  // 'W'
    {0x63, 0x14, 0x08, 0x14, 0x63},  // 'X'
    {0x07, 0x08, 0x70, 0x08, 0x07},  // 'Y'
    {0x61, 0x51, 0x49, 0x45, 0x43},  // 'Z'
    {0x00, 0x7F, 0x41, 0x41, 0x00},  // '['
    {0x02, 0x04, 0x08, 0x10, 0x20},  // '\'
    {0x00, 0x41, 0x41, 0x7F, 0x00},  // ']'
    {0x04, 0x02, 0x01, 0x02, 0x04},  // '^'
    {0x40, 0x40, 0x40, 0x40, 0x40},  // '_'
};

// ---------------------------------------------------------------------------
// 当前生效的运行时配置
// ---------------------------------------------------------------------------

Controller g_controller = Controller::kSt7796;
int16_t g_width = 480;
int16_t g_height = 320;
// ILI9488 在 SPI 下只能收 18 位色，每像素要发 3 字节而不是 2 字节。
bool g_uses18BitColor = false;

constexpr uint16_t kColorBackground = 0x0841;
constexpr uint16_t kColorPrimary = 0xFFFF;
constexpr uint16_t kColorSecondary = 0xAD55;
constexpr uint16_t kColorDivider = 0x2945;
constexpr uint16_t kColorRed = 0xF800;

void beginWrite() {
  SPI.beginTransaction(SPISettings(kSpiWriteHz, MSBFIRST, SPI_MODE0));
  digitalWrite(kPinCs, LOW);
}

void endWrite() {
  digitalWrite(kPinCs, HIGH);
  SPI.endTransaction();
}

void writeCommandByte(uint8_t command) {
  digitalWrite(kPinDc, LOW);
  SPI.transfer(command);
  digitalWrite(kPinDc, HIGH);
}

/// 按上面的字节流编码执行一整段初始化序列。
void runInitSequence(const uint8_t* sequence, size_t length) {
  size_t offset = 0;
  while (offset < length) {
    const uint8_t command = sequence[offset++];
    const uint8_t header = sequence[offset++];
    const uint8_t argCount = header & static_cast<uint8_t>(~kDelayFlag);

    beginWrite();
    writeCommandByte(command);
    for (uint8_t index = 0; index < argCount; ++index) {
      SPI.transfer(sequence[offset + index]);
    }
    endWrite();
    offset += argCount;

    if (header & kDelayFlag) {
      delay(sequence[offset++]);
    }
  }
}

void setAddressWindow(int16_t x0, int16_t y0, int16_t x1, int16_t y1) {
  beginWrite();
  writeCommandByte(0x2A);  // CASET
  SPI.transfer(static_cast<uint8_t>(x0 >> 8));
  SPI.transfer(static_cast<uint8_t>(x0));
  SPI.transfer(static_cast<uint8_t>(x1 >> 8));
  SPI.transfer(static_cast<uint8_t>(x1));
  endWrite();

  beginWrite();
  writeCommandByte(0x2B);  // RASET
  SPI.transfer(static_cast<uint8_t>(y0 >> 8));
  SPI.transfer(static_cast<uint8_t>(y0));
  SPI.transfer(static_cast<uint8_t>(y1 >> 8));
  SPI.transfer(static_cast<uint8_t>(y1));
  endWrite();
}

void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
  if (w <= 0 || h <= 0 || x >= g_width || y >= g_height) {
    return;
  }
  if (x + w > g_width) {
    w = g_width - x;
  }
  if (y + h > g_height) {
    h = g_height - y;
  }

  setAddressWindow(x, y, x + w - 1, y + h - 1);

  // 预先拼好一段重复像素，按块发送，避免逐像素调用 SPI。
  constexpr size_t kChunkPixels = 128;
  static uint8_t chunk[kChunkPixels * 3];
  const uint8_t bytesPerPixel = g_uses18BitColor ? 3 : 2;

  if (g_uses18BitColor) {
    const uint8_t red = static_cast<uint8_t>((color >> 11) & 0x1F) << 3;
    const uint8_t green = static_cast<uint8_t>((color >> 5) & 0x3F) << 2;
    const uint8_t blue = static_cast<uint8_t>(color & 0x1F) << 3;
    for (size_t index = 0; index < kChunkPixels; ++index) {
      chunk[index * 3 + 0] = red;
      chunk[index * 3 + 1] = green;
      chunk[index * 3 + 2] = blue;
    }
  } else {
    for (size_t index = 0; index < kChunkPixels; ++index) {
      chunk[index * 2 + 0] = static_cast<uint8_t>(color >> 8);
      chunk[index * 2 + 1] = static_cast<uint8_t>(color);
    }
  }

  beginWrite();
  writeCommandByte(0x2C);  // RAMWR
  uint32_t remaining = static_cast<uint32_t>(w) * static_cast<uint32_t>(h);
  while (remaining > 0) {
    const size_t batch =
        remaining > kChunkPixels ? kChunkPixels : static_cast<size_t>(remaining);
    SPI.writeBytes(chunk, batch * bytesPerPixel);
    remaining -= batch;
  }
  endWrite();
}

void fillScreen(uint16_t color) { fillRect(0, 0, g_width, g_height, color); }

int16_t textWidth(const char* text, uint8_t scale) {
  // 每个字符 5 列点阵加 1 列字距。
  return static_cast<int16_t>(strlen(text)) * 6 * scale;
}

void drawText(
    const char* text,
    int16_t x,
    int16_t y,
    uint8_t scale,
    uint16_t color
) {
  int16_t cursor = x;
  for (const char* character = text; *character != '\0'; ++character) {
    const char value = *character;
    if (value >= 0x20 && value <= 0x5F) {
      const uint8_t* glyph = kFont5x7[value - 0x20];
      for (uint8_t column = 0; column < 5; ++column) {
        for (uint8_t row = 0; row < 7; ++row) {
          if (glyph[column] & (1 << row)) {
            fillRect(
                cursor + column * scale,
                y + row * scale,
                scale,
                scale,
                color
            );
          }
        }
      }
    }
    cursor += 6 * scale;
  }
}

void drawCenteredText(
    const char* text,
    int16_t centerX,
    int16_t y,
    uint8_t scale,
    uint16_t color
) {
  drawText(text, centerX - textWidth(text, scale) / 2, y, scale, color);
}

/// 尝试回读控制器 ID（命令 0xD3）。屏幕若没接 MISO 会读到全 0 或全 FF。
uint32_t readControllerId(int8_t misoPin) {
  if (misoPin < 0) {
    return 0;
  }

  SPI.beginTransaction(SPISettings(kSpiReadHz, MSBFIRST, SPI_MODE0));
  digitalWrite(kPinCs, LOW);
  digitalWrite(kPinDc, LOW);
  SPI.transfer(0xD3);
  digitalWrite(kPinDc, HIGH);

  // 第一个字节是控制器插入的哑元，随后三字节才是厂商 ID。
  SPI.transfer(0x00);
  const uint8_t high = SPI.transfer(0x00);
  const uint8_t middle = SPI.transfer(0x00);
  const uint8_t low = SPI.transfer(0x00);

  digitalWrite(kPinCs, HIGH);
  SPI.endTransaction();

  return (static_cast<uint32_t>(high) << 16) |
         (static_cast<uint32_t>(middle) << 8) | low;
}

/// 按候选组合重新配置 SPI、复位屏幕并执行对应的初始化序列。
void applyCandidate(const Candidate& candidate) {
  g_controller = candidate.controller;
  g_uses18BitColor = candidate.controller == Controller::kIli9488;
  g_width = candidate.width;
  g_height = candidate.height;

  pinMode(kPinCs, OUTPUT);
  pinMode(kPinDc, OUTPUT);
  pinMode(kPinRst, OUTPUT);
  digitalWrite(kPinCs, HIGH);
  digitalWrite(kPinDc, HIGH);

  SPI.end();
  SPI.begin(candidate.sclk, candidate.miso, kPinMosi, -1);

  // 硬复位，确保上一轮组合留下的状态不会影响这一轮。
  digitalWrite(kPinRst, HIGH);
  delay(10);
  digitalWrite(kPinRst, LOW);
  delay(20);
  digitalWrite(kPinRst, HIGH);
  delay(150);

  switch (candidate.controller) {
    case Controller::kSt7796:
      runInitSequence(kSt7796Init, sizeof(kSt7796Init));
      break;
    case Controller::kIli9488:
      runInitSequence(kIli9488Init, sizeof(kIli9488Init));
      break;
    case Controller::kSt7789:
      runInitSequence(kSt7789Init, sizeof(kSt7789Init));
      break;
  }
}

/// 整屏底色加一个巨大字母，隔着几步也能分清当前组合。
///
/// 同时沿假定分辨率的四条边画白色边框：边框若正好落在物理屏幕边缘，说明
/// 分辨率猜对了；若只占屏幕一角，说明面板比假定的更大。
void drawBanner(const Candidate& candidate) {
  constexpr int16_t kBorderThickness = 6;
  constexpr uint8_t kBannerScale = 16;

  fillScreen(candidate.bannerColor);
  fillRect(0, 0, g_width, kBorderThickness, kColorPrimary);
  fillRect(0, g_height - kBorderThickness, g_width, kBorderThickness, kColorPrimary);
  fillRect(0, 0, kBorderThickness, g_height, kColorPrimary);
  fillRect(g_width - kBorderThickness, 0, kBorderThickness, g_height, kColorPrimary);

  drawCenteredText(candidate.banner, g_width / 2,
                   g_height / 2 - 7 * kBannerScale / 2, kBannerScale,
                   kColorPrimary);
}

/// 画出写死的 Token 仪表盘，同时在角落标出当前组合编号。
void drawDashboard(size_t candidateIndex, const Candidate& candidate) {
  const int16_t centerX = g_width / 2;
  const uint8_t bigScale = g_width >= 480 ? 10 : 7;
  const uint8_t mediumScale = g_width >= 480 ? 4 : 3;
  const uint8_t smallScale = 2;

  fillScreen(kColorBackground);

  // 编号放在角落，屏幕一旦出画面就能直接对应到正确的组合。
  char marker[32];
  snprintf(marker, sizeof(marker), "#%u %s", static_cast<unsigned>(candidateIndex + 1),
           candidate.label);
  drawText(marker, 8, 8, smallScale, kColorDivider);

  drawCenteredText("TODAY", centerX, g_height * 3 / 32, mediumScale, kColorSecondary);
  drawCenteredText("$12.48", centerX, g_height / 4, bigScale, kColorPrimary);
  drawCenteredText("9.64M TOKENS", centerX, g_height * 9 / 16, mediumScale,
                   kColorSecondary);

  fillRect(24, g_height * 11 / 16, g_width - 48, 1, kColorDivider);

  drawCenteredText("THIS WEEK", g_width / 4, g_height * 3 / 4, smallScale,
                   kColorSecondary);
  drawCenteredText("$63.21", g_width / 4, g_height * 13 / 16, mediumScale,
                   kColorPrimary);

  drawCenteredText("THIS MONTH", g_width * 3 / 4, g_height * 3 / 4, smallScale,
                   kColorSecondary);
  drawCenteredText("$218.90", g_width * 3 / 4, g_height * 13 / 16, mediumScale,
                   kColorPrimary);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println();
  Serial.println("[scan] LCD 引脚与控制器扫描固件启动");
  Serial.printf("[scan] 固定引脚 MOSI=%d CS=%d DC=%d RST=%d BL=%d\n", kPinMosi,
                kPinCs, kPinDc, kPinRst, kPinBacklight);
  Serial.printf("[scan] 共 %u 组候选，每组停留 %lu ms\n",
                static_cast<unsigned>(kCandidateCount),
                static_cast<unsigned long>(kHoldMillis));

  // 背光常亮，方便区分「没背光」和「有背光但没画面」。
  pinMode(kPinBacklight, OUTPUT);
  digitalWrite(kPinBacklight, HIGH);
}

void loop() {
  for (size_t index = 0; index < kCandidateCount; ++index) {
    const Candidate& candidate = kCandidates[index];

    Serial.printf("[scan] === #%u %s (SCLK=%d MISO=%d) ===\n",
                  static_cast<unsigned>(index + 1), candidate.label,
                  candidate.sclk, candidate.miso);

    applyCandidate(candidate);

    const uint32_t id = readControllerId(candidate.miso);
    Serial.printf("[scan] #%u 回读 ID 0xD3 = 0x%06X\n",
                  static_cast<unsigned>(index + 1), id);
    if (id != 0x000000 && id != 0xFFFFFF) {
      Serial.printf("[scan] #%u ID 非空，屏幕 MISO 可能已连通\n",
                    static_cast<unsigned>(index + 1));
    }

    // 先亮出底色和巨大字母，确认当前是哪一组，再画仪表盘验证排版。
    drawBanner(candidate);
    Serial.printf("[scan] #%u 已显示 %s 屏，字母 %s\n",
                  static_cast<unsigned>(index + 1),
                  candidate.sclk == 2 ? "蓝色" : "绿色", candidate.banner);
    delay(3500);

    drawDashboard(index, candidate);
    Serial.printf("[scan] #%u 仪表盘已绘制，请观察屏幕\n",
                  static_cast<unsigned>(index + 1));

    delay(kHoldMillis);
  }

  Serial.println("[scan] 一轮扫描结束，重新开始");
}
