"""Offline smoke test for indicators, filters, post text, card and chart.

Run:
    python self_test.py

The test never publishes anything and does not call market APIs.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from card import generate_card
from chart import generate_chart
from filters import SignalFilter, get_top_candidates
from indicators import build_trade_levels, calculate_multi_timeframe
from memory import PostMemory
from quality import PostQualityEvaluator
from writer import generate_post_draft, generate_post_with_memory


def _make_frame(rng, frequency: str, slope: float, rows: int = 260) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=rows, freq=frequency, tz="UTC")
    close = 100 + slope * np.arange(rows) + np.cumsum(rng.normal(0, 0.11, rows))
    open_price = np.r_[close[0], close[:-1]] + rng.normal(0, 0.04, rows)
    high = np.maximum(open_price, close) + rng.uniform(0.08, 0.24, rows)
    low = np.minimum(open_price, close) - rng.uniform(0.08, 0.24, rows)
    volume = rng.uniform(900, 1700, rows)
    volume[-1] = 3300
    return pd.DataFrame(
        {"open": open_price, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def _build_setup(side: str):
    rng = np.random.default_rng(17 if side == "long" else 29)
    slopes = (0.065, 0.14, 0.30, 0.55) if side == "long" else (-0.065, -0.14, -0.30, -0.55)
    frames = {
        interval: _make_frame(rng, frequency, slope)
        for interval, frequency, slope in zip(
            ("15m", "1h", "4h", "1d"),
            ("15min", "1h", "4h", "1d"),
            slopes,
        )
    }

    current = frames["15m"]
    if side == "long":
        level = current["high"].iloc[-51:-1].max()
        current.iloc[-1] = [level * 1.001, level * 1.012, level * 0.999, level * 1.008, 3300]
    else:
        level = current["low"].iloc[-51:-1].min()
        current.iloc[-1] = [level * 0.999, level * 1.001, level * 0.988, level * 0.992, 3300]
    return frames


def _test_side(side: str) -> None:
    frames = _build_setup(side)
    mtf = calculate_multi_timeframe("TESTUSDT", frames)
    score = SignalFilter(min_score=0).evaluate(mtf)
    assert score is not None, "Signal score was not calculated"
    assert score.direction == side, f"Expected {side}, got {score.direction}"
    assert score.passed_gates, f"Signal failed gates: {score.gate_reasons}"

    levels = build_trade_levels(mtf.tf_15m, side)
    with tempfile.TemporaryDirectory() as temp_directory:
        memory = PostMemory(Path(temp_directory) / "post_memory.json")
        text = generate_post_with_memory(
            symbol="TESTUSDT",
            basic="TEST",
            mtf=mtf,
            score=score,
            memory=memory,
            levels=levels,
        )
        report = PostQualityEvaluator().report(
            text,
            basic="TEST",
            direction=side,
            levels=levels,
        )
        assert report.valid, report.reasons
        assert report.score >= 72, report.score
        memory.add_post("TESTUSDT", text)
        assert memory.is_similar(text), "Memory did not recognize an identical post"

    card_path = generate_card(
        "TEST",
        side,
        levels["entry"],
        levels["tp1"],
        levels["tp2"],
        levels["tp3"],
        levels["stop"],
        levels["risk_reward"],
        score.total,
        mtf.tf_15m.change_1h,
    )
    chart_path = generate_chart(
        "TESTUSDT",
        frames["15m"],
        "TEST",
        entry=levels["entry"],
        tp1=levels["tp1"],
        tp2=levels["tp2"],
        tp3=levels["tp3"],
        stop=levels["stop"],
        direction=side,
        support=mtf.tf_15m.support,
        resistance=mtf.tf_15m.resistance,
        vol_rel=mtf.tf_15m.volume_relative,
        indicator=mtf.tf_15m,
    )
    try:
        assert card_path and os.path.getsize(card_path) > 10_000
        assert chart_path and os.path.getsize(chart_path) > 10_000
    finally:
        for path in (card_path, chart_path):
            if path and os.path.exists(path):
                os.remove(path)

    print(
        f"{side.upper()}: OK | score={score.total:.1f} | "
        f"R/R={score.risk_reward:.2f} | post quality={report.score:.1f}"
    )



def _normalized_similarity(left: str, right: str) -> float:
    return PostMemory.compare_texts(left, right)


def _test_content_diversity() -> None:
    frames = _build_setup("long")
    mtf = calculate_multi_timeframe("TESTUSDT", frames)
    score = SignalFilter(min_score=0).evaluate(mtf)
    assert score is not None
    levels = build_trade_levels(mtf.tf_15m, "long")

    with tempfile.TemporaryDirectory() as temp_directory:
        memory = PostMemory(Path(temp_directory) / "post_memory.json")
        drafts = [
            generate_post_draft(
                symbol="TESTUSDT",
                basic="TEST",
                mtf=mtf,
                score=score,
                memory=memory,
                levels=levels,
                variant_index=index,
            )
            for index in range(30)
        ]

    styles = {draft.style_id for draft in drafts}
    signals = {draft.signal_type for draft in drafts}
    similarities = [
        _normalized_similarity(drafts[i].text, drafts[j].text)
        for i in range(len(drafts))
        for j in range(i)
    ]
    assert len(styles) == 30, f"Not enough post styles: {styles}"
    assert len(signals) >= 5, f"Not enough signal angles: {signals}"
    assert max(similarities) < 0.56, f"Variants are too similar: {max(similarities):.3f}"
    print(
        f"DIVERSITY: OK | styles={len(styles)} | signals={len(signals)} | "
        f"max_similarity={max(similarities):.3f}"
    )


def _test_balanced_fallback() -> None:
    frames = _build_setup("long")
    frames["15m"].iloc[-1, frames["15m"].columns.get_loc("volume")] = 550
    mtf = calculate_multi_timeframe("BALANCEDUSDT", frames)

    strict = SignalFilter(min_score=0, profile="strict").evaluate(mtf)
    balanced = SignalFilter(min_score=0, profile="balanced").evaluate(mtf)
    assert strict is not None and balanced is not None
    assert not strict.passed_gates
    assert any("relative volume" in reason for reason in strict.gate_reasons)
    assert balanced.passed_gates, balanced.gate_reasons

    strict_ranked = get_top_candidates([mtf], top_n=1, profile="strict")
    balanced_ranked = get_top_candidates([mtf], top_n=1, profile="balanced")
    assert not strict_ranked
    assert balanced_ranked and balanced_ranked[0][0].symbol == "BALANCEDUSDT"
    print(
        "BALANCED FALLBACK: OK | "
        f"volume={mtf.tf_15m.volume_relative:.2f} | score={balanced.total:.1f}"
    )

def main() -> None:
    _test_side("long")
    _test_side("short")
    _test_content_diversity()
    _test_balanced_fallback()
    print("All offline tests passed. No publication was attempted.")


if __name__ == "__main__":
    main()
