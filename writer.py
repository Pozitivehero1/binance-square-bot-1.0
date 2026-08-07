"""Fact-locked, human-first Binance Square post generator.

Market selection lives outside this module. The writer's job is to turn a valid
setup into something a real person would stop and read in a feed:

* one clear idea per post;
* 2-4 useful numbers instead of an indicator dump;
* cashtag early enough to be clickable;
* a natural condition for entry and a natural invalidation;
* no forced CTA, no canned "Направление:" / "Граница ошибки:" language;
* optional Mistral copy only for number-free connective prose.

All prices, percentages, directions and market facts remain code-controlled.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from dotenv import load_dotenv

from attention import AttentionSnapshot, format_turnover
from content_strategy import (
    FORMAT_BY_ID,
    author_note,
    author_principle,
    choose_formats,
    choose_headline,
    headline_candidates,
    question_forbidden,
    question_required,
    visual_style_for,
)
from content_variation import SignalAngle, detect_signal_angles
from indicators import build_trade_levels
from memory import PostMemory

load_dotenv()
logger = logging.getLogger(__name__)

POST_MAX_CHARS = int(os.getenv("POST_MAX_CHARS", "620"))
POST_MIN_CHARS = int(os.getenv("POST_MIN_CHARS", "160"))
AI_VARIANTS = max(2, min(int(os.getenv("AI_VARIANTS", "4")), 8))
AI_TIMEOUT = max(10, min(int(os.getenv("AI_TIMEOUT", "50")), 120))
AI_TEMPERATURE = max(0.0, min(float(os.getenv("AI_TEMPERATURE", "0.52")), 0.72))

# Kept for compatibility with tests/imports. Human Feed v6 intentionally does
# not force terminal-like full trade-plan blocks into normal feed posts.
FULL_PLAN_FORMATS: set[str] = set()


@dataclass(frozen=True)
class GeneratedPost:
    text: str
    style_id: str
    signal_type: str
    angle_title: str
    content_format: str = "hot_reaction"
    visual_style: str = "clean_chart"
    headline: str = ""
    question_mode: str = "optional"


def _fmt_price(value: float) -> str:
    price = float(value)
    absolute = abs(price)
    if absolute >= 1000:
        return f"{price:.2f}"
    if absolute >= 1:
        return f"{price:.4f}".rstrip("0").rstrip(".")
    if absolute >= 0.01:
        return f"{price:.6f}".rstrip("0").rstrip(".")
    return f"{price:.10f}".rstrip("0").rstrip(".")


def _fmt_pct(value: float) -> str:
    value = float(value)
    if abs(value) >= 1.0:
        return f"{value:+.1f}%"
    return f"{value:+.2f}%"


def _fmt_x(value: float) -> str:
    value = max(0.0, float(value))
    if value >= 10:
        return f"x{value:.1f}"
    return f"x{value:.2f}"


def _fmt_x_human(value: float) -> str:
    """Russian-feed rendering of an x-multiple; numeric validation normalizes commas."""
    return _fmt_x(value).replace(".", ",")


def _levels(ind, direction: str) -> Dict[str, float]:
    return build_trade_levels(ind, direction)


def _ticker(basic: str) -> str:
    return "$" + re.sub(r"[^A-Za-z0-9]", "", str(basic)).upper()


def _ticker_count(text: str, basic: str) -> int:
    pattern = rf"(?<![A-Za-z0-9_])\${re.escape(str(basic).upper())}(?![A-Za-z0-9_])"
    return len(re.findall(pattern, text.upper()))


def _higher_tf_alignment(mtf, direction: str) -> Tuple[int, int, str]:
    aligned = 0
    total = 0
    labels: List[str] = []
    for label, item in (
        ("1H", getattr(mtf, "tf_1h", None)),
        ("4H", getattr(mtf, "tf_4h", None)),
        ("1D", getattr(mtf, "tf_1d", None)),
    ):
        if item is None:
            continue
        total += 1
        supports = item.ema20 > item.ema50 if direction == "long" else item.ema20 < item.ema50
        aligned += int(supports)
        labels.append(f"{label} {'за' if supports else 'против'}")
    return aligned, total, ", ".join(labels) if labels else "старшие ТФ недоступны"


def _risk_reward_percentages(levels: Dict[str, float]) -> Tuple[float, float]:
    entry = max(abs(float(levels["entry"])), 1e-12)
    risk = abs(float(levels["entry"]) - float(levels["stop"])) / entry * 100.0
    reward = abs(float(levels["tp1"]) - float(levels["entry"])) / entry * 100.0
    return risk, reward


def _angle_copy(angle: SignalAngle, ind, direction: str) -> Dict[str, str]:
    long_side = direction == "long"
    key_level = float(ind.resistance if long_side else ind.support)
    hooks = {
        "breakout": "Пробой уже есть, теперь важнее увидеть, способен ли рынок удержать его.",
        "liquidity_reclaim": "Цена вернулась после снятия ликвидности, поэтому решает качество удержания.",
        "pullback": "Импульс уже был; хороший вход теперь зависит от отката к структуре.",
        "trend_continuation": "Структура поддерживает продолжение, пока цена не теряет опорный уровень.",
        "volume_impulse": "Объём подтвердил интерес, но поздний вход от этого безопаснее не становится.",
        "mtf_alignment": "Старшие таймфреймы помогают направлению, но локальный вход всё равно должен подтвердиться.",
        "vwap_control": "Положение относительно VWAP даёт контекст, но не заменяет реакцию цены.",
        "range_edge": "Цена у границы диапазона, где сценарий должен быстро подтвердиться или сломаться.",
        "momentum": "Импульс есть, но я не хочу превращать сильный сигнал в поздний вход.",
        "volatility_expansion": "Волатильность выросла, поэтому цена ошибки сейчас особенно важна.",
        "trend_structure": "Структура задаёт направление, а уровень решает, есть ли сделка.",
    }
    return {
        "thesis": hooks.get(angle.id, hooks["trend_structure"]),
        "key_level": _fmt_price(key_level),
    }


def _fact_catalog(
    ind,
    mtf,
    direction: str,
    btc,
    angle: SignalAngle,
    levels: Dict[str, float],
    attention: Optional[AttentionSnapshot] = None,
) -> Dict[str, str]:
    aligned, total, tf_detail = _higher_tf_alignment(mtf, direction)
    relation = "выше" if ind.price >= ind.vwap else "ниже"
    ema_relation = "выше" if ind.ema20 >= ind.ema50 else "ниже"
    risk_pct, reward_pct = _risk_reward_percentages(levels)
    facts = {
        "setup": angle.title,
        "trend": f"На 15M EMA20 {ema_relation} EMA50.",
        "momentum": f"RSI {ind.rsi:.1f}, ADX {ind.adx:.1f}.",
        "volume": f"Относительный объём {_fmt_x(ind.volume_relative)}.",
        "vwap": f"Цена {relation} VWAP {_fmt_price(ind.vwap)}.",
        "changes": f"За 1H {ind.change_1h:+.2f}%, за 4H {ind.change_4h:+.2f}%.",
        "range": f"Поддержка {_fmt_price(ind.support)}, сопротивление {_fmt_price(ind.resistance)}.",
        "mtf": f"Старшие ТФ: {aligned}/{total} за направление ({tf_detail})." if total else "Старшие ТФ недоступны.",
        "risk_math": f"До стопа {risk_pct:.2f}%, до первой цели {reward_pct:.2f}%.",
        "target": f"Первая цель {_fmt_price(levels['tp1'])}, стоп {_fmt_price(levels['stop'])}.",
    }
    if attention is not None:
        facts["fresh_move"] = f"За 15 минут {_fmt_pct(attention.change_15m)}, за 45 минут {_fmt_pct(attention.change_45m)}."
        facts["fresh_volume"] = f"Последняя 15-минутная свеча по объёму {_fmt_x(attention.volume_spike)} к медиане последних 20."
        facts["turnover"] = f"Оборот за последний час около {format_turnover(attention.turnover_1h)}."
        facts["attention"] = attention.label
    if btc is not None:
        bias = {"bullish": "бычий", "bearish": "медвежий", "neutral": "нейтральный"}.get(
            str(btc.bias).lower(), str(btc.bias)
        )
        facts["btc"] = f"BTC-фон {bias}: 1H {btc.change_1h:+.2f}%, 4H {btc.change_4h:+.2f}%."
    return facts


_QUESTIONS = (
    "Вы бы здесь ждали ретест или просто пропустили движение?",
    "После такой свечи вы бы ждали откат или уже не трогали монету?",
    "Для вас здесь уже есть вход или подтверждения пока мало?",
    "Вы бы открывали сделку только после реакции на уровне?",
)


def _question(format_id: str, variant_index: int, used: Iterable[str]) -> str:
    if question_forbidden(format_id):
        return ""
    # Only about one fifth of deterministic variants contain a CTA. A real feed
    # should not end every post with a focus-group question.
    if variant_index % 5 != 0:
        return ""
    normalized_used = {PostMemory.normalize_text(item) for item in used if item}
    available = [item for item in _QUESTIONS if PostMemory.normalize_text(item) not in normalized_used]
    pool = available or list(_QUESTIONS)
    return pool[variant_index % len(pool)]


def _tags(angle: SignalAngle, direction: str, variant_index: int) -> str:
    del direction
    # Cashtag already makes the asset clickable. Hashtags are used sparingly so
    # the feed does not look auto-generated.
    if variant_index % 6 != 1:
        return ""
    return {
        "breakout": "#PriceAction",
        "liquidity_reclaim": "#Liquidity",
        "pullback": "#PriceAction",
        "trend_continuation": "#MarketStructure",
        "volume_impulse": "#VolumeAnalysis",
        "mtf_alignment": "#TechnicalAnalysis",
        "vwap_control": "#VWAP",
        "range_edge": "#PriceAction",
        "momentum": "#Momentum",
        "volatility_expansion": "#RiskManagement",
    }.get(angle.id, "#PriceAction")


def _default_fact_ids(format_id: str, facts: Dict[str, str], variant_index: int = 0) -> List[str]:
    mapping = {
        "hot_reaction": ["fresh_move", "fresh_volume"],
        "one_problem": ["fresh_move", "fresh_volume"],
        "crowd_trap": ["fresh_move", "fresh_volume"],
        "chart_story": ["fresh_move", "range"],
        "why_wait": ["fresh_move", "fresh_volume"],
        "level_story": ["fresh_move", "range"],
        "contrarian_take": ["fresh_move", "risk_math"],
        "mistake_to_avoid": ["fresh_move", "fresh_volume"],
        "signal_vs_trade": ["fresh_move", "vwap"],
        "trader_journal": ["fresh_move", "range"],
        "two_scenarios": ["fresh_move", "mtf"],
        "liquidity_map": ["range", "fresh_move"],
        "market_context": ["btc", "fresh_move"],
        "follow_up": ["fresh_move", "range"],
        "risk_memo": ["risk_math", "fresh_move"],
        "indicator_lesson": ["momentum", "fresh_move"],
        "data_brief": ["fresh_move", "fresh_volume"],
        "setup_plan": ["fresh_move", "risk_math"],
        "execution_protocol": ["fresh_move", "risk_math"],
    }
    pool = [item for item in mapping.get(format_id, ["fresh_move", "range"]) if item in facts]
    if not pool:
        return []
    if len(pool) == 1:
        return pool
    return [pool[variant_index % len(pool)]]


def _fact_line(facts: Dict[str, str], ids: Sequence[str]) -> str:
    return " ".join(facts[item] for item in ids if item in facts).strip()


def _movement_sentence(attention: Optional[AttentionSnapshot], variant_index: int) -> str:
    if attention is None:
        return ""
    move15 = _fmt_pct(attention.change_15m)
    move45 = _fmt_pct(attention.change_45m)
    volume = _fmt_x_human(attention.volume_spike)

    if abs(attention.change_15m) >= 1.0:
        variants = [
            f"За 15 минут цена прошла {move15}, а за 45 минут — {move45}.",
            f"Импульс уже заметный: {move15} за 15 минут и {move45} за 45 минут.",
            f"Движение ускорилось до {move15} за последнюю 15-минутную свечу.",
        ]
    else:
        variants = [
            f"За последние 15 минут движение {move15}; за 45 минут — {move45}.",
            f"Пока движение умеренное: {move15} за 15 минут.",
            f"Цена начала двигаться, но за 15 минут это пока {move15}.",
        ]

    text = variants[variant_index % len(variants)]
    if attention.volume_spike >= 5.0:
        text += f" Объём на последней 15-минутной свече — около {volume} от нормы."
    elif attention.volume_spike >= 3.0 and variant_index % 2 == 1:
        text += f" Объём на свече — около {volume} от нормы."
    elif attention.volume_spike >= 1.8 and variant_index % 4 == 3:
        text += f" Объём тоже заметно выше обычного — {volume}."
    return text


def _trade_sentences(
    direction: str,
    key_level: str,
    levels: Dict[str, float],
    attention: Optional[AttentionSnapshot],
    variant_index: int,
) -> Tuple[str, str]:
    side = "LONG" if direction == "long" else "SHORT"
    target = _fmt_price(levels["tp1"])
    stop = _fmt_price(levels["stop"])
    hot = bool(attention and (attention.overextended or abs(attention.change_15m) >= 1.0))

    if direction == "long":
        if hot:
            entries = (
                f"После отката удержат {key_level} — тогда смотрю LONG к {target}.",
                f"Ретест {key_level} выдержат — интерес к LONG сохраняется. Первая цель — {target}.",
                f"Хочу увидеть возврат к {key_level} и покупателей на ретесте. Тогда LONG к {target} имеет смысл.",
            )
        else:
            entries = (
                f"Удержат {key_level} — тогда смотрю LONG к {target}.",
                f"Закрепятся выше {key_level} — первая цель для LONG у {target}.",
                f"Для LONG мне нужна нормальная реакция на {key_level}; дальше смотрю на {target}.",
            )
        invalidations = (
            f"Ниже {stop} сценарий отменяется.",
            f"Вернутся ниже {stop} — LONG для меня закрыт.",
            f"Потеря {stop} ломает идею LONG.",
        )
    else:
        if hot:
            entries = (
                f"После отскока останутся ниже {key_level} — тогда смотрю SHORT к {target}.",
                f"Возврат к {key_level} продавцы удержат — интерес к SHORT сохраняется. Первая цель — {target}.",
                f"Хочу увидеть отскок к {key_level} и отказ идти выше. Тогда SHORT к {target} имеет смысл.",
            )
        else:
            entries = (
                f"Останутся ниже {key_level} — тогда смотрю SHORT к {target}.",
                f"Удержат цену под {key_level} — первая цель для SHORT у {target}.",
                f"Для SHORT мне нужна нормальная реакция под {key_level}; дальше смотрю на {target}.",
            )
        invalidations = (
            f"Выше {stop} сценарий отменяется.",
            f"Вернутся выше {stop} — SHORT для меня закрыт.",
            f"Возврат выше {stop} ломает идею SHORT.",
        )

    return entries[variant_index % len(entries)], invalidations[variant_index % len(invalidations)]


def _natural_observation(
    format_id: str,
    angle_copy: Dict[str, str],
    personal: str,
    attention: Optional[AttentionSnapshot],
    direction: str,
    variant_index: int,
) -> str:
    del variant_index
    hot = bool(attention and (attention.overextended or abs(attention.change_15m) >= 1.0))
    key = angle_copy["key_level"]
    relation = "quiet"
    if attention is not None and abs(attention.change_15m) >= 0.35:
        aligned = (direction == "long" and attention.change_15m > 0) or (
            direction == "short" and attention.change_15m < 0
        )
        relation = "aligned" if aligned else "counter"

    if format_id == "hot_reaction":
        if hot and relation == "counter" and direction == "long":
            return f"После такого снижения я не ловлю дно. Сначала хочу увидеть, как покупатели вернутся у {key}."
        if hot and relation == "counter" and direction == "short":
            return f"После такого роста я не шорчу только из-за большой свечи. Сначала хочу увидеть слабость у {key}."
        if hot:
            return f"После такой свечи мне важнее первый ретест и реакция у {key}, чем попытка успеть по рынку."
        return f"Движение есть, но пока реакция у {key} для меня важнее скорости свечи."

    if format_id == "one_problem":
        if hot and relation == "counter":
            return (
                f"Проблема одна: сильное движение против сценария ещё не означает разворот. "
                f"Без реакции у {key} я ничего не угадываю."
            )
        if hot:
            return (
                f"Проблема одна: после сильного импульса хороший сценарий легко испортить поздним входом. "
                f"Поэтому смотрю на {key}, а не на цвет свечи."
            )
        return f"Проблема одна: без нормальной реакции у {key} направление может быть верным, а вход — всё равно плохим."

    if format_id == "crowd_trap":
        if relation == "counter":
            return (
                "Большая свеча сама по себе не доказывает разворот. "
                f"Я лучше дождусь, пока цена подтвердит это у {key}."
            )
        return (
            "Самая простая ловушка здесь — решить, что сильная свеча обязана продолжиться сразу. "
            f"Я лучше дождусь проверки {key}."
        )

    if format_id == "chart_story":
        return f"Сейчас вся история графика для меня сводится к {key}. {angle_copy['thesis']}"

    if format_id == "why_wait":
        if hot and relation == "counter" and direction == "long":
            return f"Падение заметное, но ловить дно без возврата {key} я не собираюсь."
        if hot and relation == "counter" and direction == "short":
            return f"Рост заметный, но шортить его без слома структуры у {key} я не собираюсь."
        return personal

    if format_id == "level_story":
        return f"Пока цена рядом с {key}, мне не нужен прогноз следующей свечи — нужен ответ рынка на этот уровень."

    if format_id == "contrarian_take":
        if hot and relation == "counter":
            return (
                f"Идти против такого импульса без подтверждения — плохая сделка, даже если направление потом окажется верным. "
                f"Поэтому сначала {key}."
            )
        return (
            "Чем убедительнее выглядит движение, тем строже я отношусь к цене входа. "
            f"Сейчас проверка начинается у {key}."
        )

    if format_id == "mistake_to_avoid":
        if relation == "counter" and hot:
            return "Ошибка здесь — пытаться поймать вершину или дно только потому, что свеча уже стала большой."
        return "Ошибка здесь банальная: увидеть сильный сигнал и решить, что хорошая цена входа уже не важна."

    if format_id == "signal_vs_trade":
        return f"Сигнал уже есть, но для меня это пока наблюдение. Сделкой он станет только после реакции у {key}."
    if format_id == "trader_journal":
        return personal
    if format_id == "two_scenarios":
        return f"Я не хочу угадывать исход у {key}: либо уровень подтвердит идею, либо рынок сам её отменит."
    if format_id == "liquidity_map":
        return f"Вместо прогноза отмечаю {key}: рядом с этой границей станет видно, кто реально контролирует движение."
    if format_id == "market_context":
        return "Локальная картинка может быть сильной, но я не хочу торговать её в отрыве от общего фона."
    if format_id == "follow_up":
        return "После нового движения старую идею не защищаю — уровни пересчитываю по тому, что рынок показывает сейчас."
    if format_id == "risk_memo":
        return "Цель выглядит интересно только пока цена ошибки остаётся заранее понятной."
    if format_id == "indicator_lesson":
        return "Индикаторы помогают увидеть состояние рынка, но точку сделки мне всё равно даёт цена."
    if format_id == "data_brief":
        return f"Для решения мне сейчас достаточно движения цены, реакции у {key} и заранее понятной отмены."
    if format_id == "setup_plan":
        return f"План простой: не угадывать свечу, а дождаться реакции у {key}."
    if format_id == "execution_protocol":
        return "Перед ордером проверяю только то, что реально меняет решение: уровень, подтверждение и цену ошибки."
    return personal


def _render_post(
    *,
    headline: str,
    format_id: str,
    direction: str,
    angle: SignalAngle,
    angle_copy: Dict[str, str],
    facts: Dict[str, str],
    fact_ids: Sequence[str],
    levels: Dict[str, float],
    insight: str,
    personal: str,
    question: str,
    tags: str,
    previous: Optional[dict],
    attention: Optional[AttentionSnapshot],
    variant_index: int = 0,
    ai_copy: bool = False,
) -> str:
    del angle
    key_level = angle_copy["key_level"]
    movement = _movement_sentence(attention, variant_index)
    entry_sentence, invalidation = _trade_sentences(
        direction, key_level, levels, attention, variant_index
    )
    observation = _natural_observation(
        format_id, angle_copy, personal, attention, direction, variant_index
    )
    selected_fact = _fact_line(facts, fact_ids)

    # Slower formats get one extra factual/editorial sentence. Fast feed formats
    # intentionally stay compact.
    extra = ""
    if format_id == "market_context" and "btc" in facts:
        extra = facts["btc"]
    elif format_id == "indicator_lesson":
        extra = facts.get("momentum", "")
    elif format_id == "risk_memo":
        extra = facts.get("risk_math", "")
    elif format_id == "data_brief":
        extra = selected_fact
    elif format_id == "trader_journal" and variant_index % 2 == 0:
        extra = insight
    elif format_id == "follow_up" and previous:
        old_title = str(previous.get("title", "")).strip()
        if old_title:
            extra = f"Прошлый фокус был: «{old_title[:90]}». Сейчас смотрю только на новую структуру."
    elif format_id == "two_scenarios":
        if direction == "long":
            extra = f"План B тоже простой: не удержат {key_level} — LONG не открываю."
        else:
            extra = f"План B тоже простой: вернутся выше {key_level} — SHORT не открываю."
    elif format_id in {"setup_plan", "execution_protocol"}:
        extra = selected_fact

    # In hot posts movement is useful. In quiet/slow formats a selected fact can
    # replace it, which keeps every post from starting with two percentages.
    context_line = movement
    # Do not repeat the same 15m move in the headline and the next paragraph.
    # If the headline already carries the move, use abnormal volume as the one
    # extra fact (when it is genuinely notable) or go straight to the opinion.
    if attention is not None and "за 15 минут" in headline.lower():
        if attention.volume_spike >= 2.2:
            context_line = (
                f"Объём на последней 15-минутной свече — около "
                f"{_fmt_x_human(attention.volume_spike)} от нормы."
            )
        else:
            context_line = ""
    if format_id in {"market_context", "risk_memo", "indicator_lesson"} and selected_fact:
        context_line = selected_fact

    blocks = [headline, context_line, observation, entry_sentence, invalidation]
    if extra and extra not in blocks and extra != context_line:
        blocks.insert(3, extra)

    # AI prose is allowed to replace only the editorial observation. It is
    # number-free and already passed through _safe_ai_text, so price/direction/
    # target/invalidation remain fully code-controlled. Deterministic drafts keep
    # the format-specific observation above.
    if ai_copy and personal and format_id not in {
        "market_context", "risk_memo", "indicator_lesson", "data_brief",
        "setup_plan", "execution_protocol",
    }:
        blocks[2] = personal
    elif personal and format_id == "trader_journal":
        blocks[2] = personal
    if insight and format_id in {"indicator_lesson", "data_brief"}:
        if extra:
            blocks.insert(4, insight)
        else:
            blocks.insert(3, insight)

    if question:
        blocks.append(question)
    if tags:
        blocks.append(tags)

    parts = [str(part).strip() for part in blocks if str(part).strip()]
    text = "\n\n".join(parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


_AI_FORBIDDEN = (
    r"\bновост\w*", r"\бинсайд\w*", r"\bлистинг\w*", r"\bкит\w*",
    r"\bгарант\w*", r"\bточно\s+(?:выраст|упад|пойд)", r"\bбез\s+риска\b",
    r"\bпамп\w*", r"\bдамп\w*", r"\bвероятност[ьи]\s+\d",
)


def _api_key() -> str:
    return (os.getenv("MISTRAL_API") or os.getenv("MISTRAL_API_KEY") or "").strip()


def _content_mode() -> str:
    default = "ai_first" if _api_key() else "deterministic"
    return os.getenv("CONTENT_MODE", default).strip().lower()


def _clean_json(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text.strip())
    start = text.find("{")
    end = text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else text


def _safe_ai_text(value: Any, limit: int, *, question: bool = False) -> str:
    text = re.sub(r"[\r\n]+", " ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text).strip(" -–—:;,.!")[:limit].rstrip(" -–—:;,.!")
    if question and text and not text.endswith("?"):
        text += "?"
    lowered = text.lower().replace("ё", "е")
    if not text or "$" in text or "#" in text or re.search(r"\d", text):
        return ""
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _AI_FORBIDDEN):
        return ""
    return text


def _request_ai_candidates(
    *,
    basic: str,
    direction: str,
    formats: Sequence[str],
    fact_catalog: Dict[str, str],
    recent_titles: Sequence[str],
) -> List[dict]:
    key = _api_key()
    if not key:
        return []
    payload = {
        "task": f"Дай {len(formats)} коротких человеческих редакторских вариантов для технического сетапа {direction.upper()} по {basic.upper()}.",
        "requested_formats_in_order": list(formats),
        "facts": fact_catalog,
        "recent_openings_to_avoid": list(recent_titles[-10:]),
        "rules": {
            "one_candidate_per_requested_format": True,
            "use_only_fact_ids": True,
            "fact_ids_count": "1-2",
            "hook": "одна естественная фраза от первого лица без цифр, тикеров, обещаний, канцелярита и внешних фактов",
            "insight": "одно короткое человеческое объяснение без цифр, терминального языка и новых фактов",
            "question": "необязательный естественный вопрос без цифр; пустая строка предпочтительнее натянутого CTA",
            "style": "русский язык, как живой трейдер в соцсети; коротко; без слов 'направление идеи', 'граница ошибки', 'параметры сценария'",
        },
        "json_shape": {
            "candidates": [{
                "format_id": formats[0] if formats else "hot_reaction",
                "hook": "строка",
                "insight": "строка",
                "question": "",
                "fact_ids": ["fresh_move"],
            }]
        },
    }
    body = {
        "model": os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты редактор трейдерского аккаунта. Используй только переданные факты. "
                    "Не придумывай числа, события, новости, причины движения, статистику успеха или мнения рынка. "
                    "Пиши естественно и коротко. Верни только JSON."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": AI_TEMPERATURE,
        "presence_penalty": 0.20,
        "frequency_penalty": 0.30,
        "max_tokens": 1800,
    }
    response = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=AI_TIMEOUT,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    parsed = json.loads(_clean_json(str(content)))
    candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else []
    return [item for item in candidates if isinstance(item, dict)]


def _parse_ai_candidate(
    raw: dict,
    *,
    allowed_formats: Sequence[str],
    facts: Dict[str, str],
) -> Optional[dict]:
    format_id = str(raw.get("format_id", "")).strip()
    if format_id not in allowed_formats or format_id not in FORMAT_BY_ID:
        return None
    hook = _safe_ai_text(raw.get("hook"), 170)
    insight = _safe_ai_text(raw.get("insight"), 190)
    question = _safe_ai_text(raw.get("question"), 120, question=True) if raw.get("question") else ""
    if not hook or not insight:
        return None
    if question_forbidden(format_id):
        question = ""
    ids: List[str] = []
    for value in raw.get("fact_ids", []) if isinstance(raw.get("fact_ids"), list) else []:
        key = str(value).strip()
        if key in facts and key not in ids:
            ids.append(key)
    if not 1 <= len(ids) <= 2:
        return None
    return {
        "format_id": format_id,
        "hook": hook,
        "insight": insight,
        "question": question,
        "fact_ids": ids,
    }


def _numeric_tokens(text: str) -> List[str]:
    return re.findall(r"(?<![A-Za-zА-Яа-я])[-+]?\d+(?:[.,]\d+)?", text)


def _validate_contract(
    text: str,
    *,
    basic: str,
    direction: str,
    levels: Dict[str, float],
    facts: Dict[str, str],
    headline: str,
    format_id: str,
    key_level: str,
) -> Tuple[bool, Tuple[str, ...]]:
    reasons: List[str] = []
    lowered = text.lower().replace("ё", "е")

    if not 1 <= _ticker_count(text, basic) <= 3:
        reasons.append("ticker count")

    terms = ("LONG", "ЛОНГ") if direction == "long" else ("SHORT", "ШОРТ")
    if not any(term in text.upper() for term in terms):
        reasons.append("missing direction")

    # Every post keeps only the numbers a reader can actually act on: decision
    # level, first target and invalidation. TP2/TP3/RSI/ADX are optional context.
    for label, value in (
        ("key level", key_level),
        ("tp1", _fmt_price(levels["tp1"])),
        ("stop", _fmt_price(levels["stop"])),
    ):
        if value not in text:
            reasons.append(f"missing {label}")

    if not any(marker in lowered for marker in (
        "сценарий отмен",
        "сценарий для меня отмен",
        "идею просто закрываю",
        "больше не актуален",
        "для меня закрыт",
        "ломает идею",
        "не открываю",
        "прохожу мимо",
    )):
        reasons.append("missing natural invalidation")

    q_count = text.count("?")
    if question_forbidden(format_id) and q_count:
        reasons.append("question forbidden")
    if q_count > 1:
        reasons.append("too many questions")

    if len(text) < POST_MIN_CHARS or len(text) > POST_MAX_CHARS:
        reasons.append(f"length {len(text)}")
    if len(re.findall(r"#[A-Za-zА-Яа-я0-9_]+", text)) > 1:
        reasons.append("too many hashtags")
    if text.splitlines()[0].strip() != headline.strip():
        reasons.append("headline mismatch")

    robotic = (
        "направление у идеи",
        "граница ошибки:",
        "диапазон контроля",
        "стоп является технической границей",
        "параметры сценария",
        "карта исполнения",
        "правило исполнения:",
        "факты для выбора:",
        "что вижу сейчас:",
    )
    if any(item in lowered for item in robotic):
        reasons.append("robotic wording")

    for pattern in _AI_FORBIDDEN:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            reasons.append("forbidden claim")
            break

    allowed_source = " ".join([
        headline,
        *facts.values(),
        *(_fmt_price(levels[key]) for key in ("entry", "tp1", "tp2", "tp3", "stop")),
        f"{levels.get('risk_reward', 0):.2f}",
        key_level,
        "15M 45M 1H 4H 1D EMA20 EMA50 RSI ADX TP1 TP2 TP3",
    ])
    allowed = {value.replace(",", ".") for value in _numeric_tokens(allowed_source)}
    unexpected = {value.replace(",", ".") for value in _numeric_tokens(text)} - allowed
    if unexpected:
        reasons.append("unexpected numbers: " + ",".join(sorted(unexpected)))

    return not reasons, tuple(reasons)


def _headline_for(
    *,
    basic: str,
    direction: str,
    format_id: str,
    angle: SignalAngle,
    angle_copy: Dict[str, str],
    ind,
    levels: Dict[str, float],
    recent_titles: Sequence[str],
    index: int,
    attention: Optional[AttentionSnapshot] = None,
) -> str:
    risk_pct, reward_pct = _risk_reward_percentages(levels)
    relation = "выше" if ind.price >= ind.vwap else "ниже"
    candidates = headline_candidates(
        ticker=_ticker(basic),
        direction=direction,
        format_id=format_id,
        key_level=angle_copy["key_level"],
        risk_pct=f"{risk_pct:.2f}%",
        reward_pct=f"{reward_pct:.2f}%",
        rsi=ind.rsi,
        adx=ind.adx,
        price_vs_vwap=relation,
        angle_title=angle.title,
        change_15m=(attention.change_15m if attention else 0.0),
        volume_spike=(attention.volume_spike if attention else 1.0),
        attention_label=(attention.label if attention else ""),
    )
    return choose_headline(candidates, recent_titles, index)


def _build_generated(
    *,
    basic: str,
    mtf,
    score,
    levels: Dict[str, float],
    btc,
    memory: Optional[PostMemory],
    format_id: str,
    angle: SignalAngle,
    index: int,
    attention: Optional[AttentionSnapshot] = None,
    ai: Optional[dict] = None,
) -> Optional[GeneratedPost]:
    ind = mtf.tf_15m
    if ind is None:
        return None
    previous = memory.find_previous_symbol(mtf.symbol) if memory else None
    recent_titles = memory.get_last_titles(30) if memory else []
    recent_ctas = memory.get_last_ctas(30) if memory else []
    angle_copy = _angle_copy(angle, ind, score.direction)
    facts = _fact_catalog(ind, mtf, score.direction, btc, angle, levels, attention)
    headline = _headline_for(
        basic=basic,
        direction=score.direction,
        format_id=format_id,
        angle=angle,
        angle_copy=angle_copy,
        ind=ind,
        levels=levels,
        recent_titles=recent_titles,
        index=index,
        attention=attention,
    )
    fact_ids = ai["fact_ids"] if ai else _default_fact_ids(format_id, facts, index)

    insight_pool = (
        "Сигнал помогает выбрать сторону, но цена входа всё равно важнее красивой статистики.",
        "Мне важнее получить понятную реакцию, чем обязательно успеть в каждое движение.",
        "Хороший сетап не обязан становиться сделкой, если рынок не даёт нормального исполнения.",
        "После сильного импульса я предпочитаю получить подтверждение, а не платить за спешку.",
        "Рынок может уйти без меня; поздний вход обычно обходится дороже упущенного движения.",
    )
    insight = ai["insight"] if ai else insight_pool[index % len(insight_pool)]
    personal = ai["hook"] if ai else author_note(index + len(format_id))
    question = ai["question"] if ai else _question(format_id, index, recent_ctas)
    tags = _tags(angle, score.direction, index)

    text = _render_post(
        headline=headline,
        format_id=format_id,
        direction=score.direction,
        angle=angle,
        angle_copy=angle_copy,
        facts=facts,
        fact_ids=fact_ids,
        levels=levels,
        insight=insight,
        personal=personal,
        question=question,
        tags=tags,
        previous=previous,
        attention=attention,
        variant_index=index,
        ai_copy=ai is not None,
    )
    valid, reasons = _validate_contract(
        text,
        basic=basic,
        direction=score.direction,
        levels=levels,
        facts=facts,
        headline=headline,
        format_id=format_id,
        key_level=angle_copy["key_level"],
    )
    if not valid:
        logger.debug("Rejected %s candidate: %s", format_id, "; ".join(reasons))
        return None

    item = FORMAT_BY_ID[format_id]
    return GeneratedPost(
        text=text,
        style_id=(f"ai_{format_id}" if ai is not None else f"human_{format_id}"),
        signal_type=angle.id,
        angle_title=angle.title,
        content_format=format_id,
        visual_style=visual_style_for(format_id),
        headline=headline,
        question_mode=item.question_mode,
    )


def generate_post_candidates(
    *,
    symbol: str,
    basic: str,
    mtf,
    score,
    memory: Optional[PostMemory] = None,
    levels: Optional[Dict[str, float]] = None,
    btc=None,
    attention: Optional[AttentionSnapshot] = None,
    variant_count: int = 8,
) -> List[GeneratedPost]:
    del symbol
    ind = mtf.tf_15m
    if ind is None:
        raise ValueError("15m indicators are required")
    levels = dict(levels or _levels(ind, score.direction))
    levels.setdefault("risk_reward", score.risk_reward)
    requested = max(4, min(int(variant_count), 16))

    recent_formats = memory.get_last_content_formats(40) if memory else []
    previous = memory.find_previous_symbol(mtf.symbol) if memory else None
    formats = choose_formats(
        recent_formats,
        requested,
        has_btc=btc is not None,
        has_previous_symbol=previous is not None,
    )
    angles = detect_signal_angles(ind, score.direction, mtf)
    if not angles:
        raise ValueError("No truthful signal angles")

    posts: List[GeneratedPost] = []
    signatures: set[str] = set()

    # Mistral is optional and fact-locked. Deterministic drafts are always added,
    # so an unavailable/awkward AI response can never block publication.
    if _content_mode() in {"ai", "ai_first", "mistral"} and _api_key():
        primary_angle = angles[0]
        facts = _fact_catalog(ind, mtf, score.direction, btc, primary_angle, levels, attention)
        try:
            ai_formats = [item.id for item in formats[: min(AI_VARIANTS, len(formats))]]
            raw_items = _request_ai_candidates(
                basic=basic,
                direction=score.direction,
                formats=ai_formats,
                fact_catalog=facts,
                recent_titles=memory.get_last_titles(10) if memory else [],
            )
            allowed = [item.id for item in formats]
            for index, raw in enumerate(raw_items):
                parsed = _parse_ai_candidate(raw, allowed_formats=allowed, facts=facts)
                if parsed is None:
                    continue
                angle = angles[index % len(angles)]
                draft = _build_generated(
                    basic=basic,
                    mtf=mtf,
                    score=score,
                    levels=levels,
                    btc=btc,
                    memory=memory,
                    format_id=parsed["format_id"],
                    angle=angle,
                    index=index,
                    attention=attention,
                    ai=parsed,
                )
                if draft is None:
                    continue
                signature = PostMemory.normalize_text(draft.text)
                if signature in signatures:
                    continue
                signatures.add(signature)
                posts.append(draft)
        except Exception as exc:
            logger.warning("AI editorial generation unavailable; using local engine: %s", exc)

    if len(posts) >= requested:
        return posts[:requested]

    history_seed = len(memory.items) if memory else 0
    attempts = max(56, requested * 14)
    used_format_ids = {item.content_format for item in posts}
    required_unique_formats = min(requested, len({item.id for item in formats}))

    for attempt in range(attempts):
        format_item = formats[attempt % len(formats)]
        if format_item.id in used_format_ids and len(used_format_ids) < required_unique_formats:
            continue
        angle = angles[(attempt + history_seed) % len(angles)]
        digest = hashlib.sha256(
            f"human-feed-v6|{history_seed}|{attempt}|{basic}|{score.direction}".encode()
        ).digest()
        index = int.from_bytes(digest[:4], "big") % 10000
        draft = _build_generated(
            basic=basic,
            mtf=mtf,
            score=score,
            levels=levels,
            btc=btc,
            memory=memory,
            format_id=format_item.id,
            angle=angle,
            index=index,
            attention=attention,
            ai=None,
        )
        if draft is None:
            continue
        signature = PostMemory.normalize_text(draft.text)
        if signature in signatures:
            continue
        signatures.add(signature)
        posts.append(draft)
        used_format_ids.add(draft.content_format)
        if len(posts) >= requested:
            break
    return posts


def generate_post_draft(
    *,
    symbol: str,
    basic: str,
    mtf,
    score,
    memory: Optional[PostMemory] = None,
    levels: Optional[Dict[str, float]] = None,
    btc=None,
    attention: Optional[AttentionSnapshot] = None,
    variant_index: int = 0,
) -> GeneratedPost:
    drafts = generate_post_candidates(
        symbol=symbol,
        basic=basic,
        mtf=mtf,
        score=score,
        memory=memory,
        levels=levels,
        btc=btc,
        attention=attention,
        variant_count=max(4, variant_index + 1),
    )
    if not drafts:
        raise ValueError("No valid post draft")
    return drafts[variant_index % len(drafts)]


def generate_post_with_memory(**kwargs) -> str:
    return generate_post_draft(**kwargs).text
