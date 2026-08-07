"""Generate mobile-first editorial cards for Binance Square.

Unlike the old one-template signal card, every content family has a visibly
separate composition. The card is selected from the same editorial format as
the text, so the public feed does not become a wall of identical charts.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Iterable, List, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)
WATERMARK = os.getenv("CARD_WATERMARK", os.getenv("CHART_WATERMARK", "PozitiveHero"))


def _get_font(size: int, bold: bool = False):
    names = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    )
    for path in names:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
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


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 4) -> List[str]:
    words = str(text).split()
    lines: List[str] = []
    current: List[str] = []
    for word in words:
        trial = " ".join(current + [word])
        box = draw.textbbox((0, 0), trial, font=font)
        if current and box[2] - box[0] > max_width:
            lines.append(" ".join(current))
            current = [word]
            if len(lines) >= max_lines:
                break
        else:
            current.append(word)
    if current and len(lines) < max_lines:
        lines.append(" ".join(current))
    consumed = sum(len(line.split()) for line in lines)
    if consumed < len(words) and lines:
        lines[-1] = lines[-1].rstrip(".,:;—-") + "…"
    return lines


def _fit_wrapped(draw, text: str, max_width: int, max_lines: int, start: int, minimum: int, bold: bool = True):
    for size in range(start, minimum - 1, -2):
        font = _get_font(size, bold=bold)
        lines = _wrap(draw, text, font, max_width, max_lines)
        if len(lines) <= max_lines:
            return font, lines
    font = _get_font(minimum, bold=bold)
    return font, _wrap(draw, text, font, max_width, max_lines)


def _gradient_canvas(is_long: bool, visual_style: str):
    width = height = 1080
    if visual_style in {"risk_card", "split_scenario"}:
        top = (24, 12, 22)
        bottom = (11, 17, 29)
    elif visual_style in {"indicator_card", "data_card", "pulse_card"}:
        top = (11, 24, 35)
        bottom = (12, 14, 27)
    elif visual_style in {"journal_card", "followup_card"}:
        top = (25, 20, 35)
        bottom = (11, 17, 29)
    else:
        top = (11, 18, 31)
        bottom = (14, 26, 43)
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(height):
        ratio = y / (height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3)) + (255,)
        draw.line((0, y, width, y), fill=color)
    accent = (42, 205, 126, 255) if is_long else (255, 82, 104, 255)
    return image, draw, accent


def _pill(draw, box: Tuple[int, int, int, int], text: str, font, fill, text_fill=(245, 247, 251, 255), outline=None):
    draw.rounded_rectangle(box, radius=22, fill=fill, outline=outline, width=2 if outline else 1)
    bbox = draw.textbbox((0, 0), text, font=font)
    x = box[0] + (box[2] - box[0] - (bbox[2] - bbox[0])) / 2
    y = box[1] + (box[3] - box[1] - (bbox[3] - bbox[1])) / 2 - 2
    draw.text((x, y), text, font=font, fill=text_fill)


def _metric(draw, x: int, y: int, w: int, h: int, label: str, value: str, value_color, panel):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=22, fill=panel, outline=(255, 255, 255, 24), width=1)
    draw.text((x + 22, y + 16), label, font=_get_font(20, bold=True), fill=(151, 164, 184, 255))
    value_font, lines = _fit_wrapped(draw, value, w - 44, 2, 34, 20, True)
    draw.multiline_text((x + 22, y + 52), "\n".join(lines), font=value_font, fill=value_color, spacing=5)


def _headline(draw, headline: str, y: int, width: int, white, max_lines: int = 3) -> int:
    font, lines = _fit_wrapped(draw, headline, width - 120, max_lines, 52, 30, True)
    text = "\n".join(lines)
    draw.multiline_text((60, y), text, font=font, fill=white, spacing=9)
    bbox = draw.multiline_textbbox((60, y), text, font=font, spacing=9)
    return bbox[3] + 18


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
    post_style: str = "",
    signal_label: str = "Технический сетап",
    content_format: str = "setup_plan",
    visual_style: str = "headline_card",
    headline: str = "",
    rsi: float = 0.0,
    adx: float = 0.0,
    volume_relative: float = 0.0,
    change_15m: float = 0.0,
    fresh_volume: float = 1.0,
    attention_score: float = 0.0,
) -> str:
    del post_style
    is_long = direction == "long"
    side = "LONG" if is_long else "SHORT"
    image, draw, accent = _gradient_canvas(is_long, visual_style)
    width = height = 1080
    white = (245, 247, 251, 255)
    muted = (151, 164, 184, 255)
    green = (45, 211, 137, 255)
    red = (255, 82, 104, 255)
    yellow = (255, 210, 91, 255)
    blue = (99, 190, 255, 255)
    panel = (17, 25, 42, 225)

    # Background detail changes by family.
    if visual_style in {"indicator_card", "data_card"}:
        for x in range(55, width, 95):
            draw.line((x, 0, x, height), fill=(255, 255, 255, 9), width=1)
        for y in range(55, height, 95):
            draw.line((0, y, width, y), fill=(255, 255, 255, 9), width=1)
    elif visual_style == "journal_card":
        for y in range(210, 925, 62):
            draw.line((70, y, width - 70, y), fill=(255, 255, 255, 13), width=1)
        draw.line((125, 180, 125, 925), fill=(255, 82, 104, 45), width=2)
    else:
        draw.ellipse((650, -170, 1180, 360), outline=accent[:3] + (35,), width=4)
        draw.ellipse((725, -95, 1100, 280), outline=accent[:3] + (22,), width=2)

    draw.rectangle((0, 0, width, 14), fill=accent)
    _pill(draw, (58, 42, 245, 100), f"${basic.upper()}", _get_font(26, True), (255, 255, 255, 16), outline=accent)
    _pill(draw, (width - 250, 42, width - 58, 100), side, _get_font(25, True), accent, text_fill=(9, 15, 25, 255))

    title = headline or f"${basic.upper()}: {signal_label}"
    current_y = _headline(draw, title, 132, width, white, max_lines=3)
    current_y = max(current_y, 310)

    risk_pct = abs(entry - stop) / max(abs(entry), 1e-12) * 100
    reward_pct = abs(tp3 - entry) / max(abs(entry), 1e-12) * 100

    if visual_style == "pulse_card":
        y = current_y + 5
        move_color = green if change_15m >= 0 else red
        draw.rounded_rectangle((58, y, width - 58, y + 245), radius=34, fill=panel, outline=move_color, width=3)
        draw.text((88, y + 24), "ДВИЖЕНИЕ ПРЯМО СЕЙЧАС", font=_get_font(24, True), fill=muted)
        draw.text((88, y + 74), f"{change_15m:+.2f}%", font=_get_font(88, True), fill=move_color)
        draw.text((540, y + 96), "за 15 минут", font=_get_font(31, True), fill=white)

        y += 285
        _metric(draw, 58, y, 300, 145, "СВЕЖИЙ ОБЪЁМ", f"x{fresh_volume:.2f}", accent, panel)
        _metric(draw, 390, y, 300, 145, "КЛЮЧЕВОЙ УРОВЕНЬ", _fmt_price(entry), white, panel)
        _metric(draw, 722, y, 300, 145, "ВНИМАНИЕ", f"{attention_score:.0f}/100", yellow, panel)
        draw.rounded_rectangle((58, y + 180, width - 58, y + 330), radius=25, fill=panel, outline=(255, 255, 255, 24), width=1)
        draw.text((88, y + 202), "НЕ ПРОГНОЗ — УСЛОВИЕ", font=_get_font(21, True), fill=muted)
        draw.text((88, y + 247), f"Стоп-сценарий {_fmt_price(stop)}  ·  TP1 {_fmt_price(tp1)}", font=_get_font(32, True), fill=white)

    elif visual_style == "split_scenario":
        half = 456
        draw.rounded_rectangle((58, current_y, 58 + half, 760), radius=28, fill=(20, 42, 36, 225), outline=green, width=2)
        draw.rounded_rectangle((566, current_y, 566 + half, 760), radius=28, fill=(48, 24, 32, 225), outline=red, width=2)
        draw.text((88, current_y + 26), "СЦЕНАРИЙ A", font=_get_font(26, True), fill=green)
        draw.text((596, current_y + 26), "СЦЕНАРИЙ B", font=_get_font(26, True), fill=red)
        draw.multiline_text((88, current_y + 82), f"Подтверждение\n{side}\n\nВход\n{_fmt_price(entry)}\n\nTP3\n{_fmt_price(tp3)}", font=_get_font(29, True), fill=white, spacing=9)
        draw.multiline_text((596, current_y + 82), f"Отмена идеи\n\nСтоп\n{_fmt_price(stop)}\n\nБез\nусреднения", font=_get_font(29, True), fill=white, spacing=9)
        _metric(draw, 58, 795, 300, 120, "R/R", f"{rr:.2f}", yellow, panel)
        _metric(draw, 390, 795, 300, 120, "РИСК", f"{risk_pct:.2f}%", red, panel)
        _metric(draw, 722, 795, 300, 120, "ПОТЕНЦИАЛ", f"{reward_pct:.2f}%", green, panel)

    elif visual_style == "risk_card":
        draw.rounded_rectangle((58, current_y, width - 58, current_y + 225), radius=30, fill=(52, 24, 33, 235), outline=red, width=2)
        draw.text((88, current_y + 28), "СНАЧАЛА РИСК", font=_get_font(29, True), fill=red)
        draw.text((88, current_y + 83), f"{risk_pct:.2f}%", font=_get_font(78, True), fill=white)
        draw.text((430, current_y + 104), f"до стопа {_fmt_price(stop)}", font=_get_font(28, True), fill=muted)
        y = current_y + 255
        _metric(draw, 58, y, 300, 130, "ВХОД", _fmt_price(entry), white, panel)
        _metric(draw, 390, y, 300, 130, "R/R", f"{rr:.2f}", yellow, panel)
        _metric(draw, 722, y, 300, 130, "TP3", _fmt_price(tp3), green, panel)
        draw.rounded_rectangle((58, y + 160, width - 58, y + 285), radius=24, fill=panel, outline=(255, 255, 255, 20), width=1)
        draw.text((88, y + 184), "Правило", font=_get_font(21, True), fill=muted)
        draw.text((88, y + 224), "Стоп не переносится дальше после входа", font=_get_font(29, True), fill=white)

    elif visual_style == "indicator_card":
        third = 300
        gap = 32
        y = current_y
        _metric(draw, 58, y, third, 160, "RSI", f"{rsi:.1f}", blue, panel)
        _metric(draw, 58 + third + gap, y, third, 160, "ADX", f"{adx:.1f}", yellow, panel)
        _metric(draw, 58 + 2 * (third + gap), y, third, 160, "REL VOL", f"x{volume_relative:.2f}", accent, panel)
        draw.rounded_rectangle((58, y + 195, width - 58, y + 435), radius=28, fill=panel, outline=(255, 255, 255, 24), width=1)
        draw.text((88, y + 220), "ИНДИКАТОРЫ ≠ ТОЧКА ВХОДА", font=_get_font(30, True), fill=accent)
        draw.multiline_text((88, y + 280), f"Вход {_fmt_price(entry)}\nTP1 {_fmt_price(tp1)}  ·  TP2 {_fmt_price(tp2)}  ·  TP3 {_fmt_price(tp3)}\nСтоп {_fmt_price(stop)}", font=_get_font(30, True), fill=white, spacing=13)
        _metric(draw, 58, y + 470, 465, 120, "SETUP SCORE", f"{confidence:.0f}/100", blue, panel)
        _metric(draw, 555, y + 470, 467, 120, "R/R", f"{rr:.2f}", yellow, panel)

    elif visual_style == "journal_card":
        y = current_y + 10
        draw.text((92, y), "МОЯ ЗАМЕТКА", font=_get_font(25, True), fill=accent)
        note = "Жду подтверждение. Не догоняю свечу. Риск фиксирую до входа."
        note_font, note_lines = _fit_wrapped(draw, note, 840, 4, 39, 27, False)
        draw.multiline_text((92, y + 55), "\n".join(note_lines), font=note_font, fill=white, spacing=13)
        y += 270
        _metric(draw, 58, y, 300, 130, "ВХОД", _fmt_price(entry), white, panel)
        _metric(draw, 390, y, 300, 130, "СТОП", _fmt_price(stop), red, panel)
        _metric(draw, 722, y, 300, 130, "R/R", f"{rr:.2f}", yellow, panel)
        draw.text((82, y + 170), f"1H {change_1h:+.2f}%   ·   {signal_label}", font=_get_font(26, True), fill=muted)

    elif visual_style == "followup_card":
        _pill(draw, (58, current_y, 330, current_y + 64), "ОБНОВЛЕНИЕ ИДЕИ", _get_font(22, True), accent, text_fill=(8, 14, 23, 255))
        y = current_y + 100
        draw.rounded_rectangle((58, y, width - 58, y + 235), radius=28, fill=panel, outline=(255, 255, 255, 24), width=1)
        draw.text((88, y + 25), "НОВЫЙ ПЛАН", font=_get_font(25, True), fill=muted)
        draw.multiline_text((88, y + 75), f"Вход {_fmt_price(entry)}\nЦели {_fmt_price(tp1)} / {_fmt_price(tp2)} / {_fmt_price(tp3)}\nСтоп {_fmt_price(stop)}", font=_get_font(31, True), fill=white, spacing=13)
        _metric(draw, 58, y + 270, 300, 125, "R/R", f"{rr:.2f}", yellow, panel)
        _metric(draw, 390, y + 270, 300, 125, "СКОР", f"{confidence:.0f}", blue, panel)
        _metric(draw, 722, y + 270, 300, 125, "1H", f"{change_1h:+.2f}%", green if change_1h >= 0 else red, panel)

    elif visual_style == "data_card":
        y = current_y
        third = 300
        gap = 32
        entries = (
            ("ВХОД", _fmt_price(entry), white),
            ("СТОП", _fmt_price(stop), red),
            ("R/R", f"{rr:.2f}", yellow),
            ("RSI", f"{rsi:.1f}", blue),
            ("ADX", f"{adx:.1f}", yellow),
            ("ОБЪЁМ", f"x{volume_relative:.2f}", accent),
        )
        for i, (label, value, color) in enumerate(entries):
            row, col = divmod(i, 3)
            _metric(draw, 58 + col * (third + gap), y + row * 170, third, 145, label, value, color, panel)
        draw.rounded_rectangle((58, y + 365, width - 58, y + 520), radius=24, fill=panel, outline=(255, 255, 255, 22), width=1)
        draw.text((88, y + 390), "ЦЕЛИ", font=_get_font(22, True), fill=muted)
        draw.text((88, y + 438), f"{_fmt_price(tp1)}  /  {_fmt_price(tp2)}  /  {_fmt_price(tp3)}", font=_get_font(34, True), fill=green)

    else:  # headline_card and default
        y = current_y
        draw.rounded_rectangle((58, y, width - 58, y + 205), radius=30, fill=panel, outline=accent, width=2)
        draw.text((88, y + 25), "ГЛАВНАЯ ГРАНИЦА", font=_get_font(23, True), fill=muted)
        draw.text((88, y + 72), _fmt_price(entry), font=_get_font(66, True), fill=white)
        draw.text((88, y + 150), f"{signal_label}", font=_get_font(24, True), fill=accent)
        y += 238
        _metric(draw, 58, y, 300, 135, "СТОП", _fmt_price(stop), red, panel)
        _metric(draw, 390, y, 300, 135, "R/R", f"{rr:.2f}", yellow, panel)
        _metric(draw, 722, y, 300, 135, "TP3", _fmt_price(tp3), green, panel)
        draw.rounded_rectangle((58, y + 170, width - 58, y + 315), radius=24, fill=panel, outline=(255, 255, 255, 22), width=1)
        draw.text((88, y + 193), "ПЛАН", font=_get_font(21, True), fill=muted)
        draw.text((88, y + 238), f"TP1 {_fmt_price(tp1)}   ·   TP2 {_fmt_price(tp2)}   ·   TP3 {_fmt_price(tp3)}", font=_get_font(29, True), fill=white)

    draw.text((58, 1010), f"{content_format.replace('_', ' ').upper()}  ·  не финансовая рекомендация", font=_get_font(17, False), fill=muted)
    watermark_font = _get_font(17, True)
    bbox = draw.textbbox((0, 0), WATERMARK, font=watermark_font)
    draw.text((width - 58 - (bbox[2] - bbox[0]), 1010), WATERMARK, font=watermark_font, fill=(151, 164, 184, 170))

    temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    path = temp.name
    temp.close()
    image.save(path, "PNG", optimize=True)
    logger.info("Editorial card generated: %s | format=%s | visual=%s", path, content_format, visual_style)
    return path
