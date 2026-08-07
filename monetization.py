"""Write-to-Earn oriented ranking helpers.

This module does not try to infer Binance's private recommendation algorithm.
It ranks observable proxies that are useful for W2E: live attention, actual
trading activity, a clickable cashtag and a clear decision condition.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Dict


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
    """Score how useful a market is as a W2E topic right now.

    v5 over-weighted static 24h liquidity/rank. A coin could therefore be moving
    +3% in 15 minutes on abnormal volume and still lose to a quieter large market.
    v6 treats fresh attention as the largest component while keeping liquidity
    and activity as sanity checks rather than hard popularity requirements.
    """
    size = max(1, int(trend_universe_size))
    rank = max(1, min(int(trend_rank), size))
    rank_score = _clamp((1.0 - (rank - 1) / max(1, size - 1)) * 100.0)

    # Wider calibration than v5: $1M -> 0, ~$5B -> 100. Smaller active alts no
    # longer receive a near-zero score purely because they are not BTC/ETH sized.
    liquidity = _log_score(float(quote_volume_24h), 6.0, 9.7)
    activity = _log_score(float(trade_count_24h), 2.7, 6.7)

    move = min(abs(float(abs_change_24h)), 30.0)
    movement = _clamp(move / 9.0 * 100.0)
    if move > 22.0:
        movement -= min(18.0, (move - 22.0) * 1.5)
    movement = _clamp(movement)

    freshness = _clamp(
        float(attention_score) * 0.72
        + min(abs(float(change_15m)) * 32.0, 18.0)
        + min(max(float(volume_spike) - 1.0, 0.0) * 7.0, 14.0)
    )

    rr = max(0.0, float(risk_reward))
    actionability = 48.0 + min(max(rr - 1.0, 0.0) * 20.0, 32.0)
    if rr < 1.1:
        actionability -= 20.0
    # Overextension is bad for chasing a trade, but still valuable as content if
    # the post explicitly tells the reader to wait for a retest. Keep the penalty
    # moderate and let the writer/attention gate decide the angle.
    if overextended:
        actionability -= 12.0
    actionability = _clamp(actionability)

    score = (
        rank_score * 0.11
        + liquidity * 0.17
        + activity * 0.10
        + movement * 0.10
        + freshness * 0.36
        + actionability * 0.16
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
    """Evaluate a useful, non-pushy click-to-market journey."""

    ACTION_MARKERS = (
        "если ", "пока ", "жду", "уров", "удерж", "закреп", "ретест",
        "отмен", "вход", "стоп", "сценар", "пропуска", "не открываю",
        "не догон", "подтвержд", "цель", "зона интереса",
    )
    DECISION_MARKERS = (
        "для меня", "я бы", "я сейчас", "смотрю", "не спеш", "не догон",
        "жду", "пропуска", "не хочу", "закрываю",
    )
    SPAM_MARKERS = (
        "100%", "гарант", "без риска", "точно даст", "легкие деньги",
        "срочно покуп", "срочно прода", "заходи сейчас", "иксы гарант",
    )
    ROBOTIC_MARKERS = (
        "направление у идеи", "граница ошибки", "диапазон контроля",
        "параметры сценария", "карта исполнения", "правило исполнения",
    )

    def report(self, text: str, basic: str) -> ConversionIntentReport:
        clean = text.strip()
        lowered = clean.lower().replace("ё", "е")
        ticker = "$" + re.sub(r"[^A-Za-z0-9]", "", str(basic)).upper()
        ticker_matches = list(re.finditer(re.escape(ticker), clean, flags=re.IGNORECASE))

        discoverability = 15.0
        if ticker_matches:
            discoverability += 50.0
            first_pos = ticker_matches[0].start()
            if first_pos <= 120:
                discoverability += 25.0
            elif first_pos <= 220:
                discoverability += 10.0
            if 1 <= len(ticker_matches) <= 2:
                discoverability += 10.0
            elif len(ticker_matches) > 3:
                discoverability -= 20.0
        discoverability = _clamp(discoverability)

        actionability = 30.0
        actionability += min(55.0, sum(1 for x in self.ACTION_MARKERS if x in lowered) * 6.5)
        # Reward a complete compact plan without requiring terminal labels.
        if any(x in lowered for x in ("первая цель", "зона интереса")):
            actionability += 8.0
        if any(x in lowered for x in (
            "отменяется", "закрываю", "не актуален", "для меня закрыт", "ломает идею"
        )):
            actionability += 10.0
        actionability = _clamp(actionability)

        decision_context = 28.0 + min(
            60.0,
            sum(1 for x in self.DECISION_MARKERS if x in lowered) * 9.0,
        )
        decision_context = _clamp(decision_context)

        trust = 96.0
        if any(marker in lowered for marker in self.SPAM_MARKERS):
            trust -= 70.0
        if any(marker in lowered for marker in self.ROBOTIC_MARKERS):
            trust -= 24.0
        if clean.count("!") >= 3:
            trust -= 15.0
        if len(re.findall(r"\b(?:LONG|SHORT)\b", clean, flags=re.IGNORECASE)) >= 3:
            trust -= 12.0
        trust = _clamp(trust)

        readability = 100.0
        if len(clean) > 600:
            readability -= min(45.0, (len(clean) - 600) / 4.0)
        label_hits = sum(lowered.count(x) for x in (
            "вход:", "цели:", "стоп-лосс:", "r/r:", "направление:",
        ))
        readability -= min(35.0, label_hits * 8.0)
        number_count = len(re.findall(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?%?", clean))
        if number_count > 8:
            readability -= min(30.0, (number_count - 8) * 5.0)
        readability = _clamp(readability)

        score = (
            discoverability * 0.28
            + actionability * 0.30
            + decision_context * 0.18
            + trust * 0.16
            + readability * 0.08
        )
        return ConversionIntentReport(
            score=round(_clamp(score), 2),
            components={
                "discoverability": round(discoverability, 2),
                "actionability": round(actionability, 2),
                "decision_context": round(decision_context, 2),
                "trust": round(trust, 2),
                "readability": round(readability, 2),
            },
        )
