"""Offline smoke test for indicators, filters, post text, card and chart.

Run:
    python self_test.py

The test never publishes anything and does not call market APIs.
"""
from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from card import generate_card
from chart import generate_chart
from filters import SignalFilter, get_top_candidates
from indicators import build_trade_levels, calculate_multi_timeframe
from memory import PostMemory
from quality import PostQualityEvaluator
from publisher import publish
from writer import FULL_PLAN_FORMATS, generate_post_candidates, _ticker_count


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
        with patch.dict(os.environ, {"CONTENT_MODE": "deterministic"}, clear=False):
            drafts = generate_post_candidates(
                symbol="TESTUSDT",
                basic="TEST",
                mtf=mtf,
                score=score,
                memory=memory,
                levels=levels,
                variant_count=8,
            )
        assert drafts, "No post candidates were generated"
        text = drafts[0].text
        report = PostQualityEvaluator().report(
            text,
            basic="TEST",
            direction=side,
            levels=levels,
            content_format=drafts[0].content_format,
            headline=drafts[0].headline,
        )
        assert report.valid, report.reasons
        assert report.score >= 78, report.score
        assert 1 <= _ticker_count(text, "TEST") <= 3
        if drafts[0].content_format in FULL_PLAN_FORMATS:
            assert all(marker in text for marker in ("Вход:", "Цели:", "Стоп-лосс:"))
        else:
            assert any(marker in text for marker in ("Ключевой уровень:", "Уровень решения:", "Что отслеживаю:"))
            assert "TP1:" in text or "Первая цель:" in text or "Цель при подтверждении:" in text
        memory.add_post(
            "TESTUSDT",
            text,
            post_style=drafts[0].style_id,
            signal_type=drafts[0].signal_type,
            content_format=drafts[0].content_format,
            visual_style=drafts[0].visual_style,
            direction=side,
            levels=levels,
            market_price=mtf.tf_15m.price,
        )
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
        content_format=drafts[0].content_format,
        visual_style=drafts[0].visual_style,
        headline=drafts[0].headline,
        signal_label=drafts[0].angle_title,
        rsi=mtf.tf_15m.rsi,
        adx=mtf.tf_15m.adx,
        volume_relative=mtf.tf_15m.volume_relative,
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
        visual_style=drafts[0].visual_style,
        headline=drafts[0].headline,
        signal_label=drafts[0].angle_title,
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
        with patch.dict(os.environ, {"CONTENT_MODE": "deterministic"}, clear=False):
            drafts = generate_post_candidates(
                symbol="TESTUSDT",
                basic="TEST",
                mtf=mtf,
                score=score,
                memory=memory,
                levels=levels,
                variant_count=8,
            )

    styles = {draft.style_id for draft in drafts}
    formats = {draft.content_format for draft in drafts}
    visuals = {draft.visual_style for draft in drafts}
    signals = {draft.signal_type for draft in drafts}
    similarities = [
        _normalized_similarity(drafts[i].text, drafts[j].text)
        for i in range(len(drafts))
        for j in range(i)
    ]
    lengths = [len(draft.text) for draft in drafts]
    assert len(drafts) >= 6, f"Not enough candidates: {len(drafts)}"
    assert len(styles) >= 8, f"Not enough layouts: {styles}"
    assert len(formats) >= 8, f"Not enough editorial formats: {formats}"
    assert len(visuals) >= 7, f"Not enough visual families: {visuals}"
    assert len(signals) >= 5, f"Not enough signal angles: {signals}"
    assert all(not __import__("re").search(r"^\$TEST\s*[—-]\s*(?:LONG|SHORT)\s*:", draft.headline) for draft in drafts)
    assert max(similarities) < 0.45, f"Variants are too similar: {max(similarities):.3f}"
    assert max(lengths) <= 900, lengths
    print(
        f"DIVERSITY: OK | layouts={len(styles)} | formats={len(formats)} | visuals={len(visuals)} | signals={len(signals)} | "
        f"max_similarity={max(similarities):.3f} | avg_chars={sum(lengths)/len(lengths):.0f}"
    )


