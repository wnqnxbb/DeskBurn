#!/usr/bin/env python3
"""生成 Midnight Buddy 固件资源和 320x240 设计预览。

这套页面采用非对称左右分屏：左侧是今日消耗与陪伴型小精灵，右侧是本周、本月、
总计三段纵向足迹。脚本把固定文案、动态数字字形和 Image Gen 小精灵转换为固件
可直接使用的资源，同时按相同坐标输出原生分辨率预览，避免预览与实机两套排版。

用法：
    .venv/bin/python tools/generate_buddy_assets.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MASCOT_SOURCE = PROJECT_ROOT / "assets" / "dark-cute-mascot.png"
OUTPUT_HEADER = (
    PROJECT_ROOT / "firmware" / "deskburn_buddy" / "buddy_assets.h"
)
OUTPUT_PREVIEW = (
    PROJECT_ROOT / "docs" / "images" / "midnight-buddy-preview.png"
)

SF_ROUNDED_FONT = "/System/Library/Fonts/SFNSRounded.ttf"
CHINESE_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
CHINESE_BOLD_INDEX = 2

SCREEN_WIDTH = 320
SCREEN_HEIGHT = 240

# 颜色全部按 RGB565 量化后的区分度设计。深色面板之间至少跨两个量化档，实机不会
# 因背光或视角把三个周期段糊成同一块；奶油白与薰衣草紫则负责主次层级。
COLOR_BACKGROUND = (5, 7, 24)
COLOR_PANEL_A = (24, 26, 69)
COLOR_PANEL_B = (18, 21, 58)
COLOR_PANEL_LINE = (47, 43, 98)
COLOR_PRIMARY = (255, 238, 204)
COLOR_LAVENDER = (179, 145, 242)
COLOR_LAVENDER_MUTED = (112, 91, 184)
COLOR_PEACH = (255, 143, 124)
COLOR_MINT = (116, 241, 180)
COLOR_OFFLINE = (255, 105, 120)


@dataclass(frozen=True)
class RasterFont:
    """一组共享垂直包围盒、可由固件按字符查找的 alpha 字形。"""

    glyphs: dict[str, Image.Image]
    advances: dict[str, int]
    height: int


def load_rounded_font(pixel_size: int, variation: str) -> ImageFont.FreeTypeFont:
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
    """把一段不会变化的文字渲染成紧边界 alpha 蒙版。"""
    bounds = font.getbbox(text)
    width = max(1, bounds[2] - bounds[0] + 8)
    height = max(1, bounds[3] - bounds[1] + 8)
    canvas = Image.new("L", (width, height), 0)
    ImageDraw.Draw(canvas).text(
        (4 - bounds[0], 4 - bounds[1]), text, font=font, fill=255
    )
    if alpha_gamma != 1.0:
        curve = [round(255 * (value / 255) ** alpha_gamma) for value in range(256)]
        canvas = canvas.point(curve)
    return crop_to_ink(canvas)


def render_raster_font(
    characters: str,
    font: ImageFont.FreeTypeFont,
) -> RasterFont:
    """生成具有统一顶边、底边和基线的动态字形。

    每个字符保留字体原始 advance，数字变化时既不会上下跳，也不会因为紧裁边而
    破坏圆体字距。空格只生成 advance，不占固件 alpha 数组。
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
            (font.size, baseline), character, font=font, fill=255, anchor="ls"
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


def build_mascot() -> Image.Image:
    """把去背的小精灵整理成适合 TFT 的 88x64 RGB 资源。

    源图来自 Image Gen，先按 alpha 包围盒裁切，再用最近邻缩小以保留像素轮廓。
    最后限制为 12 色并与页面背景合成，既降低 RGB565 量化噪点，也让固件可以一次
    pushImage 绘制，无需为每个角色像素保存额外 alpha。
    """
    source = Image.open(MASCOT_SOURCE).convert("RGBA")
    bounds = source.getchannel("A").getbbox()
    if bounds is None:
        raise RuntimeError(f"小精灵资源没有可见像素：{MASCOT_SOURCE}")
    source = source.crop(bounds)
    source.thumbnail((88, 64), Image.Resampling.NEAREST)

    rgba = Image.new("RGBA", (88, 64), (0, 0, 0, 0))
    left = (rgba.width - source.width) // 2
    top = (rgba.height - source.height) // 2
    rgba.alpha_composite(source, (left, top))

    flat = Image.new("RGB", rgba.size, COLOR_BACKGROUND)
    flat.paste(rgba.convert("RGB"), (0, 0), rgba.getchannel("A"))
    return flat.quantize(
        colors=12,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    ).convert("RGB")


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


