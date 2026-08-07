"""Write-to-Earn oriented ranking helpers.

The module does *not* try to predict Binance's private recommendation algorithm.
It optimizes only observable proxies that matter for legitimate W2E conversion:
market attention/liquidity, a useful and actionable setup, natural cashtag placement,
and enough context for a reader to decide whether to inspect the market.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Dict, Optional


@dataclass(frozen=True)
class MarketMonetizationSnapshot:
    score: float
    trend_rank_score: float
    liquidity_score: float
    activity_score: float
    movement_score: float
    freshness_score: float
    actionability_score: float
    reason: str


@dataclass(frozen=True)
class ConversionIntentReport:
    score: float
    components: Dict[str, float]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _log_score(value: float, floor_log: float, ceiling_log: float) -> float:
    if value <= 0:
        return 0.0
    x = math.log10(value)
    if ceiling_log <= floor_log:
        return 0.0
    return _clamp((x - floor_log) / (ceiling_log - floor_log) * 100.0)


def score_market_monetization(
    *,
    quote_volume_24h: float,
    trade_count_24h: float,
    abs_change_24h: float,
    trend_rank: int,
    trend_universe_size: int,
    attention_score: float,
    change_15m: float,
    volume_spike: float,
    risk_reward: float,
    overextended: bool,
) -> MarketMonetizationSnapshot:
    """Score a candidate for plausible W2E conversion, using public market data only.

    High turnover/activity means there are actually traders around the market. Fresh
    attention raises the chance that a reader cares *now*. Actionability rewards a
    usable risk/reward while penalising late, overextended chases.
    """
    size = max(1, int(trend_universe_size))
    rank = max(1, min(int(trend_rank), size))
    rank_score = _clamp((1.0 - (rank - 1) / max(1, size - 1)) * 100.0)

    # $10M -> ~0, $10B -> ~100. This intentionally favours markets with enough fee
    # generating activity without making only BTC/ETH eligible.
    liquidity = _log_score(float(quote_volume_24h), 7.0, 10.0)
    activity = _log_score(float(trade_count_24h), 3.5, 7.0)

    move = min(abs(float(abs_change_24h)), 25.0)
    movement = _clamp(move / 8.0 * 100.0)
    # Extreme 24h moves are interesting but often poor entry points. Cap the bonus.
    if move > 18.0:
        movement -= min(20.0, (move - 18.0) * 2.0)

    freshness = _clamp(
        float(attention_score) * 0.68
        + min(abs(float(change_15m)) * 35.0, 20.0)
        + min(max(float(volume_spike) - 1.0, 0.0) * 8.0, 12.0)
    )

    rr = max(0.0, float(risk_reward))
    actionability = 45.0 + min(max(rr - 1.0, 0.0) * 22.0, 35.0)
    if rr < 1.1:
        actionability -= 20.0
    if overextended:
        actionability -= 22.0
    actionability = _clamp(actionability)

    score = (
        rank_score * 0.18
        + liquidity * 0.24
        + activity * 0.14
        + movement * 0.08
        + freshness * 0.24
        + actionability * 0.12
    )
    score = _clamp(score)
    reason = (
        f"rank={rank}/{size}, liq={liquidity:.0f}, activity={activity:.0f}, "
        f"fresh={freshness:.0f}, actionable={actionability:.0f}"
    )
    return MarketMonetizationSnapshot(
        score=round(score, 2),
        trend_rank_score=round(rank_score, 2),
        liquidity_score=round(liquidity, 2),
        activity_score=round(activity, 2),
        movement_score=round(movement, 2),
        freshness_score=round(freshness, 2),
        actionability_score=round(actionability, 2),
        reason=reason,
    )


class ConversionIntentEvaluator:
    """Evaluate whether a post naturally supports a W2E click-to-trade journey.

    This rewards usefulness and discoverability, not pressure to trade. A high score
    means: the coin is easy to identify/click, the post states a decision condition,
    and the reader can understand what would invalidate the idea.
    """

    ACTION_MARKERS = (
        "если ", "пока ", "жду", "уров", "удерж", "закреп", "отмен",
        "вход", "стоп", "сценар", "пропуска", "не вхожу", "не покупаю",
        "не продаю", "подтвержд",
    )
    DECISION_MARKERS = (
        "для меня", "я бы", "я сейчас", "мой сценар", "моя идея", "смотрю",
        "не спеш", "не догон", "жду", "пропуска",
    )
    SPAM_MARKERS = (
        "100%", "гарант", "без риска", "точно даст", "легкие деньги",
        "срочно покуп", "срочно прода", "заходи сейчас", "иксы гарант",
    )

    def report(self, text: str, basic: str) -> ConversionIntentReport:
        clean = text.strip()
        lowered = clean.lower().replace("ё", "е")
        ticker = "$" + re.sub(r"[^A-Za-z0-9]", "", str(basic)).upper()
        ticker_matches = list(re.finditer(re.escape(ticker), clean, flags=re.IGNORECASE))

        discoverability = 20.0
        if ticker_matches:
            discoverability += 45.0
            first_pos = ticker_matches[0].start()
            if first_pos <= 110:
                discoverability += 25.0
            elif first_pos <= 220:
                discoverability += 10.0
            if 1 <= len(ticker_matches) <= 2:
                discoverability += 10.0
            elif len(ticker_matches) > 3:
                discoverability -= 20.0
        discoverability = _clamp(discoverability)

        actionability = 35.0
        actionability += min(50.0, sum(1 for x in self.ACTION_MARKERS if x in lowered) * 7.0)
        if re.search(r"\b(?:tp1|tp2|tp3|стоп|вход)\b", lowered):
            actionability += 10.0
        actionability = _clamp(actionability)

        decision_context = 30.0 + min(55.0, sum(1 for x in self.DECISION_MARKERS if x in lowered) * 9.0)
        decision_context = _clamp(decision_context)

        trust = 92.0
        if any(marker in lowered for marker in self.SPAM_MARKERS):
            trust -= 65.0
        if clean.count("!") >= 4:
            trust -= 15.0
        if len(re.findall(r"\b(?:LONG|SHORT)\b", clean, flags=re.IGNORECASE)) >= 4:
            trust -= 12.0
        trust = _clamp(trust)

        score = discoverability * 0.32 + actionability * 0.30 + decision_context * 0.20 + trust * 0.18
        return ConversionIntentReport(
            score=round(_clamp(score), 2),
            components={
                "discoverability": round(discoverability, 2),
                "actionability": round(actionability, 2),
                "decision_context": round(decision_context, 2),
                "trust": round(trust, 2),
            },
        )
