#!/usr/bin/env python3
"""生成 Swiss Poster 固件资源和 320x240 设计预览。

瑞士平面海报版本使用暖白背景、大号黑体金额、等宽 Token 和少量橙红色强调。
ESP32 端不能直接加载系统字体或 SVG，因此本脚本把固定文字、图标和动态字段所需
字形预先转成 8 位 alpha 蒙版，同时用完全相同的字体与坐标输出 GitHub 预览图。

用法：
    .venv/bin/python tools/generate_swiss_assets.py
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_HEADER = PROJECT_ROOT / "firmware" / "deskburn_swiss" / "swiss_assets.h"
OUTPUT_PREVIEW = PROJECT_ROOT / "docs" / "images" / "swiss-poster-preview.png"

SF_FONT = "/System/Library/Fonts/SFNS.ttf"
SF_MONO_FONT = "/System/Library/Fonts/SFNSMono.ttf"
CHINESE_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
CHINESE_BOLD_INDEX = 2

SCREEN_WIDTH = 320
SCREEN_HEIGHT = 240

# 暖白纸张、近黑油墨和橙红专色构成参考图的主要视觉语言。
# 不能直接照搬参考图采样到的 #F7F6F3：它量化成 RGB565 后三个通道几乎同亮，
# 实机看起来会偏浅灰白。这里把绿、蓝各压低一个量化档，得到 0xF79D；在 TFT 上
# 解码约为 #F7F3EF，仍然很浅，但能保留参考图可感知的暖米白纸张色。
COLOR_PAPER = (247, 243, 238)
COLOR_INK = (8, 8, 8)
COLOR_ACCENT = (255, 76, 45)
COLOR_LIVE = (10, 143, 101)
COLOR_OFFLINE = (204, 51, 51)
COLOR_CLAUDE = (217, 119, 87)


@dataclass(frozen=True)
class RasterFont:
    """一组共用基线和高度、可按字符查找的 alpha 字形。"""

    glyphs: dict[str, Image.Image]
    advances: dict[str, int]
    height: int


def load_sf_font(pixel_size: int, variation: str) -> ImageFont.FreeTypeFont:
    """加载指定字重的 SF Pro 可变字体实例。"""
    font = ImageFont.truetype(SF_FONT, pixel_size)
    font.set_variation_by_name(variation)
    return font


def crop_to_ink(image: Image.Image) -> Image.Image:
    """裁掉固定资源四周的透明像素，便于用左上角精确定位。"""
    bounds = image.getbbox()
    return image.crop(bounds) if bounds else image


def render_text_bitmap(
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    alpha_gamma: float = 1.0,
) -> Image.Image:
    """把一段不会变化的文字渲染成裁边 alpha 蒙版。"""
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


def render_svg_alpha(svg_path: Path, size: int) -> Image.Image:
    """把 SVG 图标光栅化成指定大小的 alpha 蒙版。"""
    png = cairosvg.svg2png(
        url=str(svg_path), output_width=size, output_height=size
    )
    return crop_to_ink(Image.open(io.BytesIO(png)).convert("RGBA").getchannel("A"))


def render_raster_font(
    characters: str,
    font: ImageFont.FreeTypeFont,
) -> RasterFont:
    """生成共用垂直包围盒的动态字形。

    每个字形画在自己的 advance 格子内，但保留相同的顶边、底边和基线。固件只要
    按 advance 连续推进即可保持字距和基线一致，数字变化时也不会上下跳动。
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


def format_byte_array(name: str, image: Image.Image) -> str:
    """把单张 alpha 蒙版输出为 C++ 字节数组和尺寸描述。"""
    values = list(image.tobytes())
    lines = []
    for offset in range(0, len(values), 16):
        chunk = ", ".join(f"0x{value:02X}" for value in values[offset:offset + 16])
        lines.append(f"    {chunk},")
    return (
        f"const uint8_t {name}Alpha[] = {{\n" + "\n".join(lines) + "\n};\n"
        f"const AlphaBitmap {name} = "
        f"{{{name}Alpha, {image.width}, {image.height}}};\n"
    )


