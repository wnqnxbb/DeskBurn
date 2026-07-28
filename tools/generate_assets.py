#!/usr/bin/env python3
"""把 SVG 图标和中文字形烘焙成固件可直接使用的位图头文件。

MCU 上没法渲染 SVG，TFT_eSPI 的内置字体也只有 ASCII，所以图标和中文都在
Mac 上离线光栅化，再以 C 数组的形式编译进固件。

两类资源都存成 8 位 alpha 蒙版而不是 RGB 位图：本项目用到的图标和文字都是
单色的，蒙版加运行时上色既省一半空间，又能让同一份字形用在不同颜色的标签上。

用法：
    .venv/bin/python tools/generate_assets.py
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_HEADER = PROJECT_ROOT / "firmware" / "deskburn" / "assets.h"

# STHeiti 是 macOS 自带的黑体，笔画均匀，缩到 20 像素左右仍然清晰。
CHINESE_FONT = "/System/Library/Fonts/STHeiti Medium.ttc"

# 今日金额用 SF Pro 的 Bold 实例烘焙。TFT_eSPI 的内置 Font 6 是 1 位点阵，
# 没有粗体字重，重复偏移描一遍只会把边缘糊掉；离线光栅化能拿到真正的粗笔画
# 和抗锯齿边缘。SFNS.ttf 是可变字体，Bold 是其中一个命名实例。
COST_FONT = "/System/Library/Fonts/SFNS.ttf"
COST_FONT_INSTANCE = "Bold"

# 金额字号。48px 是原先 Font 6 的高度，加粗后略大一档以坐稳视觉重心。
COST_PIXEL_SIZE = 50

# 美元符号比数字小一档，作为前缀不与金额本身争夺注意力。
CURRENCY_PIXEL_SIZE = 32

# 图标是装饰性元素，尺寸压在今日金额（48px）之下，避免抢走视觉重心。
LOGO_SIZE = 34

# 需要烘焙的中文标签。字号按视觉层级区分：今天是主标签，本周本月是次级标签。
# 面板是 3.5 寸 320x240，像素密度低，中文字号偏小会明显发糊，所以比同级
# 的 ASCII 字体再大一档。
TEXT_ASSETS = [
    ("kTextToday", "今日消耗", 22),
    ("kTextWeek", "本周", 24),
    ("kTextMonth", "本月", 24),
    ("kTextTotal", "总计", 24),
]

# 抗锯齿会把中文的细笔画摊成一片灰，在低密度面板上看起来就是「糊」。
# 用一条 gamma 曲线把中间调整体抬高，笔画更实，边缘对比更硬。
TEXT_ALPHA_GAMMA = 0.7

LOGO_ASSETS = [
    ("kLogoOpenAi", "openai.svg"),
    ("kLogoClaude", "claude-color.svg"),
]


def rgb565(red: int, green: int, blue: int) -> int:
    """把 8 位 RGB 转成 TFT_eSPI 使用的 RGB565。"""
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)


def dominant_svg_color(svg_path: Path) -> int | None:
    """读出 SVG 里写死的填充色，供固件按品牌色绘制。

    fill="currentColor" 的图标没有自带颜色，返回 None 由固件自行决定。
    """
    match = re.search(r'fill="#([0-9a-fA-F]{6})"', svg_path.read_text())
    if not match:
        return None
    value = match.group(1)
    return rgb565(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def crop_to_ink(image: Image.Image) -> Image.Image:
    """裁掉四周全透明的区域，让位图尺寸等于实际笔画范围。

    这样固件里按中心点定位时不用关心原始画布的留白。
    """
    bbox = image.getbbox()
    return image.crop(bbox) if bbox else image


def render_svg_alpha(svg_path: Path, size: int) -> Image.Image:
    """把 SVG 光栅化，只保留 alpha 通道作为蒙版。"""
    png_bytes = cairosvg.svg2png(
        url=str(svg_path), output_width=size, output_height=size
    )
    rendered = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return crop_to_ink(rendered.split()[3])


def load_cost_font(pixel_size: int) -> ImageFont.FreeTypeFont:
    """按指定字号加载 SF Pro 的 Bold 实例。"""
    font = ImageFont.truetype(COST_FONT, pixel_size)
    font.set_variation_by_name(COST_FONT_INSTANCE)
    return font


def render_cost_glyphs() -> tuple[list[tuple[str, Image.Image]], int, int]:
    """把金额用到的字形逐个烘焙成等高蒙版。

    SF Pro 的数字不是等宽的（"1" 的前进宽度只有 "0" 的四分之三），直接按各自
    宽度排版的话，金额每次刷新都会因为数字换了而整体左右跳动。这里把所有数字
    统一到最宽那个的格子里居中，小数点保留自己的窄格子，于是位数不变时每个
    字形的位置都是固定的。

    所有字形共用一条基线并裁到同一个垂直范围，固件按格子依次平铺即可对齐。

    @return 字形列表、数字格子宽度、字形高度。
    """
    font = load_cost_font(COST_PIXEL_SIZE)
    digits = "0123456789"
    glyphs = digits + "."

    # 统一的数字格子宽度取最宽的前进宽度，保证换数字不改变整体排版。
    digit_cell = max(round(font.getlength(digit)) for digit in digits)

    # 先在共用基线上量出所有字形的垂直范围，避免逐个裁剪后高度参差。
    baseline = COST_PIXEL_SIZE * 2
    probe = Image.new("L", (COST_PIXEL_SIZE * 3, COST_PIXEL_SIZE * 3), 0)
    top, bottom = probe.height, 0
    for glyph in glyphs:
        canvas = probe.copy()
        ImageDraw.Draw(canvas).text((COST_PIXEL_SIZE, baseline), glyph,
                                    font=font, fill=255, anchor="ls")
        box = canvas.getbbox()
        top, bottom = min(top, box[1]), max(bottom, box[3])

    rendered: list[tuple[str, Image.Image]] = []
    for glyph in glyphs:
        cell = digit_cell if glyph in digits else round(font.getlength(glyph))
        canvas = Image.new("L", (cell, bottom - top), 0)
        # 字形在格子里水平居中；基线按上面量出的统一顶边换算成局部坐标。
        ImageDraw.Draw(canvas).text((cell / 2, baseline - top), glyph,
                                    font=font, fill=255, anchor="ms")
        rendered.append((glyph, canvas))

    return rendered, digit_cell, bottom - top


def render_currency_alpha() -> Image.Image:
    """烘焙美元符号。它独立居中对齐，所以按实际笔画裁剪即可。"""
    font = load_cost_font(CURRENCY_PIXEL_SIZE)
    canvas = Image.new("L", (CURRENCY_PIXEL_SIZE * 3, CURRENCY_PIXEL_SIZE * 3), 0)
    ImageDraw.Draw(canvas).text((CURRENCY_PIXEL_SIZE, CURRENCY_PIXEL_SIZE * 2),
                                "$", font=font, fill=255, anchor="ls")
    return crop_to_ink(canvas)


def render_text_alpha(text: str, pixel_size: int) -> Image.Image:
    """用系统黑体渲染一段中文，返回抗锯齿并做过笔画增强的 alpha 蒙版。"""
    font = ImageFont.truetype(CHINESE_FONT, pixel_size)

    # 先给足画布再按实际笔画裁剪，避免不同字的伸展部分被截断。
    canvas = Image.new("L", (pixel_size * len(text) * 2, pixel_size * 2), 0)
    ImageDraw.Draw(canvas).text((pixel_size // 2, pixel_size // 2), text,
                                font=font, fill=255)

    curve = [round(255 * (value / 255) ** TEXT_ALPHA_GAMMA) for value in range(256)]
    return crop_to_ink(canvas.point(curve))


def format_array(name: str, image: Image.Image) -> str:
    """把蒙版导出为 C 数组，每行 16 字节便于阅读和 diff。"""
    values = list(image.tobytes())
    lines = []
    for offset in range(0, len(values), 16):
        chunk = ", ".join(f"0x{value:02X}" for value in values[offset:offset + 16])
        lines.append(f"    {chunk},")
    body = "\n".join(lines)
    return (
        f"const uint8_t {name}Alpha[] = {{\n{body}\n}};\n"
        f"const AlphaBitmap {name} = {{{name}Alpha, {image.width}, {image.height}}};\n"
    )


def main() -> None:
    sections: list[str] = []
    color_defines: list[str] = []

    for name, filename in LOGO_ASSETS:
        svg_path = PROJECT_ROOT / filename
        mask = render_svg_alpha(svg_path, LOGO_SIZE)
        sections.append(f"// {filename}，光栅化到 {mask.width}x{mask.height}")
        sections.append(format_array(name, mask))

        color = dominant_svg_color(svg_path)
        if color is not None:
            color_defines.append(
                f"// {filename} 自带的品牌色\n"
                f"constexpr uint16_t {name}Color = 0x{color:04X};"
            )

    for name, text, pixel_size in TEXT_ASSETS:
        mask = render_text_alpha(text, pixel_size)
        sections.append(f'// "{text}"，STHeiti Medium {pixel_size}px')
        sections.append(format_array(name, mask))

    currency = render_currency_alpha()
    sections.append(f'// "$"，SF Pro Bold {CURRENCY_PIXEL_SIZE}px')
    sections.append(format_array("kCostCurrency", currency))

    glyphs, digit_cell, glyph_height = render_cost_glyphs()
    sections.append(
        f"// 金额字形，SF Pro Bold {COST_PIXEL_SIZE}px。"
        f"数字统一在 {digit_cell}px 宽的格子里居中，"
        f"因此刷新时位置固定、不会左右跳动。"
    )
    for glyph, mask in glyphs:
        label = "Dot" if glyph == "." else glyph
        sections.append(format_array(f"kCostGlyph{label}", mask))

    digit_list = ", ".join(f"&kCostGlyph{digit}" for digit in "0123456789")
    sections.append(
        "// 按数字索引取字形，固件里直接用 amount[i] - '0' 查表。\n"
        f"const AlphaBitmap* const kCostDigits[10] = {{{digit_list}}};\n"
        f"constexpr uint16_t kCostDigitCellWidth = {digit_cell};\n"
        f"constexpr uint16_t kCostGlyphHeight = {glyph_height};\n"
    )

    header = f"""/**
 * @file assets.h
 * @brief 由 tools/generate_assets.py 自动生成，请勿手工编辑。
 *
 * 重新生成：
 *     .venv/bin/python tools/generate_assets.py
 *
 * 每份资源都是 8 位 alpha 蒙版，绘制时与背景色混合并按指定颜色上色。
 */

#pragma once

#include <Arduino.h>

/// 一张 8 位 alpha 蒙版位图，按行优先存储。
struct AlphaBitmap {{
  const uint8_t* alpha;
  uint16_t width;
  uint16_t height;
}};

namespace Assets {{

{chr(10).join(color_defines)}

{chr(10).join(sections)}
}}  // namespace Assets
"""

    OUTPUT_HEADER.write_text(header)

    total = sum(len(section) for section in sections)
    print(f"已写入 {OUTPUT_HEADER.relative_to(PROJECT_ROOT)}")
    print(f"资源 {len(LOGO_ASSETS) + len(TEXT_ASSETS)} 份，头文件约 {total // 1024} KB")


if __name__ == "__main__":
    main()
