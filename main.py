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

from runtime import PROJECT_DIR, ProcessLock, load_project_env, setup_logging, write_status

load_project_env()

from btc_context import get_btc_context, get_funding_rate, is_direction_compatible
from attention import AttentionSnapshot, compute_attention
from card import generate_card
from chart import generate_chart
from data import get_data
from filters import SignalFilter, SignalScore, get_top_candidates
from history import add_published, cleanup_history, get_recently_published
from indicators import MultiTimeframeIndicators, calculate_multi_timeframe
from memory import PostMemory
from publisher import publish
from publication_guard import PublicationGuard
from quality import PostQualityEvaluator, QualityReport
from engagement import FeedAppealEvaluator
from monetization import ConversionIntentEvaluator, MarketMonetizationSnapshot, score_market_monetization
from trend import TrendingMarket, get_base_asset, get_trending_market
from content_variation import detect_signal_angles
from writer import GeneratedPost, _levels, generate_post_candidates

logger = setup_logging()

PRIMARY_TIMEFRAMES = ("15m", "1h")
CONFIRMATION_TIMEFRAMES = ("4h", "1d")
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "180"))
TOP_SYMBOLS = int(os.getenv("TOP_SYMBOLS", "80"))
SHORTLIST_SIZE = int(os.getenv("SHORTLIST_SIZE", "18"))
FINAL_CANDIDATES = int(os.getenv("FINAL_CANDIDATES", "10"))
DATA_WORKERS = max(1, min(int(os.getenv("DATA_WORKERS", "6")), 12))
KLINE_LIMIT = max(220, min(int(os.getenv("KLINE_LIMIT", "260")), 500))
MAX_FUNDING_ABS = float(os.getenv("MAX_FUNDING_ABS", "0.001"))
ENABLE_BALANCED_FALLBACK = os.getenv("ENABLE_BALANCED_FALLBACK", "1").lower() in {
    "1", "true", "yes"
}
POST_VARIANTS = max(4, min(int(os.getenv("POST_VARIANTS", "16")), 16))
MAX_POST_SIMILARITY = float(os.getenv("MAX_POST_SIMILARITY", "0.52"))
MIN_POST_QUALITY = float(os.getenv("MIN_POST_QUALITY", "82"))
MIN_FEED_APPEAL = float(os.getenv("MIN_FEED_APPEAL", "74"))
MIN_W2E_MARKET_SCORE = float(os.getenv("MIN_W2E_MARKET_SCORE", "56"))
W2E_SOFT_FLOOR = float(os.getenv("W2E_SOFT_FLOOR", "40"))
HOT_W2E_FLOOR = float(os.getenv("HOT_W2E_FLOOR", "34"))
HOT_TECH_MIN = float(os.getenv("HOT_TECH_MIN", "65"))
HOT_ATTENTION_MIN = float(os.getenv("HOT_ATTENTION_MIN", "82"))
HOT_VOLUME_SPIKE_MIN = float(os.getenv("HOT_VOLUME_SPIKE_MIN", "3"))
HOT_MOVE_15M_MIN = float(os.getenv("HOT_MOVE_15M_MIN", "1.2"))
MIN_CONVERSION_INTENT = float(os.getenv("MIN_CONVERSION_INTENT", "70"))
PRELIM_MIN_SCORE = float(os.getenv("PRELIM_MIN_SCORE", "38"))
STRICT_BTC_FILTER = os.getenv("STRICT_BTC_FILTER", "1").lower() in {"1", "true", "yes"}
DRY_RUN = os.getenv("DRY_RUN", "1").lower() in {"1", "true", "yes"}
PUBLISH_IMAGES = os.getenv("PUBLISH_IMAGES", "1").lower() in {"1", "true", "yes"}
PUBLISH_MEDIA_MODE = os.getenv("PUBLISH_MEDIA_MODE", "adaptive").strip().lower()
if PUBLISH_MEDIA_MODE not in {"adaptive", "card", "chart", "both", "none"}:
    logger.warning("Unknown PUBLISH_MEDIA_MODE=%s; using adaptive", PUBLISH_MEDIA_MODE)
    PUBLISH_MEDIA_MODE = "adaptive"