def character_suffix(character: str) -> str:
    """把字形字符转换成稳定、合法的 C++ 标识符后缀。"""
    names = {"$": "Currency", ".": "Dot", " ": "Space"}
    if character in names:
        return names[character]
    if character.isdigit():
        return f"Digit{character}"
    # 使用 Unicode 码点而不是 upper()，否则动态字体同时包含 "K" 和 tokens
    # 里的小写 "k" 时会生成同名 C++ 数组。
    return f"CharU{ord(character):04X}"


def format_raster_font(name: str, raster_font: RasterFont) -> str:
    """输出一组动态字形及其字符映射表。"""
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
        escaped = "\\'" if character == "'" else character
        entries.append(
            f"    {{'{escaped}', {bitmap_name}, {raster_font.advances[character]}}},"
        )

    sections.append(
        f"const AlphaGlyph {name}Glyphs[] = {{\n"
        + "\n".join(entries)
        + "\n};\n"
        f"const AlphaFont {name} = "
        f"{{{name}Glyphs, sizeof({name}Glyphs) / sizeof({name}Glyphs[0]), "
        f"{raster_font.height}}};\n"
    )
    return "\n".join(sections)


def rgb565(color: tuple[int, int, int]) -> int:
    """把预览用的 RGB888 颜色转换成固件使用的 RGB565。"""
    red, green, blue = color
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)


def paste_alpha(
    canvas: Image.Image,
    bitmap: Image.Image,
    position: tuple[int, int],
    color: tuple[int, int, int],
) -> None:
    """按指定纯色把 alpha 蒙版贴到设计预览中。"""
    layer = Image.new("RGB", bitmap.size, color)
    canvas.paste(layer, position, bitmap)


def raster_text_width(font: RasterFont, text: str) -> int:
    """计算动态字形字符串的总 advance 宽度。"""
    return sum(font.advances.get(character, 0) for character in text)


def draw_raster_text(
    canvas: Image.Image,
    font: RasterFont,
    text: str,
    position: tuple[int, int],
    color: tuple[int, int, int],
) -> None:
    """在预览图中按固件相同的 advance 规则绘制动态文本。"""
    cursor_x, top = position
    for character in text:
        glyph = font.glyphs.get(character)
        if glyph is None:
            continue
        paste_alpha(canvas, glyph, (cursor_x, top), color)
        cursor_x += font.advances[character]


def build_assets() -> tuple[dict[str, Image.Image], dict[str, RasterFont]]:
    """创建固件与预览共同使用的全部固定资源和动态字体。"""
    title_font = ImageFont.truetype(CHINESE_FONT, 18, index=CHINESE_BOLD_INDEX)
    label_font = ImageFont.truetype(CHINESE_FONT, 18, index=CHINESE_BOLD_INDEX)

    bitmaps = {
        "kBrandDeskBurn": render_text_bitmap(
            "DeskBurn", load_sf_font(18, "Bold")
        ),
        "kStatusLive": render_text_bitmap("LIVE", load_sf_font(15, "Bold")),
        "kStatusOff": render_text_bitmap("OFF", load_sf_font(15, "Bold")),
        "kTitleToday": render_text_bitmap(
            "今日消耗", title_font, alpha_gamma=0.72
        ),
        "kLabelWeek": render_text_bitmap("本周", label_font, alpha_gamma=0.72),
        "kLabelMonth": render_text_bitmap("本月", label_font, alpha_gamma=0.72),
        "kLabelTotal": render_text_bitmap("总计", label_font, alpha_gamma=0.72),
        "kLogoOpenAi": render_svg_alpha(PROJECT_ROOT / "openai.svg", 21),
        "kLogoClaude": render_svg_alpha(PROJECT_ROOT / "claude-color.svg", 21),
    }

    fonts = {
        "kAmountFont": render_raster_font(
            "$0123456789.", load_sf_font(58, "Black")
        ),
        "kTableCostFont": render_raster_font(
            "$0123456789", load_sf_font(23, "Bold")
        ),
        "kMonoFont": render_raster_font(
            "0123456789. KMBtokens", ImageFont.truetype(SF_MONO_FONT, 18)
        ),
    }
    return bitmaps, fonts


