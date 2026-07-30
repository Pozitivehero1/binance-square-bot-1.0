"""Direction-aware signal scoring and hard safety gates."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from indicators import (
    IndicatorResult,
    MultiTimeframeIndicators,
    build_trade_levels,
    compute_confidence_score,
)

logger = logging.getLogger(__name__)


@dataclass
class SignalScore:
    total: float
    trend: float
    momentum: float
    volatility: float
    volume: float
    pattern: float
    multi_tf: float
    confidence: float
    risk_reward: float
    risk_reward_score: float
    direction: str = "long"
    passed_gates: bool = True
    gate_reasons: Tuple[str, ...] = ()


class SignalFilter:
    """Score a setup and apply either strict or balanced publication gates.

    Strict mode remains the default. Balanced mode is intended only as a
    second pass when an entire scan produced no strict candidate. It still
    requires trend strength, usable volatility, positive R/R, multi-timeframe
    confirmation and non-dead volume; it merely accepts near-threshold setups.
    """

    STRICT_THRESHOLDS = {
        "min_score": float(os.getenv("MIN_SIGNAL_SCORE", "54")),
        "min_adx": float(os.getenv("MIN_ADX", "18")),
        "min_atr_pct": float(os.getenv("MIN_ATR_PCT", "0.25")),
        "max_atr_pct": float(os.getenv("MAX_ATR_PCT", "7.0")),
        "min_rr": float(os.getenv("MIN_RR", "1.25")),
        "min_mtf_align": float(os.getenv("MIN_MTF_ALIGN", "0.34")),
        "min_volume_rel": float(os.getenv("MIN_VOLUME_REL", "0.65")),
    }
    BALANCED_THRESHOLDS = {
        "min_score": float(os.getenv("BALANCED_MIN_SIGNAL_SCORE", "50")),
        "min_adx": float(os.getenv("BALANCED_MIN_ADX", "16.5")),
        "min_atr_pct": float(os.getenv("BALANCED_MIN_ATR_PCT", "0.18")),
        "max_atr_pct": float(os.getenv("BALANCED_MAX_ATR_PCT", "8.5")),
        "min_rr": float(os.getenv("BALANCED_MIN_RR", "1.05")),
        "min_mtf_align": float(os.getenv("BALANCED_MIN_MTF_ALIGN", "0.33")),
        "min_volume_rel": float(os.getenv("BALANCED_MIN_VOLUME_REL", "0.30")),
    }

    def __init__(self, min_score: Optional[float] = None, profile: str = "strict"):
        profile = profile.lower().strip()
        if profile not in {"strict", "balanced"}:
            raise ValueError("profile must be 'strict' or 'balanced'")
        self.profile = profile
        thresholds = (
            self.STRICT_THRESHOLDS if profile == "strict" else self.BALANCED_THRESHOLDS
        )
        self.min_score = (
            float(min_score) if min_score is not None else thresholds["min_score"]
        )
        self.min_adx = thresholds["min_adx"]
        self.min_atr_pct = thresholds["min_atr_pct"]
        self.max_atr_pct = thresholds["max_atr_pct"]
        self.min_rr = thresholds["min_rr"]
        self.min_mtf_align = thresholds["min_mtf_align"]
        self.min_volume_rel = thresholds["min_volume_rel"]

    @staticmethod
    def _direction_strength(ind: IndicatorResult, tf1h, tf4h, direction: str) -> float:
        score = 0.0
        if direction == "long":
            score += 1.2 if ind.ema20 > ind.ema50 else -1.2
            score += 0.8 if ind.price > ind.ema20 else -0.8
            score += 0.8 if ind.macd_hist > 0 else -0.8
            score += 0.5 if ind.price >= ind.vwap else -0.5
            score += 0.4 if ind.change_1h > 0 else -0.4
            if ind.breakout_up or ind.pullback_long or ind.trend_continuation_long:
                score += 1.0
            if ind.false_breakout_up:
                score -= 1.2
        else:
            score += 1.2 if ind.ema20 < ind.ema50 else -1.2
            score += 0.8 if ind.price < ind.ema20 else -0.8
            score += 0.8 if ind.macd_hist < 0 else -0.8
            score += 0.5 if ind.price <= ind.vwap else -0.5
            score += 0.4 if ind.change_1h < 0 else -0.4
            if ind.breakout_down or ind.pullback_short or ind.trend_continuation_short:
                score += 1.0
            if ind.false_breakout_down:
                score -= 1.2

        for higher_tf, weight in ((tf1h, 0.8), (tf4h, 1.0)):
            if higher_tf is None:
                continue
            aligned = higher_tf.ema20 > higher_tf.ema50 if direction == "long" else higher_tf.ema20 < higher_tf.ema50
            score += weight if aligned else -weight
        return score

    @classmethod
    def _infer_direction(cls, ind: IndicatorResult, tf1h, tf4h) -> str:
        long_strength = cls._direction_strength(ind, tf1h, tf4h, "long")
        short_strength = cls._direction_strength(ind, tf1h, tf4h, "short")
        return "long" if long_strength >= short_strength else "short"

    def evaluate(self, mtf: MultiTimeframeIndicators) -> Optional[SignalScore]:
        ind = mtf.tf_15m
        if ind is None:
            logger.info("No 15m data for %s", mtf.symbol)
            return None

        tf1h, tf4h, tfd = mtf.tf_1h, mtf.tf_4h, mtf.tf_1d
        direction = self._infer_direction(ind, tf1h, tf4h)
        levels = build_trade_levels(ind, direction)
        actual_rr = float(levels["risk_reward"])

        trend = self._score_trend(ind, direction)
        momentum = self._score_momentum(ind, direction)
        volatility = self._score_volatility(ind)
        volume = self._score_volume(ind)
        pattern = self._score_pattern(ind, direction)
        multi_tf, mtf_ratio = self._score_multi_tf(direction, tf1h, tf4h, tfd)
        confidence = compute_confidence_score(mtf, direction)
        rr_score = self._score_risk_reward(actual_rr)

        mtf.confidence_score = confidence
        total = (
            trend * 0.24
            + momentum * 0.15
            + volatility * 0.08
            + volume * 0.13
            + pattern * 0.13
            + multi_tf * 0.17
            + confidence * 0.05
            + rr_score * 0.05
        )

        atr_pct = ind.atr / ind.price * 100.0 if ind.price else 0.0
        gate_reasons: List[str] = []
        if ind.adx < self.min_adx:
            gate_reasons.append(f"ADX {ind.adx:.1f} < {self.min_adx:.1f}")
        if not self.min_atr_pct <= atr_pct <= self.max_atr_pct:
            gate_reasons.append(
                f"ATR {atr_pct:.2f}% outside {self.min_atr_pct:.2f}-{self.max_atr_pct:.2f}%"
            )
        if actual_rr < self.min_rr:
            gate_reasons.append(f"R/R {actual_rr:.2f} < {self.min_rr:.2f}")
        available_higher_tfs = sum(item is not None for item in (tf1h, tf4h, tfd))
        if available_higher_tfs < 2:
            gate_reasons.append("fewer than two higher timeframes available")
        if mtf_ratio < self.min_mtf_align:
            gate_reasons.append(
                f"MTF alignment {mtf_ratio:.2f} < {self.min_mtf_align:.2f}"
            )
        if ind.volume_relative < self.min_volume_rel:
            gate_reasons.append(
                f"relative volume {ind.volume_relative:.2f} < {self.min_volume_rel:.2f}"
            )
        if direction == "long" and ind.false_breakout_up and not ind.liquidity_sweep_down:
            gate_reasons.append("bullish setup invalidated by failed breakout")
        if direction == "short" and ind.false_breakout_down and not ind.liquidity_sweep_up:
            gate_reasons.append("bearish setup invalidated by failed breakdown")

        return SignalScore(
            total=total,
            trend=trend,
            momentum=momentum,
            volatility=volatility,
            volume=volume,
            pattern=pattern,
            multi_tf=multi_tf,
            confidence=confidence,
            risk_reward=actual_rr,
            risk_reward_score=rr_score,
            direction=direction,
            passed_gates=not gate_reasons,
            gate_reasons=tuple(gate_reasons),
        )

    @staticmethod
    def _score_trend(ind: IndicatorResult, direction: str) -> float:
        score = 0.0
        if direction == "long":
            if ind.ema20 > ind.ema50:
                score += 25
            if ind.ema50 > ind.ema200:
                score += 20
            if ind.macd_hist > 0:
                score += 20
            if ind.price > ind.ema20:
                score += 15
            if ind.price >= ind.vwap:
                score += 10
        else:
            if ind.ema20 < ind.ema50:
                score += 25
            if ind.ema50 < ind.ema200:
                score += 20
            if ind.macd_hist < 0:
                score += 20
            if ind.price < ind.ema20:
                score += 15
            if ind.price <= ind.vwap:
                score += 10
        if ind.adx >= 25:
            score += 10
        return min(score, 100.0)

    @staticmethod
    def _score_momentum(ind: IndicatorResult, direction: str) -> float:
        score = 0.0
        if direction == "long":
            if 48 <= ind.rsi <= 67:
                score += 35
            elif 40 <= ind.rsi < 48:
                score += 20
            elif 67 < ind.rsi <= 73:
                score += 12
            if ind.cci > 0:
                score += 15
            if ind.stoch_rsi_k > ind.stoch_rsi_d:
                score += 15
            if ind.change_1h > 0:
                score += 15
            if ind.change_4h > 0:
                score += 20
        else:
            if 33 <= ind.rsi <= 52:
                score += 35
            elif 52 < ind.rsi <= 60:
                score += 20
            elif 27 <= ind.rsi < 33:
                score += 12
            if ind.cci < 0:
                score += 15
            if ind.stoch_rsi_k < ind.stoch_rsi_d:
                score += 15
            if ind.change_1h < 0:
                score += 15
            if ind.change_4h < 0:
                score += 20
        return min(score, 100.0)

    @staticmethod
    def _score_volatility(ind: IndicatorResult) -> float:
        atr_pct = ind.atr / ind.price * 100.0 if ind.price else 0.0
        if 0.45 <= atr_pct <= 2.8:
            return 100.0
        if 0.25 <= atr_pct < 0.45 or 2.8 < atr_pct <= 4.5:
            return 68.0
        if atr_pct < 0.20 or atr_pct > 7.0:
            return 10.0
        return 38.0

    @staticmethod
    def _score_volume(ind: IndicatorResult) -> float:
        value = ind.volume_relative
        if 1.8 <= value <= 6.0:
            return 100.0
        if 1.4 <= value < 1.8:
            return 82.0
        if 1.1 <= value < 1.4:
            return 62.0
        if 0.85 <= value < 1.1:
            return 38.0
        if value > 6.0:
            return 55.0
        return 12.0

    @staticmethod
    def _score_pattern(ind: IndicatorResult, direction: str) -> float:
        score = 0.0
        if direction == "long":
            if ind.breakout_up:
                score += 42
            if ind.pullback_long:
                score += 32
            if ind.trend_continuation_long:
                score += 22
            if ind.liquidity_sweep_down:
                score += 24
            if ind.false_breakout_up:
                score -= 35
        else:
            if ind.breakout_down:
                score += 42
            if ind.pullback_short:
                score += 32
            if ind.trend_continuation_short:
                score += 22
            if ind.liquidity_sweep_up:
                score += 24
            if ind.false_breakout_down:
                score -= 35
        return max(0.0, min(score, 100.0))

    @staticmethod
    def _score_multi_tf(direction: str, tf1h, tf4h, tfd) -> Tuple[float, float]:
        aligns = 0
        total = 0
        weighted = 0.0
        total_weight = 0.0
        for indicator, weight in ((tf1h, 1.0), (tf4h, 1.3), (tfd, 0.9)):
            if indicator is None:
                continue
            total += 1
            total_weight += weight
            is_aligned = (
                indicator.ema20 > indicator.ema50
                if direction == "long"
                else indicator.ema20 < indicator.ema50
            )
            if is_aligned:
                aligns += 1
                weighted += weight
        if total == 0:
            return 45.0, 0.5
        ratio = aligns / total
        weighted_ratio = weighted / total_weight if total_weight else ratio
        return weighted_ratio * 100.0, ratio

    @staticmethod
    def _score_risk_reward(rr: float) -> float:
        if rr >= 4.0:
            return 100.0
        if rr >= 3.0:
            return 90.0
        if rr >= 2.2:
            return 78.0
        if rr >= 1.8:
            return 64.0
        if rr >= 1.5:
            return 50.0
        if rr >= 1.0:
            return 22.0
        return 5.0


def score_signal(mtf: MultiTimeframeIndicators) -> float:
    result = SignalFilter().evaluate(mtf)
    return result.total if result else 0.0


def get_top_candidates(
    mtf_list: List[MultiTimeframeIndicators],
    top_n: int = 5,
    require_gates: bool = True,
    profile: str = "strict",
) -> List[Tuple[MultiTimeframeIndicators, SignalScore]]:
    signal_filter = SignalFilter(profile=profile)
    scored: List[Tuple[MultiTimeframeIndicators, SignalScore]] = []
    for mtf in mtf_list:
        score = signal_filter.evaluate(mtf)
        if score is None or score.total < signal_filter.min_score:
            continue
        if require_gates and not score.passed_gates:
            logger.info(
                "Rejected %s [%s]: %s",
                mtf.symbol,
                profile,
                "; ".join(score.gate_reasons),
            )
            continue
        scored.append((mtf, score))
    scored.sort(key=lambda item: item[1].total, reverse=True)
    return scored[:top_n]