def rgb565(color: tuple[int, int, int]) -> int:
    """把预览使用的 RGB888 颜色转换成固件 RGB565。"""
    red, green, blue = color
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)


def format_rgb565_array(name: str, image: Image.Image) -> str:
    """把一张 RGB 图输出为可由 TFT_eSPI 直接推送的 RGB565 数组。"""
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


def build_assets(
) -> tuple[dict[str, Image.Image], dict[str, RasterFont], Image.Image]:
    """创建固定文字、动态字体和生产小精灵资源。"""
    chinese_font = ImageFont.truetype(
        CHINESE_FONT, 17, index=CHINESE_BOLD_INDEX
    )

    bitmaps = {
        "kBrandDeskBurn": render_text_bitmap(
            "DeskBurn", load_rounded_font(16, "Bold")
        ),
        "kStatusLive": render_text_bitmap(
            "LIVE", load_rounded_font(14, "Bold")
        ),
        "kStatusOff": render_text_bitmap(
            "OFF", load_rounded_font(14, "Bold")
        ),
        "kSleepZ": render_text_bitmap("Z", load_rounded_font(12, "Bold")),
        "kLabelToday": render_text_bitmap(
            "今日", chinese_font, alpha_gamma=0.75
        ),
        "kLabelWeek": render_text_bitmap(
            "本周", chinese_font, alpha_gamma=0.75
        ),
        "kLabelMonth": render_text_bitmap(
            "本月", chinese_font, alpha_gamma=0.75
        ),
        "kLabelTotal": render_text_bitmap(
            "总计", chinese_font, alpha_gamma=0.75
        ),
    }
    fonts = {
        "kTodayAmountFont": render_raster_font(
            "$0123456789.KM", load_rounded_font(43, "Black")
        ),
        "kPeriodAmountFont": render_raster_font(
            "$0123456789.KM", load_rounded_font(28, "Bold")
        ),
        "kTokenFont": render_raster_font(
            "0123456789. KMBtokens", load_rounded_font(15, "Medium")
        ),
    }
    return bitmaps, fonts, build_mascot()


def write_header(
    bitmaps: dict[str, Image.Image],
    fonts: dict[str, RasterFont],
    mascot: Image.Image,
) -> None:
    """写出 Midnight Buddy 固件专用资源头文件。"""
    sections = [
        "// 此文件由 tools/generate_buddy_assets.py 自动生成，请勿手工修改。",
        "#pragma once",
        "",
        "#include <stdint.h>",
        "",
        "namespace BuddyAssets {",
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
        f"constexpr uint16_t kColorBackground = "
        f"0x{rgb565(COLOR_BACKGROUND):04X};",
        f"constexpr uint16_t kColorPanelA = 0x{rgb565(COLOR_PANEL_A):04X};",
        f"constexpr uint16_t kColorPanelB = 0x{rgb565(COLOR_PANEL_B):04X};",
        f"constexpr uint16_t kColorPanelLine = "
        f"0x{rgb565(COLOR_PANEL_LINE):04X};",
        f"constexpr uint16_t kColorPrimary = 0x{rgb565(COLOR_PRIMARY):04X};",
        f"constexpr uint16_t kColorLavender = "
        f"0x{rgb565(COLOR_LAVENDER):04X};",
        f"constexpr uint16_t kColorLavenderMuted = "
        f"0x{rgb565(COLOR_LAVENDER_MUTED):04X};",
        f"constexpr uint16_t kColorPeach = 0x{rgb565(COLOR_PEACH):04X};",
        f"constexpr uint16_t kColorMint = 0x{rgb565(COLOR_MINT):04X};",
        f"constexpr uint16_t kColorOffline = "
        f"0x{rgb565(COLOR_OFFLINE):04X};",
        "",
    ]

    for name, bitmap in bitmaps.items():
        sections.append(format_byte_array(name, bitmap))
    for name, raster_font in fonts.items():
        sections.append(format_raster_font(name, raster_font))
    sections.append(format_rgb565_array("kMascot", mascot))

    sections.extend(["}  // namespace BuddyAssets", ""])
    OUTPUT_HEADER.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HEADER.write_text("\n".join(sections))


