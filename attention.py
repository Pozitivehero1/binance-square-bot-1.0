"""Measure whether a technical setup is receiving attention *right now*.

The old selector mostly rewarded a clean technical structure. That can choose a
coin whose interesting move already happened. This module scores only closed
15-minute candles and prefers fresh price acceleration, abnormal volume,
expanding candle ranges and meaningful USDT turnover.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class AttentionSnapshot:
    score: float
    change_15m: float
    change_45m: float
    volume_spike: float
    range_expansion: float
    turnover_1h: float
    distance_atr: float
    label: str
    overextended: bool


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _safe_ratio(value: float, baseline: float, default: float = 1.0) -> float:
    if not math.isfinite(value) or not math.isfinite(baseline) or baseline <= 0:
        return default
    return max(0.01, value / baseline)


def _pct(current: float, previous: float) -> float:
    if not math.isfinite(current) or not math.isfinite(previous) or previous == 0:
        return 0.0
    return (current - previous) / previous * 100.0


def _label(score: float, volume_spike: float, change_15m: float) -> str:
    if score >= 78 and volume_spike >= 2.0:
        return "резкий всплеск внимания"
    if score >= 66:
        return "активное движение"
    if score >= 54:
        return "растущий интерес"
    if abs(change_15m) >= 0.7:
        return "движение без сильного объёма"
    return "обычная рыночная активность"


def compute_attention(frame: Optional[pd.DataFrame], indicator, direction: str) -> AttentionSnapshot:
    """Return a 0-100 current-attention score from closed 15m candles."""
    if frame is None or len(frame) < 24:
        return AttentionSnapshot(35.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, "нет данных о свежем импульсе", False)

    data = frame.tail(80).copy()
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["open", "high", "low", "close", "volume"])
    if len(data) < 24:
        return AttentionSnapshot(35.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, "нет данных о свежем импульсе", False)

    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]

    change_15m = _pct(float(close.iloc[-1]), float(close.iloc[-2]))
    change_45m = _pct(float(close.iloc[-1]), float(close.iloc[-4]))

    previous_volume = float(volume.iloc[-21:-1].median())
    volume_spike = _safe_ratio(float(volume.iloc[-1]), previous_volume)

    ranges = (high - low).abs()
    previous_range = float(ranges.iloc[-21:-1].median())
    range_expansion = _safe_ratio(float(ranges.iloc[-1]), previous_range)

    turnover_1h = float((close.tail(4) * volume.tail(4)).sum())
    turnover_component = _clamp((math.log10(max(turnover_1h, 1.0)) - 4.5) * 28.0)

    motion_component = _clamp(abs(change_15m) * 45.0 + abs(change_45m) * 18.0)
    volume_component = _clamp(48.0 + math.log2(max(volume_spike, 0.05)) * 24.0)
    range_component = _clamp(46.0 + math.log2(max(range_expansion, 0.05)) * 20.0)

    aligned = (direction == "long" and change_15m > 0 and change_45m > 0) or (
        direction == "short" and change_15m < 0 and change_45m < 0
    )
    alignment_bonus = 7.0 if aligned else -5.0

    atr = max(float(getattr(indicator, "atr", 0.0) or 0.0), 1e-12)
    ema20 = float(getattr(indicator, "ema20", close.iloc[-1]) or close.iloc[-1])
    price = float(getattr(indicator, "price", close.iloc[-1]) or close.iloc[-1])
    distance_atr = abs(price - ema20) / atr
    rsi = float(getattr(indicator, "rsi", 50.0) or 50.0)
    overextended = distance_atr >= 2.7 or (direction == "long" and rsi >= 80.0) or (
        direction == "short" and rsi <= 20.0
    )
    overextension_penalty = 13.0 if overextended else 0.0
    # A genuine high-volume breakout can still be interesting even when extended,
    # but a late low-volume chase should fall down the ranking.
    if overextended and volume_spike >= 2.8 and abs(change_15m) >= 0.8:
        overextension_penalty = 5.0

    score = (
        motion_component * 0.34
        + volume_component * 0.27
        + range_component * 0.14
        + turnover_component * 0.25
        + alignment_bonus
        - overextension_penalty
    )
    score = _clamp(score)
    return AttentionSnapshot(
        score=score,
        change_15m=change_15m,
        change_45m=change_45m,
        volume_spike=volume_spike,
        range_expansion=range_expansion,
        turnover_1h=turnover_1h,
        distance_atr=distance_atr,
        label=_label(score, volume_spike, change_15m),
        overextended=overextended,
    )


def format_turnover(value: float) -> str:
    value = max(0.0, float(value))
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"
