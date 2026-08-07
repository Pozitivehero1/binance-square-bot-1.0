"""Fact-locked editorial generator for Binance Square.

The market engine still decides *which* setup is publishable. This module decides
*how* to present it: level story, risk memo, two scenarios, educational note,
trader journal, market context and other materially different formats.

Free AI prose is optional. Prices, percentages, direction, levels and all market
claims are inserted and validated by code.
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

load_dotenv()

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

logger = logging.getLogger(__name__)

POST_MAX_CHARS = int(os.getenv("POST_MAX_CHARS", "760"))
POST_MIN_CHARS = int(os.getenv("POST_MIN_CHARS", "180"))
AI_VARIANTS = max(2, min(int(os.getenv("AI_VARIANTS", "6")), 12))
AI_TIMEOUT = max(10, min(int(os.getenv("AI_TIMEOUT", "50")), 120))
AI_TEMPERATURE = max(0.0, min(float(os.getenv("AI_TEMPERATURE", "0.58")), 0.75))

# Only these families are full trade plans. Other formats are deliberately
# shorter feed posts with one key level and one invalidation point.
FULL_PLAN_FORMATS = {
    "setup_plan", "risk_memo", "two_scenarios", "execution_protocol",
    "signal_vs_trade", "follow_up",
}


@dataclass(frozen=True)
class GeneratedPost:
    text: str
    style_id: str
    signal_type: str
    angle_title: str
    content_format: str = "setup_plan"
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
    for label, item in (("1H", getattr(mtf, "tf_1h", None)), ("4H", getattr(mtf, "tf_4h", None)), ("1D", getattr(mtf, "tf_1d", None))):
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
    reward = abs(float(levels["tp3"]) - float(levels["entry"])) / entry * 100.0
    return risk, reward


def _angle_copy(angle: SignalAngle, ind, direction: str) -> Dict[str, str]:
    long_side = direction == "long"
    direction_word = "роста" if long_side else "снижения"
    invalid_side = "ниже" if long_side else "выше"
    key_level = ind.resistance if long_side else ind.support

    hooks = {
        "breakout": "Цена вышла за границу диапазона, но пробой ещё нужно удержать.",
        "liquidity_reclaim": "После снятия ликвидности рынок вернулся в рабочую область.",
        "pullback": "Импульс уже был; теперь качество идеи решает откат к структуре.",
        "trend_continuation": "Локальная структура поддерживает продолжение, если цена не потеряет опору.",
        "volume_impulse": "Объём усилил движение, но сам по себе не даёт безопасной точки входа.",
        "mtf_alignment": "Несколько таймфреймов смотрят в одну сторону, поэтому локальный триггер становится важнее.",
        "vwap_control": "Положение относительно VWAP подтверждает контроль одной стороны рынка.",
        "range_edge": "Цена находится у границы, где сценарий быстро подтвердится или сломается.",
        "momentum": "Импульс поддерживает направление, но вход должен оставаться привязанным к уровню.",
        "volatility_expansion": "Волатильность расширилась, поэтому ошибку нельзя оплачивать завышенным размером позиции.",
        "trend_structure": "Структура даёт направление, а реакция на уровень определит исполнение.",
    }
    thesis = hooks.get(angle.id, hooks["trend_structure"])
    trigger = (
        f"удержание цены выше {_fmt_price(key_level)} и реакция покупателей"
        if long_side
        else f"удержание цены ниже {_fmt_price(key_level)} и реакция продавцов"
    )
    failure = f"закрепление {invalid_side} стоп-уровня"
    return {
        "thesis": thesis,
        "trigger": trigger,
        "failure": failure,
        "direction_word": direction_word,
        "key_level": _fmt_price(key_level),
    }


def _fact_catalog(ind, mtf, direction: str, btc, angle: SignalAngle, levels: Dict[str, float], attention: Optional[AttentionSnapshot] = None) -> Dict[str, str]:
    aligned, total, tf_detail = _higher_tf_alignment(mtf, direction)
    price_vwap = "выше" if ind.price >= ind.vwap else "ниже"
    ema_relation = "выше" if ind.ema20 >= ind.ema50 else "ниже"
    risk_pct, reward_pct = _risk_reward_percentages(levels)
    facts = {
        "setup": f"Основа идеи: {angle.title.lower()}.",
        "trend": f"На 15M EMA20 {ema_relation} EMA50.",
        "momentum": f"RSI {ind.rsi:.1f}, ADX {ind.adx:.1f}.",
        "volume": f"Относительный объём x{ind.volume_relative:.2f}.",
        "vwap": f"Цена {price_vwap} VWAP {_fmt_price(ind.vwap)}.",
        "changes": f"Изменение: 1H {ind.change_1h:+.2f}%, 4H {ind.change_4h:+.2f}%.",
        "range": f"Диапазон контроля {_fmt_price(ind.support)}–{_fmt_price(ind.resistance)}.",
        "mtf": f"Старшие ТФ: {aligned}/{total} совпадают с направлением ({tf_detail})." if total else "Старшие ТФ недоступны.",
        "risk_math": f"До стопа {risk_pct:.2f}%, потенциал до TP3 {reward_pct:.2f}%.",
    }
    if attention is not None:
        facts["fresh_move"] = (
            f"Сейчас: 15M {attention.change_15m:+.2f}%, 45M {attention.change_45m:+.2f}%."
        )
        facts["fresh_volume"] = (
            f"Объём последней закрытой 15M-свечи x{attention.volume_spike:.2f} к медиане 20 свечей."
        )
        facts["turnover"] = f"Оборот за последний час около {format_turnover(attention.turnover_1h)}."
        facts["attention"] = f"Текущий режим: {attention.label}."
    if btc is not None:
        bias = {"bullish": "бычий", "bearish": "медвежий", "neutral": "нейтральный"}.get(str(btc.bias).lower(), str(btc.bias))
        facts["btc"] = f"BTC-фон {bias}: 1H {btc.change_1h:+.2f}%, 4H {btc.change_4h:+.2f}%."
    return facts


def _plan_block(levels: Dict[str, float], compact: bool = False, variant_index: int = 0) -> str:
    entry = _fmt_price(levels["entry"])
    targets = " / ".join(_fmt_price(levels[key]) for key in ("tp1", "tp2", "tp3"))
    stop = _fmt_price(levels["stop"])
    rr = f"{levels['risk_reward']:.2f}"
    variants = (
        f"План сделки\nВход: {entry} USDT\nЦели: {targets} USDT\nСтоп-лосс: {stop} USDT\nR/R: {rr}",
        f"Рабочие уровни\nВход: {entry} USDT\nЦели: {targets} USDT\nСтоп-лосс: {stop} USDT\nR/R: {rr}",
        f"Карта исполнения\nВход: {entry} USDT · R/R: {rr}\nЦели: {targets} USDT\nСтоп-лосс: {stop} USDT",
        f"Параметры сценария\nВход: {entry} USDT\nСтоп-лосс: {stop} USDT\nЦели: {targets} USDT\nR/R: {rr}",
        f"Ценовой маршрут\nВход: {entry} USDT\nЦели: {targets} USDT\nR/R: {rr}\nСтоп-лосс: {stop} USDT",
    )
    if compact:
        compact_variants = (
            f"Вход: {entry} USDT\nЦели: {targets} USDT\nСтоп-лосс: {stop} USDT · R/R: {rr}",
            f"Вход: {entry} USDT · R/R: {rr}\nЦели: {targets} USDT\nСтоп-лосс: {stop} USDT",
            f"Вход: {entry} USDT\nСтоп-лосс: {stop} USDT\nЦели: {targets} USDT · R/R: {rr}",
        )
        return compact_variants[variant_index % len(compact_variants)]
    return variants[variant_index % len(variants)]


def _level_block(levels: Dict[str, float], direction: str, key_level: str, variant_index: int = 0) -> str:
    side = "LONG" if direction == "long" else "SHORT"
    stop = _fmt_price(levels["stop"])
    tp1 = _fmt_price(levels["tp1"])
    variants = (
        f"Ключевой уровень: {key_level} USDT\n{side} подтверждается реакцией на уровне. Первая цель: {tp1}. Отмена: {stop}.",
        f"Что отслеживаю: {key_level} USDT\nЦель при подтверждении: {tp1}. Граница ошибки: {stop}.",
        f"Уровень решения: {key_level} USDT\nВыше/ниже него рынок подтверждает {side}; стоп-сценарий: {stop}. TP1: {tp1}.",
    )
    return variants[variant_index % len(variants)]


_QUESTIONS = (
    "Какое подтверждение для вас здесь обязательное?",
    "Вы бы ждали повторный тест или реакцию на закрытии свечи?",
    "Что здесь важнее для решения: уровень, объём или старший контекст?",
    "Какой аргумент против этого сценария вы считаете самым сильным?",
    "Для вас это уже точка входа или пока только зона наблюдения?",
    "Вы бы фиксировали часть позиции на первой цели?",
    "Какой признак заставил бы вас пропустить эту сделку?",
    "Вы бы исполняли такой план одним ордером или частями?",
)


def _question(format_id: str, variant_index: int, used: Iterable[str]) -> str:
    if question_forbidden(format_id):
        return ""
    # Optional formats deliberately skip the question in roughly one third of drafts,
    # so the public feed does not look like an engagement-bait template.
    if not question_required(format_id) and variant_index % 3 == 2:
        return ""
    normalized_used = {PostMemory.normalize_text(item) for item in used if item}
    available = [item for item in _QUESTIONS if PostMemory.normalize_text(item) not in normalized_used]
    pool = available or list(_QUESTIONS)
    return pool[variant_index % len(pool)]


def _tags(angle: SignalAngle, direction: str, variant_index: int) -> str:
    angle_tag = {
        "breakout": "#PriceAction",
        "liquidity_reclaim": "#Liquidity",
        "pullback": "#TrendTrading",
        "trend_continuation": "#MarketStructure",
        "volume_impulse": "#VolumeAnalysis",
        "mtf_alignment": "#TechnicalAnalysis",
        "vwap_control": "#VWAP",
        "range_edge": "#PriceAction",
        "momentum": "#Momentum",
        "volatility_expansion": "#RiskManagement",
    }.get(angle.id, "#TechnicalAnalysis")
    direction_tag = "#Long" if direction == "long" else "#Short"
    mode = variant_index % 5
    if mode == 0:
        return ""
    if mode == 1:
        return direction_tag
    return angle_tag


def _fact_line(facts: Dict[str, str], ids: Sequence[str]) -> str:
    return " ".join(facts[item] for item in ids if item in facts)


def _default_fact_ids(format_id: str, facts: Dict[str, str], variant_index: int = 0) -> List[str]:
    mapping = {
        "hot_reaction": ["fresh_move", "fresh_volume", "range", "vwap"],
        "one_problem": ["fresh_move", "fresh_volume", "risk_math", "range"],
        "crowd_trap": ["fresh_move", "fresh_volume", "risk_math", "mtf"],
        "chart_story": ["fresh_move", "range", "trend", "vwap"],
        "why_wait": ["fresh_move", "fresh_volume", "range", "vwap", "risk_math", "trend"],
        "level_story": ["fresh_move", "fresh_volume", "range", "trend", "vwap", "mtf"],
        "two_scenarios": ["fresh_move", "fresh_volume", "setup", "mtf", "risk_math", "range"],
        "risk_memo": ["fresh_move", "risk_math", "fresh_volume", "range", "momentum", "mtf"],
        "trader_journal": ["fresh_move", "fresh_volume", "setup", "range", "momentum", "mtf"],
        "indicator_lesson": ["fresh_move", "fresh_volume", "momentum", "trend", "vwap", "mtf"],
        "market_context": ["fresh_move", "fresh_volume", "btc", "mtf", "trend", "range"],
        "data_brief": ["fresh_move", "fresh_volume", "turnover", "momentum", "vwap", "range"],
        "setup_plan": ["fresh_move", "fresh_volume", "setup", "trend", "mtf", "range"],
        "contrarian_take": ["fresh_move", "fresh_volume", "vwap", "risk_math", "range", "mtf"],
        "mistake_to_avoid": ["fresh_move", "fresh_volume", "risk_math", "vwap", "range", "mtf"],
        "execution_protocol": ["fresh_move", "fresh_volume", "setup", "risk_math", "range", "momentum"],
        "signal_vs_trade": ["fresh_move", "fresh_volume", "setup", "momentum", "vwap", "risk_math"],
        "liquidity_map": ["fresh_move", "fresh_volume", "range", "vwap", "risk_math", "trend"],
        "follow_up": ["fresh_move", "fresh_volume", "range", "mtf", "risk_math", "trend"],
    }
    pool = [item for item in mapping.get(format_id, mapping["setup_plan"]) if item in facts]
    if not pool:
        return []
    start = variant_index % len(pool)
    rotated = pool[start:] + pool[:start]
    count = 2 if variant_index % 4 == 3 else 3
    return rotated[: min(count, len(rotated))]


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
    variant_index: int = 0,
) -> str:
    side = "LONG" if direction == "long" else "SHORT"
    fact_text = _fact_line(facts, fact_ids)
    plan = (
        _plan_block(levels, compact=format_id == "risk_memo", variant_index=variant_index)
        if format_id in FULL_PLAN_FORMATS
        else _level_block(levels, direction, angle_copy["key_level"], variant_index)
    )
    trigger = angle_copy["trigger"]
    failure_variants = (
        "Закрепление за стоп-уровнем отменяет идею; без усреднения.",
        "Если цена принимает область за стопом, сценарий закрывается без переноса границы ошибки.",
        "Выход за стоп-уровень означает отмену плана, а не повод увеличивать позицию.",
        "После закрепления за стопом гипотеза больше не действует; усреднение не используется.",
        "Стоп является технической границей идеи: рынок пересёк её — позиция закрыта.",
        "Точка ошибки известна заранее; за стопом я не защищаю сценарий словами.",
    )
    failure = failure_variants[variant_index % len(failure_variants)]

    if format_id == "hot_reaction":
        blocks = (
            headline,
            f"Сильная свеча сама по себе для меня не вход. Сейчас смотрю, удержит ли рынок {angle_copy['key_level']} USDT.",
            f"Для {side} нужно подтверждение: {trigger}.",
            fact_text,
            plan,
            f"Не подтвердят — пропускаю. Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "one_problem":
        blocks = (
            headline,
            f"Направление у идеи {side}, но я не хочу покупать красивую картинку. Проблема простая: без реакции на {angle_copy['key_level']} USDT цена может оставить плохой вход.",
            f"Что вижу сейчас: {fact_text}",
            f"Поэтому мой план — не догонять. Жду {trigger}.",
            plan,
            f"Если рынок уходит за границу ошибки — {failure}",
            question,
            tags,
        )
    elif format_id == "crowd_trap":
        blocks = (
            headline,
            f"{side} может быть верным, а вход — плохим. Ловушка здесь простая: запрыгнуть после заметной свечи.",
            f"Я смотрю на {angle_copy['key_level']} USDT и жду: {trigger}.",
            fact_text,
            f"Нет подтверждения — пропускаю движение.",
            plan,
            f"Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "chart_story":
        blocks = (
            headline,
            f"На графике сейчас важна не следующая свеча, а борьба вокруг {angle_copy['key_level']} USDT. {angle_copy['thesis']}",
            f"Для {side} мне нужна реакция: {trigger}. Если её нет — торговать здесь для меня нечего.",
            fact_text,
            plan,
            f"Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "why_wait":
        blocks = (
            headline,
            f"Сетап направлен в {side}, но вход по текущей цене может испортить соотношение риска. {angle_copy['thesis']}",
            f"Что я жду: {trigger}.",
            personal,
            fact_text,
            plan,
            f"Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "level_story":
        blocks = (
            headline,
            f"Ключевая граница: {angle_copy['key_level']} USDT. Пока цена рядом, прогноз менее полезен, чем наблюдение за реакцией.",
            f"Сценарий {side} подтверждается, если рынок покажет {trigger}.",
            f"Почему уровень важен: {fact_text}",
            plan,
            f"Моя позиция: {personal}",
            f"Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "two_scenarios":
        blocks = (
            headline,
            f"Сценарий A — продолжение {angle_copy['direction_word']} в направлении {side}: {trigger}.",
            f"Сценарий B — отказ: {angle_copy['failure']}; позиция не открывается или закрывается по стопу.",
            f"Факты для выбора: {fact_text}",
            plan,
            f"Отмена: {failure}",
            personal,
            question,
            tags,
        )
    elif format_id == "risk_memo":
        blocks = (
            headline,
            f"Направление: {side}. Риск считаю до входа, а не после первой неприятной свечи.",
            fact_text,
            plan,
            f"Правило исполнения: {failure}",
            author_principle(),
            tags,
        )
    elif format_id == "trader_journal":
        blocks = (
            headline,
            f"Запись в журнале. {personal}",
            f"Что вижу: направление {side}. {angle_copy['thesis']} {fact_text}",
            f"Что должно произойти до сделки: {trigger}.",
            plan,
            f"Где признаю ошибку: {failure}",
            question,
            tags,
        )
    elif format_id == "indicator_lesson":
        blocks = (
            headline,
            f"Направление {side}. RSI показывает положение импульса, ADX — его силу, но ни один из них не определяет безопасную цену исполнения.",
            fact_text,
            f"Практический вывод: индикаторы фильтруют направление, а вход активирует только {trigger}.",
            insight,
            plan,
            f"Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "market_context":
        blocks = (
            headline,
            f"Локально идея направлена в {side}. Но я не рассматриваю альткоин отдельно от BTC и старших таймфреймов.",
            fact_text,
            f"Исполнение допустимо только после условия: {trigger}.",
            personal,
            plan,
            f"Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "data_brief":
        blocks = (
            headline,
            f"Направление: {side}. {fact_text}",
            f"Вывод: {insight}",
            f"Триггер: {trigger}.",
            plan,
            f"Отмена: {failure}",
            tags,
        )
    elif format_id == "contrarian_take":
        blocks = (
            headline,
            f"Направление: {side}. Контраргумент к очевидному движению: {angle_copy['thesis']}",
            f"За идею: {fact_text}",
            "Против раннего входа: цена может продолжить движение без нормального ретеста, оставив слабое R/R.",
            personal,
            plan,
            f"Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "mistake_to_avoid":
        blocks = (
            headline,
            f"Направление {side}. Типичная ошибка — входить только потому, что картинка уже выглядит убедительно.",
            f"Почему это опасно здесь: {fact_text}",
            f"Вместо погони за ценой я жду {trigger}.",
            personal,
            plan,
            f"Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "execution_protocol":
        blocks = (
            headline,
            f"Сначала проверяю направление {side}. Затем сверяю факты: {fact_text}",
            f"После этого жду условие: {trigger}.",
            "Перед ордером рассчитываю размер позиции от стопа и не меняю границу ошибки после входа.",
            plan,
            f"Отмена: {failure}",
            tags,
        )
    elif format_id == "signal_vs_trade":
        blocks = (
            headline,
            f"Направление сигнала: {side}. {angle_copy['thesis']} {fact_text}",
            f"Сделка появляется только после условия: {trigger}.",
            "До подтверждения это аналитическое наблюдение, а не причина обязательно открывать позицию.",
            plan,
            f"Отмена: {failure}",
            personal,
            question,
            tags,
        )
    elif format_id == "liquidity_map":
        blocks = (
            headline,
            f"Рабочая карта: поддержка и сопротивление задают область, внутри которой шум легко принять за движение. {fact_text}",
            f"Для сценария {side} важна реакция: {trigger}.",
            plan,
            personal,
            f"Отмена: {failure}",
            question,
            tags,
        )
    elif format_id == "follow_up":
        previous_title = str((previous or {}).get("title", "прошлый сценарий")).strip()
        blocks = (
            headline,
            f"Прошлый фокус: «{previous_title[:120]}». Сейчас я пересчитываю уровни по новой структуре, а не защищаю старую идею.",
            fact_text,
            f"Новый триггер: {trigger}.",
            plan,
            f"Отмена: {failure}",
            question,
            tags,
        )
    else:  # setup_plan
        blocks = (
            headline,
            f"Направление: {side}. {angle_copy['thesis']}",
            fact_text,
            f"Триггер: {trigger}.",
            plan,
            personal,
            f"Отмена: {failure}",
            question,
            tags,
        )

    parts = [str(part).strip() for part in blocks if str(part).strip()]
    if len(parts) > 4:
        headline_part = parts[0]
        tag_part = parts[-1] if parts[-1].startswith("#") else ""
        body = parts[1:-1] if tag_part else parts[1:]
        question_part = ""
        if body and body[-1].endswith("?"):
            question_part = body.pop()
        plan_positions = [i for i, value in enumerate(body) if "Вход:" in value and "Стоп-лосс:" in value]
        if plan_positions:
            plan_part = body.pop(plan_positions[0])
            mode = variant_index % 3
            if mode == 1:
                body.insert(min(1, len(body)), plan_part)
            elif mode == 2:
                body.insert(max(1, len(body) - 1), plan_part)
            else:
                body.insert(min(4, len(body)), plan_part)
        if variant_index % 5 == 4 and len(body) >= 3:
            body[0], body[1] = body[1], body[0]
        parts = [headline_part, *body]
        if question_part:
            parts.append(question_part)
        if tag_part:
            parts.append(tag_part)
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
    text = re.sub(r"\s+", " ", text).strip(" -–—:;,.")[:limit].rstrip(" -–—:;,.")
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
        "task": f"Создай {len(formats)} разных редакционных вариантов для технического сетапа {direction.upper()} по {basic.upper()}.",
        "requested_formats_in_order": list(formats),
        "facts": fact_catalog,
        "recent_openings_to_avoid": list(recent_titles[-10:]),
        "rules": {
            "one_candidate_per_requested_format": True,
            "use_only_fact_ids": True,
            "fact_ids_count": "2-3",
            "hook": "человеческая подводка без цифр, тикеров, обещаний и внешних фактов",
            "insight": "одно короткое авторское объяснение без цифр и новых фактов",
            "question": "естественный вопрос без цифр; пустая строка допустима",
            "style": "русский язык, спокойно, конкретно, без канцелярита и рекламы",
        },
        "json_shape": {"candidates": [{"format_id": formats[0] if formats else "setup_plan", "hook": "строка", "insight": "строка", "question": "строка", "fact_ids": ["setup", "volume"]}]},
    }
    body = {
        "model": os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
        "messages": [
            {"role": "system", "content": "Ты редактор. Используй только переданные факты. Не придумывай числа, события, новости, причины движения, статистику успеха или мнения рынка. Верни только JSON."},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": AI_TEMPERATURE,
        "presence_penalty": 0.25,
        "frequency_penalty": 0.25,
        "max_tokens": 2200,
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
        content = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
    parsed = json.loads(_clean_json(str(content)))
    candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else []
    return [item for item in candidates if isinstance(item, dict)]


def _parse_ai_candidate(raw: dict, *, allowed_formats: Sequence[str], facts: Dict[str, str]) -> Optional[dict]:
    format_id = str(raw.get("format_id", "")).strip()
    if format_id not in allowed_formats or format_id not in FORMAT_BY_ID:
        return None
    hook = _safe_ai_text(raw.get("hook"), 180)
    insight = _safe_ai_text(raw.get("insight"), 220)
    question = _safe_ai_text(raw.get("question"), 130, question=True) if raw.get("question") else ""
    if not hook or not insight:
        return None
    if question_forbidden(format_id):
        question = ""
    elif question_required(format_id) and not question:
        return None
    ids = []
    for value in raw.get("fact_ids", []) if isinstance(raw.get("fact_ids"), list) else []:
        key = str(value).strip()
        if key in facts and key not in ids:
            ids.append(key)
    if not 2 <= len(ids) <= 3:
        return None
    return {"format_id": format_id, "hook": hook, "insight": insight, "question": question, "fact_ids": ids}


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
) -> Tuple[bool, Tuple[str, ...]]:
    reasons: List[str] = []
    if not 1 <= _ticker_count(text, basic) <= 3:
        reasons.append("ticker count")
    lowered = text.lower().replace("ё", "е")
    if format_id in FULL_PLAN_FORMATS:
        for marker in ("вход:", "цели:", "стоп-лосс:", "r/r:"):
            if marker not in lowered:
                reasons.append(f"missing {marker}")
        for key in ("entry", "tp1", "tp2", "tp3", "stop"):
            value = _fmt_price(levels[key])
            if value not in text:
                reasons.append(f"missing {key}")
        if f"{levels['risk_reward']:.2f}" not in text:
            reasons.append("missing rr")
    else:
        for key in ("tp1", "stop"):
            if _fmt_price(levels[key]) not in text:
                reasons.append(f"missing {key}")
        if not any(marker in lowered for marker in ("ключевой уровень:", "уровень решения:", "что отслеживаю:")):
            reasons.append("missing key level")
    terms = ("LONG", "ЛОНГ") if direction == "long" else ("SHORT", "ШОРТ")
    if not any(term in text.upper() for term in terms):
        reasons.append("missing direction")
    q_count = text.count("?")
    if question_required(format_id) and q_count != 1:
        reasons.append("question required")
    if question_forbidden(format_id) and q_count:
        reasons.append("question forbidden")
    if q_count > 1:
        reasons.append("too many questions")
    if len(text) < POST_MIN_CHARS or len(text) > POST_MAX_CHARS:
        reasons.append(f"length {len(text)}")
    if len(re.findall(r"#[A-Za-zА-Яа-я0-9_]+", text)) > 2:
        reasons.append("too many hashtags")
    if text.splitlines()[0].strip() != headline.strip():
        reasons.append("headline mismatch")
    if re.search(r"\$[A-Z0-9]+\s*[—-]\s*(?:LONG|SHORT)\s*:", headline, flags=re.IGNORECASE):
        reasons.append("generic headline")
    for pattern in _AI_FORBIDDEN:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            reasons.append("forbidden claim")
            break

    allowed_source = " ".join(
        [headline, *facts.values(), *(_fmt_price(levels[key]) for key in ("entry", "tp1", "tp2", "tp3", "stop")), f"{levels['risk_reward']:.2f}", "15M 1H 4H 1D EMA20 EMA50 RSI ADX TP3"]
    )
    allowed = {value.replace(",", ".") for value in _numeric_tokens(allowed_source)}
    unexpected = {value.replace(",", ".") for value in _numeric_tokens(text)} - allowed
    if unexpected:
        reasons.append("unexpected numbers: " + ",".join(sorted(unexpected)))
    return not reasons, tuple(reasons)


def _headline_for(
    *, basic: str, direction: str, format_id: str, angle: SignalAngle, angle_copy: Dict[str, str], ind, levels: Dict[str, float], recent_titles: Sequence[str], index: int, attention: Optional[AttentionSnapshot] = None,
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
    *, basic: str, mtf, score, levels: Dict[str, float], btc, memory: Optional[PostMemory], format_id: str, angle: SignalAngle, index: int, attention: Optional[AttentionSnapshot] = None, ai: Optional[dict] = None,
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
        basic=basic, direction=score.direction, format_id=format_id, angle=angle,
        angle_copy=angle_copy, ind=ind, levels=levels, recent_titles=recent_titles, index=index, attention=attention,
    )
    fact_ids = ai["fact_ids"] if ai else _default_fact_ids(format_id, facts, index)
    insight_pool = (
        "Я использую индикаторы как фильтр, но решение оставляю за реакцией цены на заранее отмеченной границе.",
        "Направление помогает подготовить план, а качество реакции определяет, будет ли сделка вообще.",
        "Даже сильные показания не компенсируют вход вдали от рабочей границы.",
        "Связка факторов полезнее отдельного индикатора, если точка отмены остаётся понятной.",
        "Технические данные дают контекст, но ордер появляется только после подтверждения.",
        "Смысл анализа не в прогнозе каждой свечи, а в заранее определённых условиях действия.",
        "Я отделяю хороший сетап от хорошего исполнения: это не одно и то же.",
        "Подтверждение важнее желания обязательно участвовать в движении.",
        "Рынок может уйти без позиции; хуже войти там, где риск уже потерял смысл.",
        "Преимущество существует только пока структура и риск не противоречат друг другу.",
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
        variant_index=index,
    )
    valid, reasons = _validate_contract(
        text,
        basic=basic,
        direction=score.direction,
        levels=levels,
        facts=facts,
        headline=headline,
        format_id=format_id,
    )
    if not valid:
        logger.debug("Rejected %s candidate: %s", format_id, "; ".join(reasons))
        return None
    item = FORMAT_BY_ID[format_id]
    return GeneratedPost(
        text=text,
        style_id=(f"ai_{format_id}" if ai is not None else f"editorial_{format_id}"),
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

    # One Mistral request can enrich several different editorial formats. It never
    # writes market numbers or levels and therefore cannot alter the trade plan.
    if _content_mode() in {"ai", "ai_first", "mistral"} and _api_key():
        primary_angle = angles[0]
        facts = _fact_catalog(ind, mtf, score.direction, btc, primary_angle, levels, attention)
        try:
            raw_items = _request_ai_candidates(
                basic=basic,
                direction=score.direction,
                formats=[item.id for item in formats[: min(AI_VARIANTS, len(formats))]],
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
                    basic=basic, mtf=mtf, score=score, levels=levels, btc=btc,
                    memory=memory, format_id=parsed["format_id"], angle=angle,
                    index=index, attention=attention, ai=parsed,
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

    # Always supplement the batch with deterministic formats. This gives the
    # selector real structural choice even when the AI returns only one style.
    # Do not let an easy-to-generate format fill the batch before every selected
    # editorial family has had a chance to produce a valid draft.
    history_seed = len(memory.items) if memory else 0
    attempts = max(48, requested * 12)
    used_format_ids = {item.content_format for item in posts}
    required_unique_formats = min(requested, len({item.id for item in formats}))
    for attempt in range(attempts):
        format_item = formats[attempt % len(formats)]
        if (
            format_item.id in used_format_ids
            and len(used_format_ids) < required_unique_formats
        ):
            continue
        angle = angles[(attempt + history_seed) % len(angles)]
        # Stable shuffle without global randomness: repeated cron processes do not
        # reset into the same first candidate forever.
        digest = hashlib.sha256(f"reach-v3|{history_seed}|{attempt}|{basic}|{score.direction}".encode()).digest()
        index = int.from_bytes(digest[:4], "big") % 10000
        draft = _build_generated(
            basic=basic, mtf=mtf, score=score, levels=levels, btc=btc,
            memory=memory, format_id=format_item.id, angle=angle,
            index=index, attention=attention, ai=None,
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
    *, symbol: str, basic: str, mtf, score, memory: Optional[PostMemory] = None,
    levels: Optional[Dict[str, float]] = None, btc=None, attention: Optional[AttentionSnapshot] = None, variant_index: int = 0,
) -> GeneratedPost:
    drafts = generate_post_candidates(
        symbol=symbol, basic=basic, mtf=mtf, score=score, memory=memory,
        levels=levels, btc=btc, attention=attention, variant_count=max(4, variant_index + 1),
    )
    if not drafts:
        raise ValueError("No valid post draft")
    return drafts[variant_index % len(drafts)]


def generate_post_with_memory(**kwargs) -> str:
    return generate_post_draft(**kwargs).text
