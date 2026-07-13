"""Post text builder — direction-aware, uses IndicatorResult + SignalScore.

Exposes:
  generate_post_with_memory(symbol, basic, mtf, score, memory, levels) -> str
  _levels(ind, direction) -> dict with entry/tp1/tp2/tp3/stop
"""
from __future__ import annotations
import logging
import random
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.4f}".rstrip("0").rstrip(".")
    if p >= 0.01:
        return f"{p:.5f}".rstrip("0").rstrip(".")
    return f"{p:.8f}".rstrip("0").rstrip(".")


def _levels(ind, direction: str) -> Dict[str, float]:
    """Compute entry/TP1-3/stop from IndicatorResult and direction."""
    price = float(ind.price)
    atr = float(ind.atr) if ind.atr else price * 0.01

    if direction == "long":
        entry = price
        stop = min(entry - atr * 1.5, float(ind.support) if ind.support else entry - atr * 1.5)
        risk = max(entry - stop, atr)
        tp1 = entry + risk * 1.5
        tp2 = entry + risk * 2.5
        tp3 = entry + risk * 4.0
        # Cap TP3 at resistance if it's far above
        if ind.resistance and ind.resistance > entry:
            tp3 = min(tp3, float(ind.resistance) * 1.02)
    else:
        entry = price
        stop = max(entry + atr * 1.5, float(ind.resistance) if ind.resistance else entry + atr * 1.5)
        risk = max(stop - entry, atr)
        tp1 = entry - risk * 1.5
        tp2 = entry - risk * 2.5
        tp3 = entry - risk * 4.0
        if ind.support and ind.support < entry:
            tp3 = max(tp3, float(ind.support) * 0.98)

    return {"entry": entry, "tp1": tp1, "tp2": tp2, "tp3": tp3, "stop": stop}


_LONG_HEADS = [
    "🚀 {basic}/USDT — сетап на лонг",
    "📈 {basic}/USDT: бычий сигнал",
    "🟢 {basic}/USDT — покупатели активизировались",
]
_SHORT_HEADS = [
    "🔻 {basic}/USDT — сетап на шорт",
    "📉 {basic}/USDT: медвежий сигнал",
    "🔴 {basic}/USDT — давление продавцов",
]


def generate_post_with_memory(*, symbol: str, basic: str, mtf, score,
                              memory=None, levels: Optional[Dict[str, float]] = None) -> str:
    """Assemble a Binance Square-style post."""
    ind = mtf.tf_15m
    direction = score.direction
    lv = levels or _levels(ind, direction)

    # Avoid re-using the exact same headline as one of the last N posts
    recent = memory.recent_texts(6) if memory else []
    pool = _LONG_HEADS if direction == "long" else _SHORT_HEADS
    heads = [h for h in pool if not any(h.format(basic=basic) in t for t in recent)] or pool
    head = random.choice(heads).format(basic=basic)

    trend_word = "восходящий" if ind.ema20 > ind.ema50 else "нисходящий"
    rsi_state = (
        "перекуплен" if ind.rsi > 70 else
        "перепродан" if ind.rsi < 30 else
        f"нейтральный ({ind.rsi:.0f})"
    )
    vol_word = "высокий" if ind.volume_relative >= 1.5 else "средний" if ind.volume_relative >= 1.0 else "низкий"

    lines = [
        head,
        "",
        f"📊 Тренд {trend_word}, ADX {ind.adx:.0f}, RSI {rsi_state}",
        f"🔊 Объём {vol_word} (x{ind.volume_relative:.2f})",
        f"🎯 Скор: {score.total:.0f}/100 · R/R {ind.risk_reward:.1f}",
        "",
        f"Вход: {_fmt_price(lv['entry'])}",
        f"TP1: {_fmt_price(lv['tp1'])}",
        f"TP2: {_fmt_price(lv['tp2'])}",
        f"TP3: {_fmt_price(lv['tp3'])}",
        f"Стоп: {_fmt_price(lv['stop'])}",
        "",
        "Не финансовая рекомендация. DYOR.",
        f"#{basic} #Crypto #TradingSignals",
    ]
    return "\n".join(lines)