def _fetch_symbol_timeframes(symbol: str, intervals: Iterable[str]) -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}
    for interval in intervals:
        frame = get_data(symbol, interval=interval, limit=KLINE_LIMIT)
        if frame is None:
            continue
        # Every indicator set uses EMA-50 and other rolling windows. Keeping
        # shorter histories would only create noisy warnings and incomplete setups.
        if len(frame) < 60:
            logger.info(
                "Skip %s %s: only %s closed candles, 60 required",
                symbol,
                interval,
                len(frame),
            )
            continue
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


def _content_opportunity_score(attention: AttentionSnapshot) -> float:
    """Score how strong the current market event is as a Square content opportunity.

    An overextended move can be a poor place to chase price while still being a very
    strong topic for a post: wait for a retest, explain the crowd trap, or show what
    would invalidate the impulse.
    """
    motion = min(abs(attention.change_15m) * 18.0, 25.0)
    volume = min(max(attention.volume_spike - 1.0, 0.0) * 5.0, 25.0)
    range_bonus = min(max(attention.range_expansion - 1.0, 0.0) * 7.0, 10.0)
    extended_story_bonus = (
        8.0
        if (
            attention.overextended
            and attention.volume_spike >= 2.5
            and abs(attention.change_15m) >= 0.8
        )
        else 0.0
    )
    return min(
        100.0,
        attention.score * 0.45
        + motion
        + volume
        + range_bonus
        + extended_story_bonus,
    )


def _w2e_candidate_gate(
    score: SignalScore,
    attention: AttentionSnapshot,
    monetization: MarketMonetizationSnapshot,
) -> Tuple[bool, str]:
    """Allow strong live events without turning the W2E gate into a low-quality bypass."""
    if monetization.score >= MIN_W2E_MARKET_SCORE:
        return True, "standard"

    hot_market = (
        monetization.score >= HOT_W2E_FLOOR
        and score.total >= HOT_TECH_MIN
        and attention.score >= HOT_ATTENTION_MIN
        and attention.volume_spike >= HOT_VOLUME_SPIKE_MIN
        and abs(attention.change_15m) >= HOT_MOVE_15M_MIN
    )
    if hot_market:
        return True, "hot-market override"

    attention_override = (
        monetization.score >= W2E_SOFT_FLOOR
        and score.total >= HOT_TECH_MIN
        and attention.score >= 70.0
        and (
            attention.volume_spike >= 1.8
            or abs(attention.change_15m) >= 0.8
        )
    )
    if attention_override:
        return True, "attention override"

    return False, "below W2E/attention gates"