def draw_track_marker(
    draw: ImageDraw.ImageDraw,
    center_y: int,
) -> None:
    """绘制一枚足迹星标；固件端使用同样的 1px/2px 几何形状。"""
    draw.rectangle((190, center_y - 5, 191, center_y + 5), fill=COLOR_PRIMARY)
    draw.rectangle((186, center_y - 1, 195, center_y), fill=COLOR_PRIMARY)


def write_preview(
    bitmaps: dict[str, Image.Image],
    fonts: dict[str, RasterFont],
    mascot: Image.Image,
) -> None:
    """按固件坐标输出一张 320x240 原生分辨率预览。"""
    canvas = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), COLOR_BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    # 三段纵向周期带是本方案与旧版上下布局最明显的结构差异。
    draw.rectangle((194, 0, 319, 79), fill=COLOR_PANEL_A)
    draw.rectangle((194, 80, 319, 159), fill=COLOR_PANEL_B)
    draw.rectangle((194, 160, 319, 239), fill=COLOR_PANEL_A)
    draw.line((194, 79, 319, 79), fill=COLOR_PANEL_LINE)
    draw.line((194, 159, 319, 159), fill=COLOR_PANEL_LINE)

    paste_alpha(canvas, bitmaps["kBrandDeskBurn"], (10, 8), COLOR_LAVENDER)
    draw.ellipse((112, 10, 119, 17), fill=COLOR_MINT)
    paste_alpha(canvas, bitmaps["kStatusLive"], (125, 8), COLOR_MINT)
    paste_alpha(canvas, bitmaps["kLabelToday"], (12, 44), COLOR_LAVENDER)

    canvas.paste(mascot, (69, 54))
    paste_alpha(canvas, bitmaps["kSleepZ"], (151, 69), COLOR_LAVENDER)
    paste_alpha(canvas, bitmaps["kSleepZ"], (159, 58), COLOR_LAVENDER_MUTED)
    # 两枚小星点保持可爱感，但不形成会和数据争抢注意力的星空背景。
    draw.rectangle((60, 78, 64, 82), fill=COLOR_PRIMARY)
    draw.rectangle((61, 75, 63, 85), fill=COLOR_PRIMARY)
    draw.rectangle((162, 103, 166, 107), fill=COLOR_PEACH)
    draw.rectangle((163, 100, 165, 110), fill=COLOR_PEACH)

    draw_raster_text(
        canvas,
        fonts["kTodayAmountFont"],
        "$67.05",
        (12, 137),
        COLOR_PRIMARY,
    )
    draw_raster_text(
        canvas,
        fonts["kTokenFont"],
        "74.46 M tokens",
        (18, 193),
        COLOR_LAVENDER,
    )

    # 中间足迹只做节奏连接，不承担分隔职责；三个 plus 对齐三段数据中心。
    for y in range(13, 240, 12):
        draw.rectangle((190, y, 191, y + 2), fill=COLOR_LAVENDER_MUTED)
    for center_y in (40, 120, 200):
        draw_track_marker(draw, center_y)

    labels = ("kLabelWeek", "kLabelMonth", "kLabelTotal")
    costs = ("$420", "$1935", "$3273")
    tokens = ("423.58 M", "1.74 B", "2.10 B")
    for row, (label, cost, token_text) in enumerate(
        zip(labels, costs, tokens, strict=True)
    ):
        top = row * 80
        paste_alpha(canvas, bitmaps[label], (205, top + 9), COLOR_LAVENDER)
        draw_raster_text(
            canvas,
            fonts["kPeriodAmountFont"],
            cost,
            (204, top + 30),
            COLOR_PRIMARY,
        )
        draw_raster_text(
            canvas,
            fonts["kTokenFont"],
            token_text,
            (205, top + 61),
            COLOR_LAVENDER,
        )

    OUTPUT_PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUTPUT_PREVIEW)


def main() -> None:
    """生成资源头文件与原生尺寸预览，并打印输出路径。"""
    bitmaps, fonts, mascot = build_assets()
    write_header(bitmaps, fonts, mascot)
    write_preview(bitmaps, fonts, mascot)
    print(f"wrote {OUTPUT_HEADER}")
    print(f"wrote {OUTPUT_PREVIEW}")


if __name__ == "__main__":
    main()
