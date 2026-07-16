"""Signal scoring and filtering.

v3 changes vs original:
- Removed duplicated SignalFilter / SignalScore definitions (the original file
  had the same class defined twice).
- Direction-aware scoring: separate long/short logic instead of long-only.
- Rebalanced weights: multi-TF alignment and volume weigh more; pattern
  bucket cannot rescue a bad trend.
- Hard gates: ADX floor, ATR floor, min risk/reward, min multi-TF alignment.
- Returns direction ('long' / 'short') alongside the score.
"""

import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass
from indicators import MultiTimeframeIndicators, IndicatorResult

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
    direction: str = "long"      # 'long' or 'short'
    passed_gates: bool = True    # False = filtered out even if score is ok


class SignalFilter:
    # Hard gates — a signal that fails any of these is rejected regardless of score
    MIN_SCORE = 45.0            # was 25; raised to reduce noise
    MIN_ADX = 18.0              # trend strength minimum
    MIN_ATR_PCT = 0.25          # dead markets skipped
    MAX_ATR_PCT = 8.0           # too volatile / news event skipped
    MIN_RR = 1.5                # risk/reward floor
    MIN_MTF_ALIGN = 0.5         # at least half of higher TFs must agree

    def __init__(self, min_score: Optional[float] = None):
        if min_score is not None:
            self.MIN_SCORE = min_score

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _infer_direction(ind: IndicatorResult, tf1h, tf4h) -> str:
        """Bullish vs bearish setup based on structure across 15m/1h/4h."""
        votes = 0
        if ind.ema20 > ind.ema50: votes += 1
        if ind.price > ind.ema20: votes += 1
        if ind.macd > ind.macd_signal: votes += 1
        if tf1h is not None and tf1h.ema20 > tf1h.ema50: votes += 1
        if tf4h is not None and tf4h.ema20 > tf4h.ema50: votes += 1
        return "long" if votes >= 3 else "short"

    # ------------------------------------------------------------------ scoring
    def evaluate(self, mtf: MultiTimeframeIndicators) -> Optional[SignalScore]:
        if not mtf.tf_15m:
            logger.warning(f"No 15m data for {mtf.symbol}")
            return None

        ind = mtf.tf_15m
        tf1h, tf4h, tfd = mtf.tf_1h, mtf.tf_4h, mtf.tf_1d
        direction = self._infer_direction(ind, tf1h, tf4h)

        s_trend = self._score_trend(ind, direction)
        s_mom = self._score_momentum(ind, direction)
        s_vol = self._score_volatility(ind)
        s_volu = self._score_volume(ind)
        s_pat = self._score_pattern(ind, direction)
        s_mtf, mtf_ratio = self._score_multi_tf(ind, tf1h, tf4h, tfd)
        s_conf = float(mtf.confidence_score)
        s_rr = self._score_risk_reward(ind)

        total = (
            s_trend * 0.22 +
            s_mom   * 0.15 +
            s_vol   * 0.08 +
            s_volu  * 0.13 +
            s_pat   * 0.12 +
            s_mtf   * 0.18 +   # multi-TF alignment weighs more
            s_conf  * 0.05 +
            s_rr    * 0.07
        )

        # Hard gates
        atr_pct = ind.atr / ind.price * 100 if ind.price else 0
        gates_ok = (
            ind.adx >= self.MIN_ADX
            and self.MIN_ATR_PCT <= atr_pct <= self.MAX_ATR_PCT
            and ind.risk_reward >= self.MIN_RR
            and mtf_ratio >= self.MIN_MTF_ALIGN
        )

        return SignalScore(
            total=total,
            trend=s_trend, momentum=s_mom, volatility=s_vol,
            volume=s_volu, pattern=s_pat, multi_tf=s_mtf,
            confidence=s_conf, risk_reward=s_rr,
            direction=direction, passed_gates=gates_ok,
        )

    # ------------------------------------------------------------------ buckets
    def _score_trend(self, ind: IndicatorResult, direction: str) -> float:
        score = 0.0
        if direction == "long":
            if ind.ema20 > ind.ema50: score += 25
            if ind.ema50 > ind.ema200: score += 20
            if ind.macd > ind.macd_signal: score += 20
            if ind.price > ind.ema20: score += 15
        else:
            if ind.ema20 < ind.ema50: score += 25
            if ind.ema50 < ind.ema200: score += 20
            if ind.macd < ind.macd_signal: score += 20
            if ind.price < ind.ema20: score += 15
        if ind.adx > 25: score += 20
        return min(score, 100.0)

    def _score_momentum(self, ind: IndicatorResult, direction: str) -> float:
        score = 0.0
        if direction == "long":
            if 50 <= ind.rsi <= 68: score += 30
            elif 40 <= ind.rsi < 50: score += 15  # pullback zone
            elif ind.rsi > 70: score += 5          # overbought
            if ind.cci > 0: score += 15
            if ind.stoch_rsi_k > ind.stoch_rsi_d: score += 20
            if ind.change_1h > 0: score += 15
            if ind.change_4h > 0: score += 20
        else:
            if 32 <= ind.rsi <= 50: score += 30
            elif 50 < ind.rsi <= 60: score += 15
            elif ind.rsi < 30: score += 5
            if ind.cci < 0: score += 15
            if ind.stoch_rsi_k < ind.stoch_rsi_d: score += 20
            if ind.change_1h < 0: score += 15
            if ind.change_4h < 0: score += 20
        return min(score, 100.0)

    def _score_volatility(self, ind: IndicatorResult) -> float:
        atr_pct = ind.atr / ind.price * 100 if ind.price else 0
        # Prefer moderate volatility, penalize extremes
        if 0.4 <= atr_pct <= 2.5: return 100.0
        if 0.25 <= atr_pct < 0.4 or 2.5 < atr_pct <= 4.0: return 65.0
        if atr_pct < 0.25 or atr_pct > 6.0: return 15.0
        return 40.0

    def _score_volume(self, ind: IndicatorResult) -> float:
        vr = ind.volume_relative
        if vr >= 2.0: return 100.0
        if vr >= 1.5: return 80.0
        if vr >= 1.2: return 55.0
        if vr >= 1.0: return 35.0
        return 15.0

    def _score_pattern(self, ind: IndicatorResult, direction: str) -> float:
        score = 0.0
        if direction == "long":
            if ind.breakout_up: score += 35
            if ind.pullback and ind.trend_continuation: score += 30
            if ind.liquidity_sweep and ind.price > ind.swing_low: score += 20
        else:
            if ind.breakout_down: score += 35
            if ind.liquidity_sweep and ind.price < ind.swing_high: score += 20
            if not ind.trend_continuation: score += 15
        if ind.false_breakout: score -= 15  # penalty rather than reward
        return max(0.0, min(score, 100.0))

    def _score_multi_tf(self, ind, tf1h, tf4h, tfd) -> Tuple[float, float]:
        trend_15m = ind.ema20 > ind.ema50
        aligns, total = 0, 0
        for tf in (tf1h, tf4h, tfd):
            if tf is None: continue
            total += 1
            if trend_15m == (tf.ema20 > tf.ema50): aligns += 1
        if total == 0: return 50.0, 0.5
        ratio = aligns / total
        return ratio * 100, ratio

    def _score_risk_reward(self, ind: IndicatorResult) -> float:
        rr = ind.risk_reward
        if rr >= 3.0: return 100.0
        if rr >= 2.5: return 85.0
        if rr >= 2.0: return 70.0
        if rr >= 1.5: return 50.0
        if rr >= 1.0: return 25.0
        return 5.0


def score_signal(mtf: MultiTimeframeIndicators) -> float:
    s = SignalFilter().evaluate(mtf)
    return s.total if s else 0.0


def get_top_candidates(
    mtf_list: List[MultiTimeframeIndicators],
    top_n: int = 5,
    require_gates: bool = True,
) -> List[Tuple[MultiTimeframeIndicators, SignalScore]]:
    f = SignalFilter()
    scored = []
    for mtf in mtf_list:
        s = f.evaluate(mtf)
        if not s: continue
        if s.total < f.MIN_SCORE: continue
        if require_gates and not s.passed_gates: continue
        scored.append((mtf, s))
    scored.sort(key=lambda x: x[1].total, reverse=True)
    return scored[:top_n]