def _test_mistral_fact_lock() -> None:
    frames = _build_setup("long")
    mtf = calculate_multi_timeframe("TESTUSDT", frames)
    score = SignalFilter(min_score=0).evaluate(mtf)
    assert score is not None
    levels = build_trade_levels(mtf.tf_15m, "long")
    payload = {
        "candidates": [
            {
                "format_id": "hot_reaction",
                "hook": "Очевидное направление ещё не означает хорошую цену исполнения",
                "insight": "Сначала проверяю качество реакции, а уже потом принимаю решение.",
                "question": "Какой контраргумент к этому сценарию для вас сильнее всего?",
                "fact_ids": ["volume", "vwap", "risk_math"],
            },
            {
                "format_id": "crowd_trap",
                "hook": "Цифры полезны только вместе с понятным условием сделки",
                "insight": "Данные подтверждают наблюдение, но не заменяют реакцию цены.",
                "question": "Вы бы входили после такой свечи или ждали повторный тест?",
                "fact_ids": ["momentum", "volume", "changes"],
            },
            {
                "format_id": "why_wait",
                "hook": "Отдельный индикатор легко создаёт ложную уверенность",
                "insight": "Связка структуры и уровня важнее одного показателя.",
                "question": "Какой индикатор вы проверяете последним перед входом?",
                "fact_ids": ["momentum", "trend", "vwap"],
            },
            {
                "format_id": "level_story",
                "hook": "Вся идея сводится к реакции возле одной границы",
                "insight": "Если рынок не удерживает уровень, направление перестаёт иметь значение.",
                "question": "Вы бы ждали закрытие свечи или повторный тест?",
                "fact_ids": ["range", "trend", "volume"],
            },
        ]
    }
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    }

    with tempfile.TemporaryDirectory() as temp_directory:
        memory = PostMemory(Path(temp_directory) / "post_memory.json")
        with patch.dict(
            os.environ,
            {"CONTENT_MODE": "ai_first", "MISTRAL_API": "test-key"},
            clear=False,
        ), patch("writer.requests.post", return_value=response) as mocked_post:
            drafts = generate_post_candidates(
                symbol="TESTUSDT",
                basic="TEST",
                mtf=mtf,
                score=score,
                memory=memory,
                levels=levels,
                variant_count=4,
            )

    assert mocked_post.call_count == 1, "Expected one batched Mistral request"
    assert len(drafts) == 4, [draft.style_id for draft in drafts]
    ai_drafts = [draft for draft in drafts if draft.style_id.startswith("ai_")]
    assert len(ai_drafts) >= 3, [draft.style_id for draft in drafts]
    for draft in ai_drafts:
        assert draft.content_format in {"hot_reaction", "crowd_trap", "why_wait", "level_story"}
        assert 1 <= _ticker_count(draft.text, "TEST") <= 3
        if draft.content_format in FULL_PLAN_FORMATS:
            assert all(marker in draft.text for marker in ("Вход:", "Цели:", "Стоп-лосс:"))
        else:
            assert any(marker in draft.text for marker in ("Ключевой уровень:", "Уровень решения:", "Что отслеживаю:"))
        report = PostQualityEvaluator().report(
            draft.text,
            basic="TEST",
            direction="long",
            levels=levels,
            content_format=draft.content_format,
            headline=draft.headline,
        )
        assert report.valid, report.reasons
    print("MISTRAL FACT LOCK: OK | one request | AI drafts fact-locked | deterministic fill")


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


def _test_publisher_command() -> None:
    with tempfile.TemporaryDirectory() as temp_directory:
        skill_dir = Path(temp_directory) / "square-post"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "post-image.mjs").write_text("// test stub", encoding="utf-8")
        image = Path(temp_directory) / "chart.png"
        image.write_bytes(b"test-image")

        completed = Mock(returncode=0, stdout="Success! ID: test", stderr="")
        with patch.dict(os.environ, {"SQUARE_API": "test-square-key"}, clear=False), \
             patch("publisher.find_skill_dir", return_value=str(skill_dir)), \
             patch("publisher.subprocess.run", return_value=completed) as mocked_run:
            assert publish("$TEST — test post", image_path=str(image))

        command = mocked_run.call_args.args[0]
        assert command[0] == "node"
        assert command[1].endswith("post-image.mjs")
        assert command[2:5] == ["--text", "$TEST — test post", "--images"]
        assert command[5] == str(image.resolve())
        environment = mocked_run.call_args.kwargs["env"]
        assert environment["BINANCE_SQUARE_OPENAPI_KEY"] == "test-square-key"
    print("PUBLISHER COMMAND: OK | image script | key only in environment")

def main() -> None:
    _test_side("long")
    _test_side("short")
    _test_content_diversity()
    _test_mistral_fact_lock()
    _test_balanced_fallback()
    _test_publisher_command()
    print("All offline tests passed. No publication was attempted.")


if __name__ == "__main__":
    main()
