"""Main orchestration for the crypto signal bot.

v3 pipeline:
  1. Load trending USDT pairs (top by volume × movement)
  2. Pull BTC market context; if strongly biased, filter alt directions
  3. For each candidate: multi-TF data (15m/1h/4h/1d) → indicators → score
  4. Apply hard gates and pick the best signal
  5. Optional funding-rate sanity check
  6. Generate direction-aware post (Mistral polish) + chart with SL/TP zones
  7. Publish and record to memory/history
"""
from __future__ import annotations
import os
import logging

from data import get_data
from indicators import calculate_multi_timeframe
from filters import get_top_candidates
from writer import generate_post_with_memory, _levels
from publisher import publish
from trend import get_trending_symbols, get_base_asset
from history import get_recently_published, add_published, cleanup_history
from chart import generate_chart
from memory import PostMemory
from quality import PostQualityEvaluator
from btc_context import get_btc_context, is_direction_compatible, get_funding_rate

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("bot")


TIMEFRAMES = ("15m", "1h", "4h", "1d")
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "180"))
TOP_SYMBOLS = int(os.getenv("TOP_SYMBOLS", "80"))
MAX_FUNDING_ABS = float(os.getenv("MAX_FUNDING_ABS", "0.001"))  # 0.10%


def _analyze_symbol(sym: str):
    dfs = {}
    for tf in TIMEFRAMES:
        df = get_data(sym, interval=tf, limit=250)
        if df is not None:
            dfs[tf] = df
    if "15m" not in dfs:
        return None
    return calculate_multi_timeframe(sym, dfs)


def main() -> int:
    cleanup_history()

    symbols = get_trending_symbols(limit=TOP_SYMBOLS)
    if not symbols:
        logger.error("No trending symbols found")
        return 1

    recent = get_recently_published(minutes=COOLDOWN_MIN)
    symbols = [s for s in symbols if s not in recent]
    logger.info(f"Symbols after cooldown filter: {len(symbols)}")
    if not symbols:
        return 0

    btc = get_btc_context()
    if btc:
        logger.info(f"BTC bias: {btc.bias} "
                    f"(1h {btc.change_1h:+.2f}%, 4h {btc.change_4h:+.2f}%, "
                    f"24h {btc.change_24h:+.2f}%)")

    candidates = []
    for sym in symbols:
        try:
            mtf = _analyze_symbol(sym)
            if mtf and mtf.tf_15m is not None:
                candidates.append(mtf)
        except Exception as e:
            logger.warning(f"analyze {sym} failed: {e}")

    logger.info(f"Analyzed candidates: {len(candidates)}")

    # Rank; take a wider slice so we can filter by BTC context / funding
    top = get_top_candidates(candidates, top_n=8, require_gates=True)
    if not top:
        logger.info("Nothing passed the gates")
        return 0

    chosen = None
    for mtf, sc in top:
        if btc and not is_direction_compatible(sc.direction, btc):
            logger.info(f"skip {mtf.symbol}: direction {sc.direction} "
                        f"vs BTC {btc.bias}")
            continue
        funding = get_funding_rate(mtf.symbol)
        if funding is not None and abs(funding) > MAX_FUNDING_ABS:
            # Extreme funding: skip trades in the crowded direction
            if (funding > 0 and sc.direction == "long") or \
               (funding < 0 and sc.direction == "short"):
                logger.info(f"skip {mtf.symbol}: crowded funding {funding*100:.3f}%")
                continue
        chosen = (mtf, sc)
        break

    if not chosen:
        chosen = top[0]  # fall back — better than publishing nothing
        logger.info(f"fallback pick: {chosen[0].symbol}")

    best_mtf, best_score = chosen
    symbol = best_mtf.symbol
    basic = get_base_asset(symbol)
    logger.info(
        f"BEST {symbol} score={best_score.total:.1f} dir={best_score.direction} "
        f"trend={best_score.trend:.0f} mom={best_score.momentum:.0f} "
        f"vol={best_score.volume:.0f} mtf={best_score.multi_tf:.0f} "
        f"rr={best_score.risk_reward:.0f}"
    )

    ind = best_mtf.tf_15m
    lv = _levels(ind, best_score.direction)

    memory = PostMemory()
    post_text = generate_post_with_memory(
        symbol=symbol, basic=basic, mtf=best_mtf,
        score=best_score, memory=memory, levels=lv,
    )

    quality = PostQualityEvaluator().evaluate(post_text)
    logger.info(f"Post quality: {quality:.1f}")

    raw = get_data(symbol, interval="15m", limit=250)
    chart_path = generate_chart(
        symbol, raw, basic,
        entry=lv["entry"], tp1=lv["tp1"], tp2=lv["tp2"], tp3=lv["tp3"],
        stop=lv["stop"], direction=best_score.direction,
        support=ind.support, resistance=ind.resistance,
    )
    if chart_path:
        logger.info(f"Chart: {chart_path}")

    ok = publish(post_text, image_path=chart_path)
    if ok:
        add_published(symbol)
        memory.add_post(symbol, post_text)
        logger.info(f"Published {symbol}")
    else:
        logger.error("Publication failed")

    if chart_path and os.path.exists(chart_path):
        try: os.remove(chart_path)
        except OSError: pass

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())