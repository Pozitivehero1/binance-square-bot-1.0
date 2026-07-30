"""Generate varied, readable square setup cards for Binance Square."""
from __future__ import annotations

import logging
import math
import os
import tempfile
from typing import Callable, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

WATERMARK = os.getenv("CARD_WATERMARK", "PozitiveHero")


def _get_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _fmt_price(value: float) -> str:
    price = float(value)
    absolute = abs(price)
    if absolute >= 1000:
        return f"{price:.2f}"
    if absolute >= 1:
        return f"{price:.4f}".rstrip("0").rstrip(".")
    if absolute >= 0.01:
        return f"{price:.6f}".rstrip("0").rstrip(".")
    return f"{price:.10f}".rstrip("0").rstrip(".")


def _draw_pattern(draw: ImageDraw.ImageDraw, width: int, height: int, mode: int) -> None:
    if mode == 1:
        for y in range(90, height, 90):
            draw.line((0, y, width, y), fill=(255, 255, 255, 10), width=1)
        for x in range(90, width, 90):
            draw.line((x, 0, x, height), fill=(255, 255, 255, 8), width=1)
        return
    if mode == 2:
        for radius in range(120, 880, 110):
            draw.arc((width // 2 - radius, height // 2 - radius, width // 2 + radius, height // 2 + radius), 195, 350, fill=(255, 255, 255, 12), width=2)
        return
    if mode == 3:
        for x in range(-height, width * 2, 72):
            draw.line((x, 0, x + height, height), fill=(255, 255, 255, 11), width=1)
        return
    for x in range(-height, width * 2, 54):
        draw.line((x, 0, x + height, height), fill=(255, 255, 255, 10), width=1)
    for x in range(40, width, 90):
        for y in range(40, height, 90):
            radius = 7 + int(3 * math.sin((x + y) / 130))
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(255, 255, 255, 18), width=1)


def _centered_text(draw, y: int, text: str, font, fill, width: int) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    text_width = box[2] - box[0]
    draw.text(((width - text_width) / 2, y), text, font=font, fill=fill)
    return box[3] - box[1]


def _fit_text(draw, text: str, max_width: int, start_size: int, minimum: int = 18, bold: bool = True):
    size = start_size
    while size > minimum:
        font = _get_font(size, bold=bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
        size -= 2
    return _get_font(minimum, bold=bold)


def _base_canvas(is_long: bool, pattern_mode: int):
    width, height = 1080, 1080
    image = Image.new("RGB", (width, height), (10, 15, 27))
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(height):
        ratio = y / max(height - 1, 1)
        draw.line(
            (0, y, width, y),
            fill=(int(10 + 12 * ratio), int(15 + 18 * ratio), int(27 + 26 * ratio), 255),
        )
    _draw_pattern(draw, width, height, pattern_mode)
    accent = (46, 204, 113, 255) if is_long else (255, 71, 87, 255)
    return image, draw, accent


def _draw_footer(draw, width: int, height: int, basic: str, is_long: bool, muted, footer_font, small_font) -> None:
    hashtag = f"#{basic.upper()}  #{'LONG' if is_long else 'SHORT'}  #TechnicalAnalysis"
    _centered_text(draw, 936, hashtag, small_font, muted, width)
    _centered_text(draw, 978, "Не финансовая рекомендация", footer_font, muted, width)
    watermark_box = draw.textbbox((0, 0), WATERMARK, font=footer_font)
    watermark_width = watermark_box[2] - watermark_box[0]
    draw.text((width - watermark_width - 65, height - 56), WATERMARK, font=footer_font, fill=(120, 130, 148, 150))


def generate_card(
    basic: str,
    direction: str,
    entry: float,
    tp1: float,
    tp2: float,
    tp3: float,
    stop: float,
    rr: float,
    confidence: float,
    change_1h: float,
    *,
    post_style: str = "market_note",
    signal_label: str = "Технический сетап",
) -> str:
    """Create one of four card layouts selected from the post style."""
    is_long = direction == "long"
    style_group = {
        "numbers_first": 1,
        "level_focus": 1,
        "scenario_tree": 2,
        "thesis": 2,
        "risk_first": 3,
        "compact_brief": 3,
    }.get(post_style, 0)

    width, height = 1080, 1080
    image, draw, accent = _base_canvas(is_long, style_group)
    green = (46, 204, 113, 255)
    red = (255, 71, 87, 255)
    white = (246, 248, 252, 255)
    muted = (155, 166, 184, 255)
    yellow = (255, 214, 92, 255)
    blue = (100, 200, 255, 255)
    panel = (17, 25, 42, 230)

    title_font = _get_font(70, bold=True)
    direction_font = _get_font(35, bold=True)
    section_font = _get_font(23, bold=True)
    value_font = _get_font(33, bold=True)
    small_font = _get_font(20, bold=False)
    footer_font = _get_font(17, bold=False)

    draw.rectangle((0, 0, width, 16), fill=accent)
    draw.rounded_rectangle((48, 42, width - 48, height - 45), radius=36, fill=(8, 13, 24, 145), outline=(255, 255, 255, 24), width=2)

    def metric(x: int, y: int, box_width: int, box_height: int, label: str, value: str, value_color=white, centered: bool = False) -> None:
        draw.rounded_rectangle((x, y, x + box_width, y + box_height), radius=20, fill=panel, outline=(255, 255, 255, 20), width=1)
        if centered:
            _centered_text(draw, y + 15, label, section_font, muted, box_width)
            box = draw.textbbox((0, 0), value, font=value_font)
            draw.text((x + (box_width - (box[2] - box[0])) / 2, y + 54), value, font=value_font, fill=value_color)
        else:
            draw.text((x + 20, y + 15), label, font=section_font, fill=muted)
            draw.text((x + 20, y + 53), value, font=value_font, fill=value_color)

    _centered_text(draw, 68, f"${basic.upper()}", title_font, white, width)
    _centered_text(draw, 154, "LONG" if is_long else "SHORT", direction_font, accent, width)
    signal_font = _fit_text(draw, signal_label.upper(), 850, 29, 20, bold=True)
    _centered_text(draw, 202, signal_label.upper(), signal_font, muted, width)

    margin = 76
    gap = 18
    content_width = width - 2 * margin

    if style_group == 1:
        # Level ladder: strong numerical thumbnail for numbers-first posts.
        metric(margin, 270, content_width, 116, "ТОЧКА ВХОДА", _fmt_price(entry), white, centered=True)
        ladder_y = 410
        row_h = 92
        rows = (("TP3", tp3, green), ("TP2", tp2, green), ("TP1", tp1, green), ("СТОП", stop, red))
        for index, (label, value, color) in enumerate(rows):
            y = ladder_y + index * (row_h + 12)
            draw.rounded_rectangle((margin, y, width - margin, y + row_h), radius=18, fill=panel, outline=(255, 255, 255, 18), width=1)
            draw.text((margin + 24, y + 27), label, font=section_font, fill=color)
            value_text = _fmt_price(value)
            box = draw.textbbox((0, 0), value_text, font=value_font)
            draw.text((width - margin - 24 - (box[2] - box[0]), y + 22), value_text, font=value_font, fill=white)
        third = (content_width - 2 * gap) // 3
        metric(margin, 834, third, 92, "R/R", f"{rr:.2f}", yellow)
        metric(margin + third + gap, 834, third, 92, "СКОР", f"{confidence:.0f}", blue)
        metric(margin + 2 * (third + gap), 834, third, 92, "1H", f"{change_1h:+.2f}%", green if change_1h >= 0 else red)

    elif style_group == 2:
        # Split thesis card: separates opportunity and invalidation.
        half = (content_width - gap) // 2
        metric(margin, 275, half, 128, "ВХОД", _fmt_price(entry), white)
        metric(margin + half + gap, 275, half, 128, "СТОП", _fmt_price(stop), red)
        draw.rounded_rectangle((margin, 430, width - margin, 625), radius=24, fill=panel, outline=(255, 255, 255, 20), width=1)
        draw.text((margin + 24, 454), "СЦЕНАРИЙ ПРОДОЛЖЕНИЯ", font=section_font, fill=accent)
        draw.text((margin + 24, 506), f"TP1  {_fmt_price(tp1)}\nTP2  {_fmt_price(tp2)}\nTP3  {_fmt_price(tp3)}", font=value_font, fill=white, spacing=13)
        draw.rounded_rectangle((margin, 650, width - margin, 810), radius=24, fill=(28, 22, 31, 220), outline=(255, 255, 255, 20), width=1)
        draw.text((margin + 24, 674), "ТОЧКА ОТМЕНЫ", font=section_font, fill=red)
        draw.text((margin + 24, 724), f"Закрепление за стопом {_fmt_price(stop)}", font=_fit_text(draw, f"Закрепление за стопом {_fmt_price(stop)}", content_width - 48, 30, 20), fill=white)
        half_stat = (content_width - gap) // 2
        metric(margin, 834, half_stat, 92, "R/R", f"{rr:.2f}", yellow)
        metric(margin + half_stat + gap, 834, half_stat, 92, "ИЗМЕНЕНИЕ 1H", f"{change_1h:+.2f}%", green if change_1h >= 0 else red)

    elif style_group == 3:
        # Risk-first card: visually emphasizes invalidation before targets.
        draw.rounded_rectangle((margin, 272, width - margin, 420), radius=25, fill=(38, 20, 28, 235), outline=red, width=2)
        draw.text((margin + 28, 296), "СНАЧАЛА РИСК", font=direction_font, fill=red)
        draw.text((margin + 28, 350), f"Стоп: {_fmt_price(stop)}", font=value_font, fill=white)
        metric(margin, 448, content_width, 112, "ВХОД ПО СЦЕНАРИЮ", _fmt_price(entry), white, centered=True)
        third = (content_width - 2 * gap) // 3
        metric(margin, 586, third, 112, "TP1", _fmt_price(tp1), green)
        metric(margin + third + gap, 586, third, 112, "TP2", _fmt_price(tp2), green)
        metric(margin + 2 * (third + gap), 586, third, 112, "TP3", _fmt_price(tp3), green)
        half = (content_width - gap) // 2
        metric(margin, 728, half, 112, "R/R", f"{rr:.2f}", yellow)
        metric(margin + half + gap, 728, half, 112, "SETUP SCORE", f"{confidence:.0f}/100", blue)
        change_color = green if change_1h >= 0 else red
        draw.rounded_rectangle((margin, 862, width - margin, 925), radius=18, fill=panel, outline=(255, 255, 255, 20), width=1)
        _centered_text(draw, 878, f"Изменение за 1H: {change_1h:+.2f}%", section_font, change_color, width)

    else:
        # Balanced dashboard: default market-note/checklist layout.
        half = (content_width - gap) // 2
        third = (content_width - 2 * gap) // 3
        metric(margin, 270, half, 116, "ВХОД", _fmt_price(entry), white)
        metric(margin + half + gap, 270, half, 116, "СТОП", _fmt_price(stop), red)
        metric(margin, 410, third, 112, "TP1", _fmt_price(tp1), green)
        metric(margin + third + gap, 410, third, 112, "TP2", _fmt_price(tp2), green)
        metric(margin + 2 * (third + gap), 410, third, 112, "TP3", _fmt_price(tp3), green)
        metric(margin, 548, third, 112, "R/R", f"{rr:.2f}", yellow)
        metric(margin + third + gap, 548, third, 112, "SETUP SCORE", f"{confidence:.0f}/100", blue)
        metric(margin + 2 * (third + gap), 548, third, 112, "ИЗМ. 1H", f"{change_1h:+.2f}%", green if change_1h >= 0 else red)
        draw.rounded_rectangle((margin, 688, width - margin, 858), radius=22, fill=panel, outline=(255, 255, 255, 20), width=1)
        draw.text((margin + 24, 714), "ЛОГИКА СЕТАПА", font=section_font, fill=accent)
        body_font = _fit_text(draw, signal_label, content_width - 48, 31, 22, bold=True)
        draw.text((margin + 24, 766), signal_label, font=body_font, fill=white)
        draw.text((margin + 24, 816), "Уровни рассчитаны по ATR и структуре рынка", font=small_font, fill=muted)

    _draw_footer(draw, width, height, basic, is_long, muted, footer_font, small_font)

    temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    path = temp.name
    temp.close()
    image.save(path, "PNG", optimize=True)
    logger.info("Card generated: %s | style=%s | signal=%s", path, post_style, signal_label)
    return path
