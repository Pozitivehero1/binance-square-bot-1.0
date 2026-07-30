"""Main orchestration for the Binance Square technical-setup bot.

Pipeline:
1. Rank liquid/trending USDT pairs.
2. Fetch 15m and 1h data for a broad universe.
3. Fetch 4h and 1d only for a smaller preliminary shortlist.
4. Apply direction-aware scoring, BTC context, funding and hard safety gates.
5. Generate several complete post variants and keep the best valid one.
6. Render a card and chart, publish, then update persistent memory/history.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from btc_context import get_btc_context, get_funding_rate, is_direction_compatible
from card import generate_card
from chart import generate_chart
from data import get_data
from filters import SignalFilter, SignalScore, get_top_candidates
from history import add_published, cleanup_history, get_recently_published
from indicators import MultiTimeframeIndicators, calculate_multi_timeframe
from memory import PostMemory
from publisher import publish
from quality import PostQualityEvaluator, QualityReport
from trend import get_base_asset, get_trending_symbols
from content_variation import detect_signal_angles
from writer import GeneratedPost, _levels, generate_post_candidates

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("bot")

PRIMARY_TIMEFRAMES = ("15m", "1h")
CONFIRMATION_TIMEFRAMES = ("4h", "1d")
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "180"))
TOP_SYMBOLS = int(os.getenv("TOP_SYMBOLS", "80"))
SHORTLIST_SIZE = int(os.getenv("SHORTLIST_SIZE", "18"))
FINAL_CANDIDATES = int(os.getenv("FINAL_CANDIDATES", "10"))
DATA_WORKERS = max(1, min(int(os.getenv("DATA_WORKERS", "6")), 12))
KLINE_LIMIT = max(220, min(int(os.getenv("KLINE_LIMIT", "260")), 500))
MAX_FUNDING_ABS = float(os.getenv("MAX_FUNDING_ABS", "0.001"))
ENABLE_BALANCED_FALLBACK = os.getenv("ENABLE_BALANCED_FALLBACK", "0").lower() in {
    "1", "true", "yes"
}
POST_VARIANTS = max(4, min(int(os.getenv("POST_VARIANTS", "8")), 16))
MAX_POST_SIMILARITY = float(os.getenv("MAX_POST_SIMILARITY", "0.56"))
MIN_POST_QUALITY = float(os.getenv("MIN_POST_QUALITY", "72"))
PRELIM_MIN_SCORE = float(os.getenv("PRELIM_MIN_SCORE", "38"))
STRICT_BTC_FILTER = os.getenv("STRICT_BTC_FILTER", "1").lower() in {"1", "true", "yes"}
DRY_RUN = os.getenv("DRY_RUN", "0").lower() in {"1", "true", "yes"}
PUBLISH_IMAGES = os.getenv("PUBLISH_IMAGES", "1").lower() in {"1", "true", "yes"}
PUBLISH_MEDIA_MODE = os.getenv("PUBLISH_MEDIA_MODE", "chart").strip().lower()


def _fetch_symbol_timeframes(symbol: str, intervals: Iterable[str]) -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}
    for interval in intervals:
        frame = get_data(symbol, interval=interval, limit=KLINE_LIMIT)
        if frame is not None:
            frames[interval] = frame
    return frames


def _fetch_many(symbols: List[str], intervals: Iterable[str]) -> Dict[str, Dict[str, pd.DataFrame]]:
    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    if not symbols:
        return result

    with ThreadPoolExecutor(max_workers=DATA_WORKERS, thread_name_prefix="market-data") as executor:
        futures = {
            executor.submit(_fetch_symbol_timeframes, symbol, tuple(intervals)): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                frames = future.result()
                if frames:
                    result[symbol] = frames
            except Exception as exc:
                logger.warning("Data fetch failed for %s: %s", symbol, exc)
    return result


def _preliminary_shortlist(
    primary_data: Dict[str, Dict[str, pd.DataFrame]],
) -> List[str]:
    signal_filter = SignalFilter(min_score=PRELIM_MIN_SCORE)
    scored: List[Tuple[str, float]] = []
    for symbol, frames in primary_data.items():
        if "15m" not in frames or "1h" not in frames:
            continue
        mtf = calculate_multi_timeframe(symbol, frames)
        score = signal_filter.evaluate(mtf)
        if score is not None and score.total >= PRELIM_MIN_SCORE:
            scored.append((symbol, score.total))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in scored[:SHORTLIST_SIZE]]


def _build_full_candidates(
    shortlist: List[str],
    primary_data: Dict[str, Dict[str, pd.DataFrame]],
    confirmation_data: Dict[str, Dict[str, pd.DataFrame]],
) -> List[MultiTimeframeIndicators]:
    candidates: List[MultiTimeframeIndicators] = []
    for symbol in shortlist:
        frames = dict(primary_data.get(symbol, {}))
        frames.update(confirmation_data.get(symbol, {}))
        if "15m" not in frames:
            continue
        mtf = calculate_multi_timeframe(symbol, frames)
        if mtf.tf_15m is not None:
            candidates.append(mtf)
    return candidates


def _choose_market_candidate(
    ranked: List[Tuple[MultiTimeframeIndicators, SignalScore]],
    btc,
    memory: PostMemory,
) -> Optional[Tuple[MultiTimeframeIndicators, SignalScore, Optional[float]]]:
    """Choose a safe candidate while avoiding the same signal archetype every run."""
    frequencies = memory.signal_type_frequency(24)
    last_signal_types = memory.get_last_signal_types(5)
    eligible: List[Tuple[float, MultiTimeframeIndicators, SignalScore, Optional[float], str]] = []

    for mtf, score in ranked:
        if STRICT_BTC_FILTER and btc and not is_direction_compatible(score.direction, btc):
            logger.info(
                "Skip %s: %s setup conflicts with BTC %s bias",
                mtf.symbol,
                score.direction,
                btc.bias,
            )
            continue

        funding = get_funding_rate(mtf.symbol)
        if funding is not None and abs(funding) > MAX_FUNDING_ABS:
            crowded = (funding > 0 and score.direction == "long") or (
                funding < 0 and score.direction == "short"
            )
            if crowded:
                logger.info(
                    "Skip %s: crowded %s funding %.4f%%",
                    mtf.symbol,
                    score.direction,
                    funding * 100.0,
                )
                continue

        angles = detect_signal_angles(mtf.tf_15m, score.direction, mtf)
        # The candidate may support several truthful angles. Prefer the least repeated
        # among its strongest four, rather than always labeling everything continuation.
        best_angle = min(
            angles[:4],
            key=lambda item: (frequencies.get(item.id, 0), -item.weight),
        )
        repeat_count = frequencies.get(best_angle.id, 0)
        immediate_repeat = 1 if best_angle.id in last_signal_types[-2:] else 0
        diversity_penalty = min(14.0, repeat_count * 2.4 + immediate_repeat * 4.5)
        adjusted_score = score.total - diversity_penalty
        eligible.append((adjusted_score, mtf, score, funding, best_angle.id))
        logger.info(
            "Candidate %s raw=%.1f adjusted=%.1f angle=%s repeats=%s",
            mtf.symbol,
            score.total,
            adjusted_score,
            best_angle.id,
            repeat_count,
        )

    if not eligible:
        return None
    eligible.sort(key=lambda item: item[0], reverse=True)
    _, mtf, score, funding, _ = eligible[0]
    return mtf, score, funding


def _text_similarity(left: str, right: str) -> float:
    return PostMemory.compare_texts(left, right)


def _best_post_variant(
    *,
    symbol: str,
    basic: str,
    mtf: MultiTimeframeIndicators,
    score: SignalScore,
    levels: Dict[str, float],
    memory: PostMemory,
    btc,
) -> Optional[Tuple[GeneratedPost, QualityReport]]:
    evaluator = PostQualityEvaluator()
    variants: List[Tuple[GeneratedPost, QualityReport, float]] = []
    generated_texts: List[str] = []
    recent_styles = memory.get_last_post_styles(24)
    recent_signals = memory.get_last_signal_types(24)

    try:
        drafts = generate_post_candidates(
            symbol=symbol,
            basic=basic,
            mtf=mtf,
            score=score,
            memory=memory,
            levels=levels,
            btc=btc,
            variant_count=POST_VARIANTS,
        )
    except Exception as exc:
        logger.error("Post candidate generation failed: %s", exc)
        return None

    for index, draft in enumerate(drafts):
        try:
            report = evaluator.report(
                draft.text,
                basic=basic,
                direction=score.direction,
                levels=levels,
            )
            memory_similarity = memory.similarity_score(draft.text)
            local_similarity = max(
                (_text_similarity(draft.text, other) for other in generated_texts),
                default=0.0,
            )
            generated_texts.append(draft.text)

            similarity_penalty = max(0.0, memory_similarity - 0.28) * 72.0
            similarity_penalty += max(0.0, local_similarity - 0.52) * 25.0

            style_repeats = recent_styles.count(draft.style_id)
            signal_repeats = recent_signals.count(draft.signal_type)
            novelty_penalty = min(7.0, style_repeats * 1.2)
            novelty_penalty += min(7.0, signal_repeats * 1.2)
            if recent_styles[-1:] == [draft.style_id]:
                novelty_penalty += 2.5
            if recent_signals[-1:] == [draft.signal_type]:
                novelty_penalty += 3.0

            # AI-first candidates are shorter and fact-locked. A small bonus prevents
            # the legacy long-form template from winning only because it has more blocks.
            source_bonus = 3.0 if draft.style_id.startswith("ai_") else 0.0
            adjusted_score = report.score - similarity_penalty - novelty_penalty + source_bonus
            logger.info(
                "Post candidate %s: style=%s angle=%s quality=%.1f valid=%s "
                "memory_sim=%.2f local_sim=%.2f adjusted=%.1f",
                index + 1,
                draft.style_id,
                draft.signal_type,
                report.score,
                report.valid,
                memory_similarity,
                local_similarity,
                adjusted_score,
            )

            if report.valid and memory_similarity < MAX_POST_SIMILARITY:
                variants.append((draft, report, adjusted_score))
            elif report.valid:
                logger.info(
                    "Candidate %s rejected as too similar: %.3f >= %.3f",
                    index + 1,
                    memory_similarity,
                    MAX_POST_SIMILARITY,
                )
            else:
                logger.warning(
                    "Candidate %s rejected reasons: %s",
                    index + 1,
                    ", ".join(report.reasons),
                )
        except Exception as exc:
            logger.warning("Post candidate %s failed: %s", index + 1, exc)

    if not variants:
        return None
    variants.sort(key=lambda item: item[2], reverse=True)
    best_draft, best_report, _ = variants[0]
    if best_report.score < MIN_POST_QUALITY:
        logger.info(
            "Best post quality %.1f is below MIN_POST_QUALITY %.1f",
            best_report.score,
            MIN_POST_QUALITY,
        )
        return None
    return best_draft, best_report


def _cleanup_files(paths: Iterable[Optional[str]]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.debug("Temporary file cleanup failed for %s: %s", path, exc)


def main() -> int:
    cleanup_history()
    memory = PostMemory()

    symbols = get_trending_symbols(limit=TOP_SYMBOLS)
    if not symbols:
        logger.error("No trending symbols found")
        return 1

    recent = set(get_recently_published(minutes=COOLDOWN_MIN))
    symbols = [symbol for symbol in symbols if symbol not in recent]
    logger.info("Symbols after cooldown: %s", len(symbols))
    if not symbols:
        return 0

    btc = get_btc_context()
    if btc:
        logger.info(
            "BTC bias=%s, 1h=%+.2f%%, 4h=%+.2f%%, 24h=%+.2f%%",
            btc.bias,
            btc.change_1h,
            btc.change_4h,
            btc.change_24h,
        )

    primary_data = _fetch_many(symbols, PRIMARY_TIMEFRAMES)
    shortlist = _preliminary_shortlist(primary_data)
    logger.info("Preliminary shortlist: %s", ", ".join(shortlist) if shortlist else "empty")
    if not shortlist:
        return 0

    confirmation_data = _fetch_many(shortlist, CONFIRMATION_TIMEFRAMES)
    candidates = _build_full_candidates(shortlist, primary_data, confirmation_data)
    selection_profile = "strict"
    ranked = get_top_candidates(
        candidates,
        top_n=FINAL_CANDIDATES,
        require_gates=True,
        profile="strict",
    )
    if not ranked and ENABLE_BALANCED_FALLBACK:
        logger.info(
            "No candidate passed strict gates; trying balanced gates for near-threshold setups"
        )
        selection_profile = "balanced"
        ranked = get_top_candidates(
            candidates,
            top_n=FINAL_CANDIDATES,
            require_gates=True,
            profile="balanced",
        )
    if not ranked:
        logger.info("No candidate passed strict or balanced signal gates")
        return 0

    logger.info(
        "Signal selection profile: %s (%s candidates)",
        selection_profile,
        len(ranked),
    )
    chosen = _choose_market_candidate(ranked, btc, memory)
    if chosen is None:
        logger.info("All candidates were rejected by BTC/funding safety filters")
        return 0

    best_mtf, best_score, funding = chosen
    symbol = best_mtf.symbol
    basic = get_base_asset(symbol)
    indicator = best_mtf.tf_15m
    if indicator is None:
        return 1

    levels = _levels(indicator, best_score.direction)
    logger.info(
        "BEST %s score=%.1f direction=%s profile=%s trend=%.0f momentum=%.0f volume=%.0f "
        "mtf=%.0f R/R=%.2f funding=%s",
        symbol,
        best_score.total,
        best_score.direction,
        selection_profile,
        best_score.trend,
        best_score.momentum,
        best_score.volume,
        best_score.multi_tf,
        best_score.risk_reward,
        f"{funding * 100:.4f}%" if funding is not None else "n/a",
    )

    generated = _best_post_variant(
        symbol=symbol,
        basic=basic,
        mtf=best_mtf,
        score=best_score,
        levels=levels,
        memory=memory,
        btc=btc,
    )
    if generated is None:
        logger.info("No publication-quality post was generated")
        return 0
    selected_post, quality_report = generated
    post_text = selected_post.text
    logger.info(
        "Selected post quality: %.1f | style=%s | signal=%s",
        quality_report.score,
        selected_post.style_id,
        selected_post.signal_type,
    )
    logger.debug("Post preview:\n%s", post_text)

    card_path: Optional[str] = None
    chart_path: Optional[str] = None
    images: List[str] = []
    try:
        if PUBLISH_IMAGES:
            if PUBLISH_MEDIA_MODE in {"card", "both"}:
                try:
                    card_path = generate_card(
                        basic=basic,
                        direction=best_score.direction,
                        entry=levels["entry"],
                        tp1=levels["tp1"],
                        tp2=levels["tp2"],
                        tp3=levels["tp3"],
                        stop=levels["stop"],
                        rr=levels["risk_reward"],
                        confidence=best_score.total,
                        change_1h=indicator.change_1h,
                        post_style=selected_post.style_id,
                        signal_label=selected_post.angle_title,
                    )
                except Exception as exc:
                    logger.warning("Card generation failed: %s", exc)

            if PUBLISH_MEDIA_MODE in {"chart", "both"}:
                raw_15m = primary_data.get(symbol, {}).get("15m")
                if raw_15m is None:
                    raw_15m = get_data(symbol, interval="15m", limit=KLINE_LIMIT)
                try:
                    chart_path = generate_chart(
                        symbol,
                        raw_15m,
                        basic,
                        entry=levels["entry"],
                        tp1=levels["tp1"],
                        tp2=levels["tp2"],
                        tp3=levels["tp3"],
                        stop=levels["stop"],
                        direction=best_score.direction,
                        support=indicator.support,
                        resistance=indicator.resistance,
                        vol_rel=indicator.volume_relative,
                        indicator=indicator,
                    )
                except Exception as exc:
                    logger.warning("Chart generation failed: %s", exc)

            images = [path for path in (card_path, chart_path) if path and os.path.isfile(path)]

        if DRY_RUN:
            logger.info("DRY_RUN enabled; publication skipped")
            print(post_text)
            return 0

        published = publish(post_text, image_path=images if images else None)
        if not published:
            logger.error("Publication failed")
            return 2

        add_published(symbol)
        memory.add_post(
            symbol,
            post_text,
            post_style=selected_post.style_id,
            signal_type=selected_post.signal_type,
        )
        logger.info("Published %s successfully", symbol)
        return 0
    finally:
        _cleanup_files((card_path, chart_path))


if __name__ == "__main__":
    raise SystemExit(main())
