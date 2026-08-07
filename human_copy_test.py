"""Offline regression test for human-feed copy.

It reproduces a BICO-like hot move (+7.47%/15m, +11.61%/45m, volume x18.08)
and checks that the selected post is compact, actionable and free from the
robotic phrases that triggered the Human Feed v6 rewrite.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from attention import AttentionSnapshot
from filters import SignalFilter
from indicators import build_trade_levels, calculate_multi_timeframe
from main import _best_post_variant
from memory import PostMemory
from self_test import _build_setup
from writer import _fmt_price


BANNED = (
    "направление у идеи",
    "граница ошибки",
    "диапазон контроля",
    "стоп является технической границей",
    "параметры сценария",
    "карта исполнения",
    "правило исполнения",
    "что вижу сейчас",
)


def main() -> None:
    logging.getLogger().setLevel(logging.ERROR)
    frames = _build_setup("long")
    mtf = calculate_multi_timeframe("BICOUSDT", frames)
    score = SignalFilter(min_score=0).evaluate(mtf)
    assert score is not None
    levels = build_trade_levels(mtf.tf_15m, "long")
    attention = AttentionSnapshot(
        score=90.0,
        change_15m=7.47,
        change_45m=11.61,
        volume_spike=18.08,
        range_expansion=4.8,
        turnover_1h=4_500_000.0,
        distance_atr=4.2,
        label="резкий всплеск внимания",
        overextended=True,
    )

    with tempfile.TemporaryDirectory() as temp_directory:
        memory = PostMemory(Path(temp_directory) / "post_memory.json")
        with patch.dict(os.environ, {"CONTENT_MODE": "deterministic"}, clear=False):
            selected = _best_post_variant(
                symbol="BICOUSDT",
                basic="BICO",
                mtf=mtf,
                score=score,
                levels=levels,
                memory=memory,
                btc=None,
                attention=attention,
            )

    assert selected is not None, "Hot BICO-like event produced no publishable post"
    draft, report = selected
    lowered = draft.text.lower().replace("ё", "е")
    assert report.valid, report.reasons
    assert report.score >= 90.0, report.score
    assert 240 <= len(draft.text) <= 560, len(draft.text)
    assert "$BICO" in draft.text
    assert "LONG" in draft.text.upper()
    assert _fmt_price(levels["tp1"]) in draft.text
    assert _fmt_price(levels["stop"]) in draft.text
    assert "+7.5%" in draft.text
    assert "x18,1" in draft.text
    assert not any(item in lowered for item in BANNED), draft.text
    assert draft.text.count("?") <= 1

    print(
        f"HUMAN COPY: OK | format={draft.content_format} | chars={len(draft.text)} | "
        f"quality={report.score:.1f}"
    )
    print("--- sample ---")
    print(draft.text)


if __name__ == "__main__":
    main()
