"""Create an offline preview of editorial posts and media.

No Binance, Mistral or publishing request is made. The script uses the same
synthetic market fixtures as the safety tests, runs the real generator and
writes a contact sheet plus individual images to ``preview_output``.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from card import generate_card
from attention import compute_attention
from chart import generate_chart
from filters import SignalFilter
from indicators import build_trade_levels, calculate_multi_timeframe
from memory import PostMemory
from self_test import _build_setup
from writer import generate_post_candidates


CARD_VISUALS = {
    "headline_card",
    "split_scenario",
    "risk_card",
    "journal_card",
    "indicator_card",
    "data_card",
    "followup_card",
    "pulse_card",
}


def _font(size: int, bold: bool = False):
    candidates = (
        "/usr/share/fonts/truetype/dejavu//DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    )
    for item in candidates:
        if os.path.exists(item):
            return ImageFont.truetype(item, size)
    return ImageFont.load_default()


def _unique_visual_drafts():
    frames = _build_setup("long")
    mtf = calculate_multi_timeframe("TESTUSDT", frames)
    score = SignalFilter(min_score=0).evaluate(mtf)
    if score is None or mtf.tf_15m is None:
        raise RuntimeError("Synthetic setup could not be evaluated")
    levels = build_trade_levels(mtf.tf_15m, score.direction)
    attention = compute_attention(frames["15m"], mtf.tf_15m, score.direction)
    with tempfile.TemporaryDirectory() as temp_directory:
        memory = PostMemory(Path(temp_directory) / "preview_memory.json")
        with patch.dict(os.environ, {"CONTENT_MODE": "deterministic"}, clear=False):
            drafts = generate_post_candidates(
                symbol="TESTUSDT",
                basic="TEST",
                mtf=mtf,
                score=score,
                memory=memory,
                levels=levels,
                attention=attention,
                variant_count=16,
            )
    unique = []
    seen = set()
    for draft in drafts:
        if draft.visual_style in seen:
            continue
        seen.add(draft.visual_style)
        unique.append(draft)
    return frames, mtf, score, levels, attention, unique


def _contact_sheet(paths: list[Path], output_path: Path) -> None:
    columns = 4
    tile = 410
    gap = 18
    header = 80
    rows = (len(paths) + columns - 1) // columns
    width = gap + columns * (tile + gap)
    height = header + gap + rows * (tile + gap)
    sheet = Image.new("RGB", (width, height), (13, 18, 29))
    draw = ImageDraw.Draw(sheet)
    draw.text((gap, 18), "Binance Square — offline media preview", font=_font(31, True), fill=(244, 247, 252))
    for index, path in enumerate(paths):
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((tile, tile), Image.Resampling.LANCZOS)
            x = gap + (index % columns) * (tile + gap)
            y = header + gap + (index // columns) * (tile + gap)
            canvas = Image.new("RGB", (tile, tile), (20, 27, 42))
            offset = ((tile - image.width) // 2, (tile - image.height) // 2)
            canvas.paste(image, offset)
            sheet.paste(canvas, (x, y))
    sheet.save(output_path, "PNG", optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate offline post and media previews")
    parser.add_argument("--output", default="preview_output", help="output directory")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    frames, mtf, score, levels, attention, drafts = _unique_visual_drafts()
    if not drafts:
        raise RuntimeError("No valid drafts generated")

    written_images: list[Path] = []
    text_blocks: list[str] = []
    indicator = mtf.tf_15m
    assert indicator is not None

    for index, draft in enumerate(drafts, 1):
        if draft.visual_style in CARD_VISUALS:
            temporary = generate_card(
                basic="TEST",
                direction=score.direction,
                entry=levels["entry"],
                tp1=levels["tp1"],
                tp2=levels["tp2"],
                tp3=levels["tp3"],
                stop=levels["stop"],
                rr=levels["risk_reward"],
                confidence=score.total,
                change_1h=indicator.change_1h,
                post_style=draft.style_id,
                signal_label=draft.angle_title,
                content_format=draft.content_format,
                visual_style=draft.visual_style,
                headline=draft.headline,
                rsi=indicator.rsi,
                adx=indicator.adx,
                volume_relative=indicator.volume_relative,
                change_15m=attention.change_15m,
                fresh_volume=attention.volume_spike,
                attention_score=attention.score,
            )
        else:
            temporary = generate_chart(
                "TESTUSDT",
                frames["15m"],
                "TEST",
                entry=levels["entry"],
                tp1=levels["tp1"],
                tp2=levels["tp2"],
                tp3=levels["tp3"],
                stop=levels["stop"],
                direction=score.direction,
                support=indicator.support,
                resistance=indicator.resistance,
                vol_rel=indicator.volume_relative,
                indicator=indicator,
                visual_style=draft.visual_style,
                headline=draft.headline,
                signal_label=draft.angle_title,
            )
        if not temporary:
            raise RuntimeError(f"Media generation failed for {draft.visual_style}")
        suffix = "card" if draft.visual_style in CARD_VISUALS else "chart"
        destination = output / f"{index:02d}_{draft.visual_style}_{suffix}.png"
        shutil.move(temporary, destination)
        written_images.append(destination)
        text_blocks.append(
            f"=== {index:02d} | format={draft.content_format} | visual={draft.visual_style} | angle={draft.signal_type} ===\n{draft.text}\n"
        )

    (output / "sample_posts.txt").write_text("\n".join(text_blocks), encoding="utf-8")
    _contact_sheet(written_images, output / "contact_sheet.png")
    print(f"PREVIEW: OK | drafts={len(drafts)} | images={len(written_images)} | output={output}")
    print("No market, Mistral or publishing network call was attempted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