def write_header(
    bitmaps: dict[str, Image.Image],
    fonts: dict[str, RasterFont],
) -> None:
    """写出 Swiss Poster 固件专用资源头文件。"""
    sections = [
        "// 此文件由 tools/generate_swiss_assets.py 自动生成，请勿手工修改。",
        "#pragma once",
        "",
        "#include <stdint.h>",
        "",
        "namespace SwissAssets {",
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
        f"constexpr uint16_t kColorPaper = 0x{rgb565(COLOR_PAPER):04X};",
        f"constexpr uint16_t kColorInk = 0x{rgb565(COLOR_INK):04X};",
        f"constexpr uint16_t kColorAccent = 0x{rgb565(COLOR_ACCENT):04X};",
        f"constexpr uint16_t kColorLive = 0x{rgb565(COLOR_LIVE):04X};",
        f"constexpr uint16_t kColorOffline = 0x{rgb565(COLOR_OFFLINE):04X};",
        f"constexpr uint16_t kColorClaude = 0x{rgb565(COLOR_CLAUDE):04X};",
        "",
    ]

    for name, bitmap in bitmaps.items():
        sections.append(format_byte_array(name, bitmap))
    for name, raster_font in fonts.items():
        sections.append(format_raster_font(name, raster_font))

    sections.extend(["}  // namespace SwissAssets", ""])
    OUTPUT_HEADER.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HEADER.write_text("\n".join(sections))


def write_preview(
    bitmaps: dict[str, Image.Image],
    fonts: dict[str, RasterFont],
) -> None:
    """按固件坐标输出一张 320x240 像素设计预览。"""
    canvas = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), COLOR_PAPER)
    draw = ImageDraw.Draw(canvas)

    # 顶栏：用户要求 DeskBurn 左侧不放小图标，因此品牌文字从 18px 直接起排。
    paste_alpha(canvas, bitmaps["kBrandDeskBurn"], (18, 9), COLOR_ACCENT)
    paste_alpha(canvas, bitmaps["kLogoOpenAi"], (211, 7), COLOR_INK)
    paste_alpha(canvas, bitmaps["kLogoClaude"], (239, 7), COLOR_CLAUDE)
    draw.ellipse((267, 12, 275, 20), fill=COLOR_LIVE)
    paste_alpha(canvas, bitmaps["kStatusLive"], (280, 10), COLOR_LIVE)

    paste_alpha(canvas, bitmaps["kTitleToday"], (18, 42), COLOR_INK)
    draw.rectangle((285, 64, 290, 118), fill=COLOR_ACCENT)

    amount_font = fonts["kAmountFont"]
    draw_raster_text(canvas, amount_font, "$67.05", (18, 66), COLOR_INK)

    mono_font = fonts["kMonoFont"]
    draw_raster_text(canvas, mono_font, "74.46 M tokens", (18, 127), COLOR_INK)

    # 表格列线和行线先画，红色竖线最后覆盖交点，保持海报的专色层次。
    draw.line((18, 184, 309, 184), fill=COLOR_INK, width=1)
    draw.line((18, 212, 309, 212), fill=COLOR_INK, width=1)
    draw.line((202, 158, 202, 238), fill=COLOR_ACCENT, width=2)

    row_centers = (171, 199, 227)
    labels = ("kLabelWeek", "kLabelMonth", "kLabelTotal")
    costs = ("$420", "$1935", "$3273")
    tokens = ("423.58 M", "1.74 B", "2.10 B")
    cost_font = fonts["kTableCostFont"]
    for center_y, label, cost, token_text in zip(
        row_centers, labels, costs, tokens, strict=True
    ):
        label_bitmap = bitmaps[label]
        paste_alpha(
            canvas,
            label_bitmap,
            (20, center_y - label_bitmap.height // 2),
            COLOR_INK,
        )

        cost_width = raster_text_width(cost_font, cost)
        draw_raster_text(
            canvas,
            cost_font,
            cost,
            (188 - cost_width, center_y - cost_font.height // 2),
            COLOR_INK,
        )
        draw_raster_text(
            canvas,
            mono_font,
            token_text,
            (216, center_y - mono_font.height // 2),
            COLOR_INK,
        )

    OUTPUT_PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUTPUT_PREVIEW)


def main() -> None:
    """生成资源头文件与预览，并打印输出路径。"""
    bitmaps, fonts = build_assets()
    write_header(bitmaps, fonts)
    write_preview(bitmaps, fonts)
    print(f"wrote {OUTPUT_HEADER}")
    print(f"wrote {OUTPUT_PREVIEW}")


if __name__ == "__main__":
    main()