def _choose_market_candidate(
    ranked: List[Tuple[MultiTimeframeIndicators, SignalScore]],
    btc,
    memory: PostMemory,
    primary_data: Dict[str, Dict[str, pd.DataFrame]],
    market_meta: Dict[str, TrendingMarket],
    market_universe_size: int,
) -> Optional[Tuple[MultiTimeframeIndicators, SignalScore, Optional[float], AttentionSnapshot, MarketMonetizationSnapshot]]:
    """Choose the strongest publishable live event rather than only the best static W2E score."""
    frequencies = memory.signal_type_frequency(24)
    last_signal_types = memory.get_last_signal_types(5)
    eligible: List[
        Tuple[
            float,
            MultiTimeframeIndicators,
            SignalScore,
            Optional[float],
            str,
            AttentionSnapshot,
            MarketMonetizationSnapshot,
        ]
    ] = []

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
        best_angle = min(
            angles[:4],
            key=lambda item: (frequencies.get(item.id, 0), -item.weight),
        )
        repeat_count = frequencies.get(best_angle.id, 0)
        immediate_repeat = 1 if best_angle.id in last_signal_types[-2:] else 0
        diversity_penalty = min(10.0, repeat_count * 1.6 + immediate_repeat * 3.0)

        raw_15m = primary_data.get(mtf.symbol, {}).get("15m")
        attention = compute_attention(raw_15m, mtf.tf_15m, score.direction)
        meta = market_meta.get(mtf.symbol)
        if meta is None:
            monetization = score_market_monetization(
                quote_volume_24h=attention.turnover_1h * 24.0,
                trade_count_24h=0.0,
                abs_change_24h=abs(attention.change_45m) * 8.0,
                trend_rank=market_universe_size,
                trend_universe_size=market_universe_size,
                attention_score=attention.score,
                change_15m=attention.change_15m,
                volume_spike=attention.volume_spike,
                risk_reward=score.risk_reward,
                overextended=attention.overextended,
            )
        else:
            monetization = score_market_monetization(
                quote_volume_24h=meta.quote_volume,
                trade_count_24h=meta.trade_count,
                abs_change_24h=abs(meta.change_pct),
                trend_rank=meta.rank,
                trend_universe_size=market_universe_size,
                attention_score=attention.score,
                change_15m=attention.change_15m,
                volume_spike=attention.volume_spike,
                risk_reward=score.risk_reward,
                overextended=attention.overextended,
            )

        gate_allowed, gate_mode = _w2e_candidate_gate(score, attention, monetization)
        content_opportunity = _content_opportunity_score(attention)

        adjusted_score = (
            score.total * 0.30
            + attention.score * 0.35
            + monetization.score * 0.25
            + content_opportunity * 0.10
            - diversity_penalty
        )

        logger.info(
            "Candidate %s tech=%.1f attention=%.1f w2e=%.1f content=%.1f final=%.1f "
            "gate=%s angle=%s 15m=%+.2f%% vol=x%.2f extended=%s repeats=%s [%s]",
            mtf.symbol,
            score.total,
            attention.score,
            monetization.score,
            content_opportunity,
            adjusted_score,
            gate_mode,
            best_angle.id,
            attention.change_15m,
            attention.volume_spike,
            attention.overextended,
            repeat_count,
            monetization.reason,
        )

        if not gate_allowed:
            logger.info(
                "Skip %s: W2E %.1f < %.1f and no attention override "
                "(tech=%.1f attention=%.1f 15m=%+.2f%% vol=x%.2f)",
                mtf.symbol,
                monetization.score,
                MIN_W2E_MARKET_SCORE,
                score.total,
                attention.score,
                attention.change_15m,
                attention.volume_spike,
            )
            continue

        eligible.append(
            (
                adjusted_score,
                mtf,
                score,
                funding,
                best_angle.id,
                attention,
                monetization,
            )
        )

    if not eligible:
        return None

    eligible.sort(key=lambda item: item[0], reverse=True)
    _, mtf, score, funding, _, attention, monetization = eligible[0]
    return mtf, score, funding, attention, monetization

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
    attention: AttentionSnapshot,
) -> Optional[Tuple[GeneratedPost, QualityReport]]:
    evaluator = PostQualityEvaluator()
    appeal_evaluator = FeedAppealEvaluator()
    conversion_evaluator = ConversionIntentEvaluator()
    variants: List[Tuple[GeneratedPost, QualityReport, float]] = []
    generated_texts: List[str] = []
    recent_styles = memory.get_last_post_styles(24)
    recent_signals = memory.get_last_signal_types(24)
    recent_formats = memory.get_last_content_formats(30)
    recent_visuals = memory.get_last_visual_styles(20)

    try:
        drafts = generate_post_candidates(
            symbol=symbol,
            basic=basic,
            mtf=mtf,
            score=score,
            memory=memory,
            levels=levels,
            btc=btc,
            attention=attention,
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
                content_format=draft.content_format,
                headline=draft.headline,
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
            format_repeats = recent_formats.count(draft.content_format)
            visual_repeats = recent_visuals.count(draft.visual_style)
            novelty_penalty = min(5.0, style_repeats * 0.8)
            novelty_penalty += min(5.0, signal_repeats * 0.9)
            novelty_penalty += min(12.0, format_repeats * 2.0)
            novelty_penalty += min(6.0, visual_repeats * 1.0)
            if recent_formats[-1:] == [draft.content_format]:
                novelty_penalty += 5.0
            if recent_visuals[-1:] == [draft.visual_style]:
                novelty_penalty += 2.5
            if recent_signals[-1:] == [draft.signal_type]:
                novelty_penalty += 2.0

            # Human Feed v6: factual validity stays a hard gate, but the ranking
            # strongly favours compact, readable posts that fit the *current*
            # market state. A hot/extended move should become a retest/caution
            # story, not a dashboard or a blind entry alert.
            editorial_bonus = 2.5 if draft.content_format not in recent_formats[-6:] else 0.0
            context_bonus = 0.0
            if attention.overextended or abs(attention.change_15m) >= 1.0:
                if draft.content_format in {
                    "hot_reaction", "one_problem", "crowd_trap", "why_wait",
                    "contrarian_take", "mistake_to_avoid", "signal_vs_trade",
                }:
                    context_bonus += 9.0
                if draft.content_format in {"setup_plan", "execution_protocol", "data_brief"}:
                    context_bonus -= 9.0
            elif attention.score >= 60:
                if draft.content_format in {"hot_reaction", "chart_story", "level_story"}:
                    context_bonus += 4.0

            if attention.volume_spike >= 2.5 and (
                "за 15 минут" in draft.headline.lower()
                or "объём" in draft.headline.lower()
                or "свеч" in draft.headline.lower()
            ):
                context_bonus += 4.0

            appeal = appeal_evaluator.report(draft.text)
            conversion = conversion_evaluator.report(draft.text, basic)

            human_format_bonus = 0.0
            if draft.content_format in {
                "hot_reaction", "one_problem", "crowd_trap", "chart_story",
                "why_wait", "level_story", "contrarian_take", "mistake_to_avoid",
            }:
                human_format_bonus += 6.0

            # Prefer the 260-540 character sweet spot. Long technical posts can
            # still pass factual checks, but they should not beat a clean feed post.
            length_bonus = 4.0 if 260 <= len(draft.text) <= 540 else 0.0
            if len(draft.text) > 580:
                length_bonus -= min(10.0, (len(draft.text) - 580) / 8.0)

            robotic_penalty = 0.0
            lowered_text = draft.text.lower().replace("ё", "е")
            for phrase in (
                "направление у идеи", "граница ошибки", "диапазон контроля",
                "параметры сценария", "карта исполнения", "правило исполнения",
            ):
                if phrase in lowered_text:
                    robotic_penalty += 12.0

            adjusted_score = (
                report.score * 0.35
                + appeal.score * 0.40
                + conversion.score * 0.25
                - similarity_penalty
                - novelty_penalty
                - robotic_penalty
                + editorial_bonus
                + context_bonus
                + human_format_bonus
                + length_bonus
            )
            logger.info(
                "Post candidate %s: format=%s visual=%s angle=%s quality=%.1f appeal=%.1f conversion=%.1f valid=%s "
                "memory_sim=%.2f local_sim=%.2f adjusted=%.1f",
                index + 1,
                draft.content_format,
                draft.visual_style,
                draft.signal_type,
                report.score,
                appeal.score,
                conversion.score,
                report.valid,
                memory_similarity,
                local_similarity,
                adjusted_score,
            )

            if (
                report.valid
                and memory_similarity < MAX_POST_SIMILARITY
                and appeal.score >= MIN_FEED_APPEAL
                and conversion.score >= MIN_CONVERSION_INTENT
            ):
                variants.append((draft, report, adjusted_score))
            elif report.valid and conversion.score < MIN_CONVERSION_INTENT:
                logger.info(
                    "Candidate %s rejected for low W2E conversion intent: %.1f < %.1f",
                    index + 1, conversion.score, MIN_CONVERSION_INTENT,
                )
            elif report.valid and appeal.score < MIN_FEED_APPEAL:
                logger.info(
                    "Candidate %s rejected for low feed appeal: %.1f < %.1f",
                    index + 1, appeal.score, MIN_FEED_APPEAL,
                )
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



def _log_near_misses(candidates: List[MultiTimeframeIndicators], limit: int = 3) -> None:
    """Explain the strongest rejected setups without changing publication rules."""
    signal_filter = SignalFilter(profile="strict")
    near = []
    for mtf in candidates:
        score = signal_filter.evaluate(mtf)
        if score is None:
            continue
        near.append((score.total, mtf.symbol, score))
    near.sort(key=lambda item: item[0], reverse=True)
    for total, symbol, score in near[: max(1, limit)]:
        reasons = "; ".join(score.gate_reasons) if score.gate_reasons else "score below threshold"
        logger.info(
            "Near miss %s score=%.1f direction=%s: %s",
            symbol,
            total,
            score.direction,
            reasons,
        )

def _cleanup_files(paths: Iterable[Optional[str]]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.debug("Temporary file cleanup failed for %s: %s", path, exc)


def _run_once() -> int:
    cleanup_history()
    memory = PostMemory()
    guard = PublicationGuard(memory.items)
    logger.info(
        "Run start project=%s cwd=%s dry_run=%s memory=%s",
        PROJECT_DIR,
        os.getcwd(),
        DRY_RUN,
        memory.path,
    )
    if not DRY_RUN:
        pacing = guard.preflight()
        if not pacing.allowed:
            next_time = pacing.next_allowed_at.isoformat() if pacing.next_allowed_at else "n/a"
            logger.info("Cron check skipped: %s; next=%s", pacing.reason, next_time)
            write_status("skipped", pacing.reason, next_allowed_at=next_time)
            return 0
        logger.info("Publication guard: %s", pacing.reason)

    trending_market = get_trending_market(limit=TOP_SYMBOLS)
    if not trending_market:
        logger.error("No trending symbols found")
        return 1
    market_meta = {item.symbol: item for item in trending_market}
    symbols = [item.symbol for item in trending_market]

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
        if ENABLE_BALANCED_FALLBACK:
            logger.info("No candidate passed strict or balanced signal gates")
        else:
            logger.info(
                "No candidate passed strict gates; balanced fallback is disabled"
            )
        _log_near_misses(candidates)
        return 0

    logger.info(
        "Signal selection profile: %s (%s candidates)",
        selection_profile,
        len(ranked),
    )
    chosen = _choose_market_candidate(
        ranked, btc, memory, primary_data, market_meta, max(1, len(trending_market))
    )
    if chosen is None:
        logger.info("All candidates were rejected by BTC/funding/W2E attention gates")
        write_status("skipped", "no candidate passed W2E/attention selection")
        return 0

    best_mtf, best_score, funding, attention, monetization = chosen
    symbol = best_mtf.symbol
    basic = get_base_asset(symbol)
    indicator = best_mtf.tf_15m
    if indicator is None:
        return 1

    levels = _levels(indicator, best_score.direction)
    logger.info(
        "BEST %s tech=%.1f attention=%.1f w2e=%.1f direction=%s profile=%s trend=%.0f momentum=%.0f volume=%.0f "
        "mtf=%.0f R/R=%.2f 15m=%+.2f%% fresh_vol=x%.2f funding=%s",
        symbol,
        best_score.total,
        attention.score,
        monetization.score,
        best_score.direction,
        selection_profile,
        best_score.trend,
        best_score.momentum,
        best_score.volume,
        best_score.multi_tf,
        best_score.risk_reward,
        attention.change_15m,
        attention.volume_spike,
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
        attention=attention,
    )
    if generated is None:
        logger.info("No publication-quality post was generated")
        return 0
    selected_post, quality_report = generated
    post_text = selected_post.text
    logger.info(
        "Selected post quality: %.1f | format=%s | visual=%s | signal=%s",
        quality_report.score,
        selected_post.content_format,
        selected_post.visual_style,
        selected_post.signal_type,
    )
    logger.debug("Post preview:\n%s", post_text)

    reach = guard.evaluate_candidate(
        market_score=best_score.total,
        quality_score=quality_report.score,
        volume_relative=max(indicator.volume_relative, attention.volume_spike),
        change_1h=max(abs(indicator.change_1h), abs(attention.change_15m) * 2.0),
    )
    logger.info("Distribution gate: %s", reach.reason)
    if not DRY_RUN and not reach.allowed:
        write_status(
            "skipped",
            reach.reason,
            symbol=symbol,
            reach_score=reach.score,
            market_score=best_score.total,
            quality_score=quality_report.score,
        )
        return 0

    card_path: Optional[str] = None
    chart_path: Optional[str] = None
    images: List[str] = []
    try:
        if PUBLISH_IMAGES:
            card_visuals = {
                "headline_card", "split_scenario", "risk_card", "journal_card",
                "indicator_card", "data_card", "followup_card", "pulse_card",
            }
            if PUBLISH_MEDIA_MODE == "adaptive":
                human_chart_formats = {
                    "hot_reaction", "one_problem", "crowd_trap", "chart_story",
                    "why_wait", "level_story", "contrarian_take", "mistake_to_avoid",
                    "signal_vs_trade", "two_scenarios", "liquidity_map", "trader_journal",
                }
                if selected_post.content_format in human_chart_formats:
                    effective_media = "chart"
                else:
                    effective_media = "card" if selected_post.visual_style in card_visuals else "chart"
            else:
                effective_media = PUBLISH_MEDIA_MODE

            if effective_media in {"card", "both"}:
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
                        content_format=selected_post.content_format,
                        visual_style=selected_post.visual_style,
                        headline=selected_post.headline,
                        rsi=indicator.rsi,
                        adx=indicator.adx,
                        volume_relative=indicator.volume_relative,
                        change_15m=attention.change_15m,
                        fresh_volume=attention.volume_spike,
                        attention_score=attention.score,
                    )
                except Exception as exc:
                    logger.warning("Card generation failed: %s", exc)

            if effective_media in {"chart", "both"}:
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
                        visual_style=selected_post.visual_style,
                        headline=selected_post.headline,
                        signal_label=selected_post.angle_title,
                    )
                except Exception as exc:
                    logger.warning("Chart generation failed: %s", exc)

            # Adaptive mode publishes one strong thumbnail. "both" remains available
            # for users who explicitly want the card and the chart together.
            images = [path for path in (card_path, chart_path) if path and os.path.isfile(path)]

        if DRY_RUN:
            logger.info("DRY_RUN enabled; publication skipped")
            write_status(
                "dry_run",
                "post generated but not published",
                symbol=symbol,
                reach_score=reach.score,
                quality_score=quality_report.score,
                w2e_market_score=monetization.score,
            )
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
            content_format=selected_post.content_format,
            visual_style=selected_post.visual_style,
            direction=best_score.direction,
            levels=levels,
            market_price=indicator.price,
        )
        guard.record_success(
            symbol=symbol,
            direction=best_score.direction,
            content_format=selected_post.content_format,
            visual_style=selected_post.visual_style,
            market_score=best_score.total,
            quality_score=quality_report.score,
            reach_score=float(reach.score or 0.0),
            post_id=published.post_id,
        )
        write_status(
            "published",
            "publication completed",
            symbol=symbol,
            direction=best_score.direction,
            post_id=published.post_id,
            reach_score=reach.score,
            quality_score=quality_report.score,
            w2e_market_score=monetization.score,
            content_format=selected_post.content_format,
            visual_style=selected_post.visual_style,
        )
        logger.info("Published %s successfully (post_id=%s)", symbol, published.post_id or "n/a")
        return 0
    finally:
        _cleanup_files((card_path, chart_path))


def main() -> int:
    with ProcessLock() as lock:
        if not lock.acquired:
            logger.info("Another bot process is still running; cron launch skipped")
            write_status("skipped", "another bot process is still running")
            return 0
        try:
            return _run_once()
        except Exception as exc:
            logger.exception("Unhandled bot failure")
            write_status("error", str(exc))
            return 3


if __name__ == "__main__":
    raise SystemExit(main())
