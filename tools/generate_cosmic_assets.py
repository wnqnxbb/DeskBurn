#!/usr/bin/env python3
"""生成 Cosmic Buddy 固件资源和 320×240 原生预览。

这套页面严格沿用接受稿的“左侧月夜精灵 + 右侧三座星球数据岛”结构。Image Gen
只负责无文字的生产美术层；品牌、状态、中文标签、金额和 Token 仍由固件动态
绘制。脚本把背景转换为 RGB565，同时生成动态字形与预览，保证实机和文档共用
同一套坐标与资源。

用法：
    .venv/bin/python tools/generate_cosmic_assets.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND_SOURCE = (
    PROJECT_ROOT / "assets" / "cosmic-buddy-background-source.png"
)
BACKGROUND_OUTPUT = (
    PROJECT_ROOT / "assets" / "cosmic-buddy-background.png"
)
OUTPUT_HEADER = (
    PROJECT_ROOT / "firmware" / "deskburn_cosmic" / "cosmic_assets.h"
)
OUTPUT_PREVIEW = (
    PROJECT_ROOT / "docs" / "images" / "cosmic-buddy-preview.png"
)

SF_ROUNDED_FONT = "/System/Library/Fonts/SFNSRounded.ttf"
CHINESE_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
CHINESE_BOLD_INDEX = 2

SCREEN_WIDTH = 320
SCREEN_HEIGHT = 240

# 文字颜色从接受稿取样后按 RGB565 可辨识度微调。奶油白负责金额，薰衣草紫负责
# 层级标签，薄荷绿和珊瑚红只用于实时状态，避免与插画争夺视觉焦点。
COLOR_PRIMARY = (255, 238, 204)
COLOR_LAVENDER = (184, 149, 241)
COLOR_LAVENDER_LIGHT = (222, 203, 255)
COLOR_PEACH = (255, 143, 124)
COLOR_MINT = (116, 241, 180)
COLOR_OFFLINE = (255, 105, 120)

# 以下坐标全部来自 1448×1086 接受稿按 320×240 等比缩放后的视觉锚点。
BRAND_POSITION = (8, 7)
STATUS_DOT_CENTER = (73, 12)
STATUS_POSITION = (80, 7)
TODAY_LABEL_POSITION = (11, 32)

TODAY_CURRENCY_POSITION = (18, 168)
TODAY_AMOUNT_POSITION = (38, 159)
TODAY_TOKENS_POSITION = (37, 207)

PERIOD_LABEL_POSITIONS = ((226, 17), (222, 97), (218, 177))
PERIOD_AMOUNT_RECTS = (
    (244, 28, 68, 18),
    (244, 108, 68, 18),
    (244, 189, 68, 18),
)
PERIOD_TOKEN_RECTS = ((245, 53, 66, 8), (245, 134, 66, 8), (245, 211, 66, 8))


@dataclass(frozen=True)
class RasterFont:
    """一组共享垂直包围盒、可由固件逐字符查找的 alpha 字形。"""

    glyphs: dict[str, Image.Image]
    advances: dict[str, int]
    height: int


def load_rounded_font(
    pixel_size: int,
    variation: str,
) -> ImageFont.FreeTypeFont:
    """加载指定字号和字重的 SF Pro Rounded 可变字体。"""
    font = ImageFont.truetype(SF_ROUNDED_FONT, pixel_size)
    font.set_variation_by_name(variation)
    return font


def crop_to_ink(image: Image.Image) -> Image.Image:
    """裁掉固定资源四周的透明像素，便于按左上角精确定位。"""
    bounds = image.getbbox()
    return image.crop(bounds) if bounds else image


def render_text_bitmap(
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    alpha_gamma: float = 1.0,
) -> Image.Image:
    """把一段固定文字渲染成紧边界 alpha 蒙版。"""
    bounds = font.getbbox(text)
    width = max(1, bounds[2] - bounds[0] + 8)
    height = max(1, bounds[3] - bounds[1] + 8)
    canvas = Image.new("L", (width, height), 0)
    ImageDraw.Draw(canvas).text(
        (4 - bounds[0], 4 - bounds[1]),
        text,
        font=font,
        fill=255,
    )
    if alpha_gamma != 1.0:
        curve = [
            round(255 * (value / 255) ** alpha_gamma)
            for value in range(256)
        ]
        canvas = canvas.point(curve)
    return crop_to_ink(canvas)


def render_decimal_sparkle() -> Image.Image:
    """生成参考图中替代今日金额小数点的四角星 alpha 蒙版。"""
    canvas = Image.new("L", (13, 13), 0)
    ImageDraw.Draw(canvas).polygon(
        ((6, 0), (8, 4), (12, 6), (8, 8),
         (6, 12), (4, 8), (0, 6), (4, 4)),
        fill=255,
    )
    return canvas


def render_raster_font(
    characters: str,
    font: ImageFont.FreeTypeFont,
) -> RasterFont:
    """生成具有统一顶边、底边和基线的动态字形。

    字符保留原字体 advance，数字变化时不会上下跳。空格只记录 advance，不生成
    alpha 数据，固件端也就不需要为它执行 SPI 推送。
    """
    unique_characters = "".join(dict.fromkeys(characters))
    baseline = font.size * 2
    probe_width = font.size * 4
    probe_height = font.size * 4
    top = probe_height
    bottom = 0

    for character in unique_characters:
        if character == " ":
            continue
        probe = Image.new("L", (probe_width, probe_height), 0)
        ImageDraw.Draw(probe).text(
            (font.size, baseline),
            character,
            font=font,
            fill=255,
            anchor="ls",
        )
        bounds = probe.getbbox()
        if bounds:
            top = min(top, bounds[1])
            bottom = max(bottom, bounds[3])

    height = bottom - top
    glyphs: dict[str, Image.Image] = {}
    advances: dict[str, int] = {}
    for character in unique_characters:
        advance = max(1, round(font.getlength(character)))
        advances[character] = advance
        canvas = Image.new("L", (advance, height), 0)
        if character != " ":
            ImageDraw.Draw(canvas).text(
                (advance / 2, baseline - top),
                character,
                font=font,
                fill=255,
                anchor="ms",
            )
        glyphs[character] = canvas

    return RasterFont(glyphs=glyphs, advances=advances, height=height)


def build_background() -> Image.Image:
    """把 Image Gen 无文字源图缩放成屏幕原生背景。

    源图和目标图都是 4:3，因此不裁切任何插画。使用 Lanczos 缩放可在 320×240
    屏幕上保留云岛轮廓与柔和发光，最终 RGB565 转换会自然限制色阶。
    """
    source = Image.open(BACKGROUND_SOURCE).convert("RGB")
    if source.width * 3 != source.height * 4:
        raise RuntimeError(
            f"Cosmic Buddy 背景必须为 4:3，当前是 {source.size}"
        )
    background = source.resize(
        (SCREEN_WIDTH, SCREEN_HEIGHT),
        Image.Resampling.LANCZOS,
    )
    BACKGROUND_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    background.save(BACKGROUND_OUTPUT)
    return background


def build_assets(
) -> tuple[dict[str, Image.Image], dict[str, RasterFont], Image.Image]:
    """创建固定文字、动态字体和原生尺寸背景资源。"""
    chinese_today = ImageFont.truetype(
        CHINESE_FONT,
        22,
        index=CHINESE_BOLD_INDEX,
    )
    chinese_period = ImageFont.truetype(
        CHINESE_FONT,
        12,
        index=CHINESE_BOLD_INDEX,
    )

    bitmaps = {
        "kBrandDeskBurn": render_text_bitmap(
            "DeskBurn",
            load_rounded_font(12, "Bold"),
        ),
        "kStatusLive": render_text_bitmap(
            "LIVE",
            load_rounded_font(9, "Bold"),
        ),
        "kStatusOff": render_text_bitmap(
            "OFF",
            load_rounded_font(9, "Bold"),
        ),
        "kTodayCurrency": render_text_bitmap(
            "$",
            load_rounded_font(30, "Bold"),
        ),
        "kTodayDecimalSparkle": render_decimal_sparkle(),
        "kLabelToday": render_text_bitmap(
            "今日",
            chinese_today,
            alpha_gamma=0.72,
        ),
        "kLabelWeek": render_text_bitmap(
            "本周",
            chinese_period,
            alpha_gamma=0.72,
        ),
        "kLabelMonth": render_text_bitmap(
            "本月",
            chinese_period,
            alpha_gamma=0.72,
        ),
        "kLabelTotal": render_text_bitmap(
            "总计",
            chinese_period,
            alpha_gamma=0.72,
        ),
    }
    fonts = {
        "kTodayAmountFont": render_raster_font(
            "0123456789.KM",
            load_rounded_font(43, "Black"),
        ),
        "kPeriodAmountFont": render_raster_font(
            "$0123456789.KM",
            load_rounded_font(20, "Bold"),
        ),
        "kTodayTokenFont": render_raster_font(
            "0123456789. KMBTtokens",
            load_rounded_font(14, "Medium"),
        ),
        "kPeriodTokenFont": render_raster_font(
            "0123456789. KMBT",
            load_rounded_font(11, "Medium"),
        ),
    }
    return bitmaps, fonts, build_background()


def rgb565(color: tuple[int, int, int]) -> int:
    """把预览使用的 RGB888 颜色转换成固件 RGB565。"""
    red, green, blue = color
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)


def format_byte_array(name: str, image: Image.Image) -> str:
    """把单张 alpha 蒙版输出为 C++ 字节数组和尺寸描述。"""
    values = list(image.tobytes())
    lines = []
    for offset in range(0, len(values), 16):
        chunk = ", ".join(
            f"0x{value:02X}" for value in values[offset:offset + 16]
        )
        lines.append(f"    {chunk},")
    return (
        f"const uint8_t {name}Alpha[] = {{\n"
        + "\n".join(lines)
        + "\n};\n"
        f"const AlphaBitmap {name} = "
        f"{{{name}Alpha, {image.width}, {image.height}}};\n"
    )


def format_rgb565_array(name: str, image: Image.Image) -> str:
    """把一张 RGB 图输出为 TFT_eSPI 可直接推送的 RGB565 数组。"""
    values = [rgb565(pixel) for pixel in image.getdata()]
    lines = []
    for offset in range(0, len(values), 12):
        chunk = ", ".join(
            f"0x{value:04X}" for value in values[offset:offset + 12]
        )
        lines.append(f"    {chunk},")
    return (
        f"const uint16_t {name}Pixels[] = {{\n"
        + "\n".join(lines)
        + "\n};\n"
        f"const RgbBitmap {name} = "
        f"{{{name}Pixels, {image.width}, {image.height}}};\n"
    )


def character_suffix(character: str) -> str:
    """把动态字符转换成稳定且合法的 C++ 标识符后缀。"""
    names = {
        "$": "Currency",
        ".": "Dot",
        " ": "Space",
    }
    if character in names:
        return names[character]
    if character.isdigit():
        return f"Digit{character}"
    return f"CharU{ord(character):04X}"


def format_raster_font(name: str, raster_font: RasterFont) -> str:
    """输出动态字形数组、字符映射和统一字高。"""
    sections: list[str] = []
    entries: list[str] = []
    for character, image in raster_font.glyphs.items():
        suffix = character_suffix(character)
        bitmap_name = f"{name}{suffix}"
        if character == " ":
            entries.append(
                f"    {{' ', {{nullptr, {image.width}, {image.height}}}, "
                f"{raster_font.advances[character]}}},"
            )
            continue
        sections.append(format_byte_array(bitmap_name, image))
        entries.append(
            f"    {{'{character}', {bitmap_name}, "
            f"{raster_font.advances[character]}}},"
        )

    sections.append(
        f"const AlphaGlyph {name}Glyphs[] = {{\n"
        + "\n".join(entries)
        + "\n};\n"
        f"const AlphaFont {name} = "
        f"{{{name}Glyphs, sizeof({name}Glyphs) / "
        f"sizeof({name}Glyphs[0]), {raster_font.height}}};\n"
    )
    return "\n".join(sections)


def write_header(
    bitmaps: dict[str, Image.Image],
    fonts: dict[str, RasterFont],
    background: Image.Image,
) -> None:
    """写出 Cosmic Buddy 固件专用资源头文件。"""
    sections = [
        "// 此文件由 tools/generate_cosmic_assets.py 自动生成，请勿手工修改。",
        "#pragma once",
        "",
        "#include <stdint.h>",
        "",
        "namespace CosmicAssets {",
        "",
        "struct AlphaBitmap {",
        "  const uint8_t* alpha;",
        "  uint16_t width;",
        "  uint16_t height;",
        "};",
        "",
        "struct AlphaGlyph {",
        "  char character;",
        "  AlphaBitmap bitmap;",
        "  uint16_t advance;",
        "};",
        "",
        "struct AlphaFont {",
        "  const AlphaGlyph* glyphs;",
        "  uint16_t count;",
        "  uint16_t height;",
        "};",
        "",
        "struct RgbBitmap {",
        "  const uint16_t* pixels;",
        "  uint16_t width;",
        "  uint16_t height;",
        "};",
        "",
        f"constexpr uint16_t kColorPrimary = "
        f"0x{rgb565(COLOR_PRIMARY):04X};",
        f"constexpr uint16_t kColorLavender = "
        f"0x{rgb565(COLOR_LAVENDER):04X};",
        f"constexpr uint16_t kColorLavenderLight = "
        f"0x{rgb565(COLOR_LAVENDER_LIGHT):04X};",
        f"constexpr uint16_t kColorPeach = "
        f"0x{rgb565(COLOR_PEACH):04X};",
        f"constexpr uint16_t kColorMint = "
        f"0x{rgb565(COLOR_MINT):04X};",
        f"constexpr uint16_t kColorOffline = "
        f"0x{rgb565(COLOR_OFFLINE):04X};",
        "",
    ]

    for name, bitmap in bitmaps.items():
        sections.append(format_byte_array(name, bitmap))
    for name, raster_font in fonts.items():
        sections.append(format_raster_font(name, raster_font))
    sections.append(format_rgb565_array("kBackground", background))
    sections.extend(["}  // namespace CosmicAssets", ""])

    OUTPUT_HEADER.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HEADER.write_text("\n".join(sections))


def paste_alpha(
    canvas: Image.Image,
    bitmap: Image.Image,
    position: tuple[int, int],
    color: tuple[int, int, int],
) -> None:
    """按指定纯色把 alpha 蒙版贴到预览中。"""
    layer = Image.new("RGB", bitmap.size, color)
    canvas.paste(layer, position, bitmap)


def draw_raster_text(
    canvas: Image.Image,
    font: RasterFont,
    text: str,
    position: tuple[int, int],
    color: tuple[int, int, int],
) -> None:
    """按固件相同的字符 advance 规则绘制一段动态预览文字。"""
    cursor_x, top = position
    for character in text:
        glyph = font.glyphs.get(character)
        if glyph is None:
            continue
        paste_alpha(canvas, glyph, (cursor_x, top), color)
        cursor_x += font.advances[character]


def draw_today_amount(
    canvas: Image.Image,
    font: RasterFont,
    sparkle: Image.Image,
    text: str,
    position: tuple[int, int],
) -> None:
    """绘制今日金额，并用粉色四角星替代普通小数点。"""
    cursor_x, top = position
    for character in text:
        glyph = font.glyphs.get(character)
        if glyph is None:
            continue
        if character == ".":
            paste_alpha(
                canvas,
                sparkle,
                (cursor_x, top + 25),
                COLOR_PEACH,
            )
        else:
            paste_alpha(canvas, glyph, (cursor_x, top), COLOR_PRIMARY)
        cursor_x += font.advances[character]


def raster_text_width(font: RasterFont, text: str) -> int:
    """计算动态文本的 advance 总宽度，用于字段边界校验。"""
    return sum(font.advances.get(character, 0) for character in text)


def draw_left_aligned_text(
    canvas: Image.Image,
    font: RasterFont,
    text: str,
    rect: tuple[int, int, int, int],
    color: tuple[int, int, int],
) -> None:
    """在指定字段矩形内按左边界绘制动态文字。"""
    left, top, _, _ = rect
    draw_raster_text(canvas, font, text, (left, top), color)


def write_preview(
    bitmaps: dict[str, Image.Image],
    fonts: dict[str, RasterFont],
    background: Image.Image,
) -> None:
    """按固件坐标输出一张 320×240 原生分辨率预览。"""
    canvas = background.copy()
    draw = ImageDraw.Draw(canvas)

    paste_alpha(
        canvas,
        bitmaps["kBrandDeskBurn"],
        BRAND_POSITION,
        COLOR_LAVENDER,
    )
    dot_x, dot_y = STATUS_DOT_CENTER
    draw.ellipse(
        (dot_x - 3, dot_y - 3, dot_x + 3, dot_y + 3),
        fill=COLOR_MINT,
    )
    paste_alpha(
        canvas,
        bitmaps["kStatusLive"],
        STATUS_POSITION,
        COLOR_MINT,
    )
    paste_alpha(
        canvas,
        bitmaps["kLabelToday"],
        TODAY_LABEL_POSITION,
        COLOR_LAVENDER,
    )
    # “今日”下方的短线和圆点是接受稿的固定识别细节。
    draw.rectangle((12, 55, 28, 56), fill=COLOR_LAVENDER)
    draw.ellipse((32, 54, 35, 57), fill=COLOR_LAVENDER)

    for position, name in zip(
        PERIOD_LABEL_POSITIONS,
        ("kLabelWeek", "kLabelMonth", "kLabelTotal"),
        strict=True,
    ):
        paste_alpha(
            canvas,
            bitmaps[name],
            position,
            COLOR_LAVENDER_LIGHT,
        )

    paste_alpha(
        canvas,
        bitmaps["kTodayCurrency"],
        TODAY_CURRENCY_POSITION,
        COLOR_LAVENDER,
    )
    draw_today_amount(
        canvas,
        fonts["kTodayAmountFont"],
        bitmaps["kTodayDecimalSparkle"],
        "67.05",
        TODAY_AMOUNT_POSITION,
    )
    draw_raster_text(
        canvas,
        fonts["kTodayTokenFont"],
        "74.46 M tokens",
        TODAY_TOKENS_POSITION,
        COLOR_LAVENDER,
    )

    for text, rect in zip(
        ("$420", "$1935", "$3273"),
        PERIOD_AMOUNT_RECTS,
        strict=True,
    ):
        draw_left_aligned_text(
            canvas,
            fonts["kPeriodAmountFont"],
            text,
            rect,
            COLOR_PRIMARY,
        )
    for text, rect in zip(
        ("423.58 M", "1.74 B", "2.10 B"),
        PERIOD_TOKEN_RECTS,
        strict=True,
    ):
        draw_left_aligned_text(
            canvas,
            fonts["kPeriodTokenFont"],
            text,
            rect,
            COLOR_LAVENDER,
        )

    OUTPUT_PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUTPUT_PREVIEW)


def validate_layout(fonts: dict[str, RasterFont]) -> None:
    """检查预览和极端格式化样例不会越过动态字段边界。"""
    checks = (
        ("本周金额", fonts["kPeriodAmountFont"], "$9999", 68),
        ("压缩金额", fonts["kPeriodAmountFont"], "$99K", 68),
        ("周期 Token", fonts["kPeriodTokenFont"], "999.99 M", 68),
        ("万亿 Token", fonts["kPeriodTokenFont"], "4.29 T", 68),
        ("今日金额", fonts["kTodayAmountFont"], "9999", 126),
        ("今日 Token", fonts["kTodayTokenFont"], "999.99 M tokens", 145),
    )
    for label, font, text, limit in checks:
        width = raster_text_width(font, text)
        if width > limit:
            raise RuntimeError(
                f"{label} 越界：{text!r} 需要 {width}px，字段只有 {limit}px"
            )

    for row, (amount_rect, token_rect) in enumerate(
        zip(PERIOD_AMOUNT_RECTS, PERIOD_TOKEN_RECTS, strict=True),
        start=1,
    ):
        amount_bottom = amount_rect[1] + amount_rect[3]
        if amount_rect[3] < fonts["kPeriodAmountFont"].height:
            raise RuntimeError(f"第 {row} 行金额恢复区低于字形高度")
        if token_rect[3] < fonts["kPeriodTokenFont"].height:
            raise RuntimeError(f"第 {row} 行 Token 恢复区低于字形高度")
        # 两个字段会按各自缓存独立刷新，因此恢复矩形不能碰到另一行文字。
        if amount_bottom > token_rect[1]:
            raise RuntimeError(f"第 {row} 行金额与 Token 恢复区重叠")


def main() -> None:
    """生成背景、资源头文件与预览，并输出生成路径。"""
    bitmaps, fonts, background = build_assets()
    validate_layout(fonts)
    write_header(bitmaps, fonts, background)
    write_preview(bitmaps, fonts, background)
    print(f"wrote {BACKGROUND_OUTPUT}")
    print(f"wrote {OUTPUT_HEADER}")
    print(f"wrote {OUTPUT_PREVIEW}")


if __name__ == "__main__":
    main()
