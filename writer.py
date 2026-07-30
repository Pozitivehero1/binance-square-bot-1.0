"""Fact-based Binance Square post generator with meaningful content diversity."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import random
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

from content_variation import (
    CTA_VARIANTS,
    PLAN_TITLES,
    POST_STYLES,
    PostStyle,
    SignalAngle,
    choose,
    choose_post_style,
    choose_signal_angle,
    detect_signal_angles,
    hashtags as varied_hashtags,
    HUMAN_HOOKS,
    PERSONAL_PHRASES,
    RISK_SENTENCES,
    CONTEXT_OPENERS,
)
from indicators import build_trade_levels
from memory import PostMemory

logger = logging.getLogger(__name__)

MISTRAL_API = (os.getenv("MISTRAL_API") or os.getenv("MISTRAL_API_KEY") or "").strip()
CONTENT_MODE = os.getenv("CONTENT_MODE", "ai_first" if MISTRAL_API else "deterministic").strip().lower()
AI_VARIANTS = max(2, min(int(os.getenv("AI_VARIANTS", "4")), 8))
AI_TIMEOUT = max(10, min(int(os.getenv("AI_TIMEOUT", "50")), 120))
AI_TEMPERATURE = max(0.0, min(float(os.getenv("AI_TEMPERATURE", "0.55")), 0.7))
AI_MIN_VALID = max(1, min(int(os.getenv("AI_MIN_VALID", "2")), AI_VARIANTS))
ENABLE_AI_POLISH = os.getenv("ENABLE_AI_POLISH", "0").strip().lower() in {"1", "true", "yes"}
POST_MAX_CHARS = int(os.getenv("POST_MAX_CHARS", "900"))


@dataclass(frozen=True)
class GeneratedPost:
    text: str
    style_id: str
    signal_type: str
    angle_title: str


# ---------------------------------------------------------------------------
# Formatting and levels
# ---------------------------------------------------------------------------
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


def _format_ticker(basic: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]", "", str(basic)).upper()
    return f"${clean}"


def _fix_ticker_spacing(text: str) -> str:
    text = re.sub(r"(\$[A-Z0-9]{2,15})[,:;.!?]", r"\1", text)
    text = re.sub(r"\$[A-Z0-9]{2,15}", lambda match: match.group(0).replace(" ", ""), text)
    return text


def _mandatory_values(levels: Dict[str, float]) -> List[str]:
    return [
        _fmt_price(levels["entry"]),
        _fmt_price(levels["tp1"]),
        _fmt_price(levels["tp2"]),
        _fmt_price(levels["tp3"]),
        _fmt_price(levels["stop"]),
        f"{levels['risk_reward']:.2f}",
    ]


def _contains_required_content(text: str, levels: Dict[str, float]) -> bool:
    labels = ("Вход", "TP1", "TP2", "TP3", "Стоп", "R/R")
    return all(label.lower() in text.lower() for label in labels) and all(
        value in text for value in _mandatory_values(levels)
    )


def _pick_unused(options: Sequence[str], used: Iterable[str]) -> str:
    normalized_used = {PostMemory.normalize_text(item) for item in used if item}
    available = [item for item in options if PostMemory.normalize_text(item) not in normalized_used]
    return random.choice(available or list(options))


# ---------------------------------------------------------------------------
# Market facts
# ---------------------------------------------------------------------------
def _direction_terms(direction: str) -> Tuple[str, str, str, str]:
    if direction == "long":
        return "LONG", "выше", "ниже", "покупателей"
    return "SHORT", "ниже", "выше", "продавцов"


def _higher_tf_context(mtf, direction: str) -> Tuple[str, int, int]:
    labels: List[str] = []
    aligned_count = 0
    total = 0
    for label, indicator in (("1H", mtf.tf_1h), ("4H", mtf.tf_4h), ("1D", mtf.tf_1d)):
        if indicator is None:
            continue
        total += 1
        aligned = indicator.ema20 > indicator.ema50 if direction == "long" else indicator.ema20 < indicator.ema50
        aligned_count += int(aligned)
        labels.append(f"{label} {'✓' if aligned else '×'}")
    return (" · ".join(labels) if labels else "нет данных старших ТФ", aligned_count, total)


def _btc_context_text(btc, direction: str) -> str:
    if btc is None:
        return "Контекст BTC сейчас недоступен — решение строится только по структуре актива."
    compatible = (
        btc.bias == "neutral"
        or (btc.bias == "bullish" and direction == "long")
        or (btc.bias == "bearish" and direction == "short")
    )
    relation = "поддерживает направление" if compatible else "требует дополнительного подтверждения"
    return (
        f"BTC: {btc.bias}, 1H {btc.change_1h:+.2f}%, 4H {btc.change_4h:+.2f}% — "
        f"фон {relation}."
    )


def _market_metrics(ind) -> Dict[str, str]:
    atr_pct = ind.atr / ind.price * 100.0 if ind.price else 0.0
    return {
        "price": _fmt_price(ind.price),
        "change_1h": f"{ind.change_1h:+.2f}%",
        "change_4h": f"{ind.change_4h:+.2f}%",
        "change_24h": f"{ind.change_24h:+.2f}%",
        "volume": f"x{ind.volume_relative:.2f}",
        "rsi": f"{ind.rsi:.1f}",
        "adx": f"{ind.adx:.1f}",
        "atr_pct": f"{atr_pct:.2f}%",
        "vwap": _fmt_price(ind.vwap),
        "ema20": _fmt_price(ind.ema20),
        "ema50": _fmt_price(ind.ema50),
        "support": _fmt_price(ind.support),
        "resistance": _fmt_price(ind.resistance),
    }


def _angle_content(angle: SignalAngle, ind, direction: str, mtf) -> Dict[str, str]:
    ticker_side, supportive_side, failure_side, participants = _direction_terms(direction)
    metrics = _market_metrics(ind)
    higher_tf, aligned, total = _higher_tf_context(mtf, direction)
    key_level = ind.resistance if direction == "long" else ind.support
    opposite_level = ind.support if direction == "long" else ind.resistance

    default = {
        "hook": f"Рынок формирует {ticker_side}-сценарий у {_fmt_price(key_level)}",
        "thesis": (
            f"Цена находится {supportive_side} EMA20, MACD-гистограмма поддерживает направление, "
            f"а ADX {metrics['adx']} показывает наличие трендового движения."
        ),
        "evidence": (
            f"RSI {metrics['rsi']} · объём {metrics['volume']} · VWAP {metrics['vwap']} · "
            f"старшие ТФ: {higher_tf}."
        ),
        "confirmation": f"Подтверждение — удержание цены {supportive_side} {_fmt_price(key_level)}.",
        "failure": f"Слабость проявится при возврате {failure_side} {_fmt_price(opposite_level)}.",
    }

    if angle.id == "breakout":
        return {
            "hook": f"Пробой {_fmt_price(key_level)} переводит рынок в {ticker_side}-режим",
            "thesis": (
                f"Цена закрылась {supportive_side} ключевой границы диапазона. Это не просто движение внутри боковика: "
                f"уровень {_fmt_price(key_level)} теперь должен подтвердиться как опора сценария."
            ),
            "evidence": f"Объём {metrics['volume']} · ADX {metrics['adx']} · 1H {metrics['change_1h']} · {higher_tf}.",
            "confirmation": f"Главное подтверждение — повторное удержание {_fmt_price(key_level)} после возможного ретеста.",
            "failure": f"Возврат и закрепление {failure_side} пробитой границы отменит идею продолжения.",
        }

    if angle.id == "liquidity_reclaim":
        swept = ind.swing_low if direction == "long" else ind.swing_high
        return {
            "hook": f"После снятия ликвидности цена вернулась в пользу {participants}",
            "thesis": (
                f"Рынок проколол {_fmt_price(swept)}, но не удержался за экстремумом и вернулся обратно. "
                f"Такой возврат делает реакцию важнее самого прокола."
            ),
            "evidence": f"Текущая цена {metrics['price']} · VWAP {metrics['vwap']} · RSI {metrics['rsi']} · объём {metrics['volume']}.",
            "confirmation": f"Сценарий усиливается при удержании цены {supportive_side} EMA20 {_fmt_price(ind.ema20)}.",
            "failure": f"Повторный уход {failure_side} свипнутого экстремума вернёт преимущество противоположной стороне.",
        }

    if angle.id == "pullback":
        return {
            "hook": f"Не погоня за ценой: {ticker_side}-идея формируется на откате к EMA20",
            "thesis": (
                f"Основной тренд сохраняется, а цена вернулась к динамической зоне EMA20 {_fmt_price(ind.ema20)} "
                f"и пока удерживает её в сторону текущего движения."
            ),
            "evidence": f"EMA20/EMA50: {_fmt_price(ind.ema20)}/{_fmt_price(ind.ema50)} · RSI {metrics['rsi']} · {higher_tf}.",
            "confirmation": f"Нужна реакция от EMA20 и возврат импульса {supportive_side} текущей цены.",
            "failure": f"Закрепление {failure_side} EMA50 {_fmt_price(ind.ema50)} разрушит логику отката внутри тренда.",
        }

    if angle.id == "trend_continuation":
        return {
            "hook": f"Тренд ещё не сломан: структура остаётся в пользу {ticker_side}",
            "thesis": (
                f"EMA20 расположена в нужную сторону относительно EMA50, цена держится {supportive_side} EMA20, "
                f"а MACD не показывает разворота против сценария."
            ),
            "evidence": f"ADX {metrics['adx']} · RSI {metrics['rsi']} · 4H {metrics['change_4h']} · старшие ТФ: {higher_tf}.",
            "confirmation": f"Продолжение получит подтверждение после обновления локального экстремума по направлению сделки.",
            "failure": f"Потеря EMA20 и возврат к EMA50 ослабят трендовое преимущество.",
        }

    if angle.id == "volume_impulse":
        return {
            "hook": f"Объём вырос до {metrics['volume']} — проверяем, продолжится ли импульс",
            "thesis": (
                f"Текущая свеча проходит на объёме выше среднего. Сам по себе всплеск не гарантирует движение, "
                f"поэтому ключевым остаётся удержание цены у {_fmt_price(key_level)}."
            ),
            "evidence": f"Объём {metrics['volume']} · изменение 1H {metrics['change_1h']} · ADX {metrics['adx']} · RSI {metrics['rsi']}.",
            "confirmation": f"Полезное подтверждение — следующая реакция без резкого возврата {failure_side} ключевого уровня.",
            "failure": f"Если объём останется высоким, но цена вернётся в диапазон, импульс можно считать поглощённым.",
        }

    if angle.id == "mtf_alignment":
        return {
            "hook": f"{aligned} из {total} старших таймфреймов поддерживают {ticker_side}",
            "thesis": (
                f"Сигнал строится не на одном 15-минутном импульсе: направление EMA20/EMA50 совпадает "
                f"на нескольких старших периодах."
            ),
            "evidence": f"Согласованность: {higher_tf} · ADX 15M {metrics['adx']} · объём {metrics['volume']}.",
            "confirmation": f"Локальный вход имеет смысл только пока 15M не расходится со старшей структурой.",
            "failure": f"Разворот 15M против 1H/4H без быстрого восстановления повысит риск ложного входа.",
        }

    if angle.id == "vwap_control":
        return {
            "hook": f"Цена удерживается {supportive_side} VWAP {_fmt_price(ind.vwap)}",
            "thesis": (
                f"VWAP сейчас выступает ориентиром контроля внутри дня. Пока цена остаётся {supportive_side} него, "
                f"инициатива соответствует направлению {ticker_side}."
            ),
            "evidence": f"Цена {metrics['price']} · VWAP {metrics['vwap']} · EMA20 {metrics['ema20']} · объём {metrics['volume']}.",
            "confirmation": f"Лучшее подтверждение — реакция от VWAP с обновлением локального экстремума.",
            "failure": f"Закрепление {failure_side} VWAP ослабит внутридневную структуру.",
        }

    if angle.id == "range_edge":
        return {
            "hook": f"Цена подошла к границе диапазона {_fmt_price(key_level)}",
            "thesis": (
                f"Сейчас важна не скорость движения, а реакция у края диапазона {metrics['support']}–{metrics['resistance']}. "
                f"Именно здесь станет понятно, есть ли продолжение {ticker_side}."
            ),
            "evidence": f"До ключевой границы менее двух ATR · ATR {metrics['atr_pct']} · объём {metrics['volume']}.",
            "confirmation": f"Подтверждение — закрытие и удержание {supportive_side} {_fmt_price(key_level)}.",
            "failure": f"Отбой от границы с возвратом к середине диапазона отменит идею немедленного продолжения.",
        }

    if angle.id == "momentum":
        return {
            "hook": f"Моментум поддерживает {ticker_side}, но вход решает уровень",
            "thesis": (
                f"RSI {metrics['rsi']} и MACD-гистограмма направлены в сторону сценария. "
                f"Моментум подтверждает движение, но не заменяет контроль риска."
            ),
            "evidence": f"RSI {metrics['rsi']} · ADX {metrics['adx']} · 1H {metrics['change_1h']} · VWAP {metrics['vwap']}.",
            "confirmation": f"Импульс должен сохраниться при тесте {_fmt_price(key_level)}.",
            "failure": f"Дивергенция цены и моментума либо возврат {failure_side} VWAP ослабят сигнал.",
        }

    if angle.id == "volatility_expansion":
        return {
            "hook": f"Волатильность расширяется: ATR достиг {metrics['atr_pct']}",
            "thesis": (
                f"Диапазон свечей увеличился одновременно с ADX {metrics['adx']}. Это создаёт потенциал движения, "
                f"но требует меньшего размера позиции из-за более широкого стопа."
            ),
            "evidence": f"ATR {metrics['atr_pct']} · объём {metrics['volume']} · диапазон {metrics['support']}–{metrics['resistance']}.",
            "confirmation": f"Сценарий подтверждается только при направленном выходе без мгновенного возврата в диапазон.",
            "failure": f"Резкое сжатие диапазона после выхода укажет на отсутствие продолжения.",
        }

    return default

# ---------------------------------------------------------------------------
# Layout primitives
# ---------------------------------------------------------------------------
def _level_block(
    levels: Dict[str, float],
    title: str,
    style: PostStyle,
    variant_index: int,
) -> str:
    entry = _fmt_price(levels["entry"])
    tp1 = _fmt_price(levels["tp1"])
    tp2 = _fmt_price(levels["tp2"])
    tp3 = _fmt_price(levels["tp3"])
    stop = _fmt_price(levels["stop"])
    rr = f"{levels['risk_reward']:.2f}"
    fmt = style.level_format

    if fmt == "compact_grid":
        return (
            f"{title}\n"
            f"Вход {entry} USDT  |  Стоп {stop} USDT  |  R/R {rr}\n"
            f"TP1 {tp1}  ·  TP2 {tp2}  ·  TP3 {tp3} USDT"
        )
    if fmt == "inline":
        return (
            f"{title}: Вход {entry} USDT → TP1 {tp1} → TP2 {tp2} → "
            f"TP3 {tp3}; Стоп {stop} USDT; R/R {rr}."
        )
    if fmt == "numbered":
        return (
            f"{title}\n"
            f"0. Вход: {entry} USDT\n"
            f"1. TP1: {tp1} USDT\n"
            f"2. TP2: {tp2} USDT\n"
            f"3. TP3: {tp3} USDT\n"
            f"Защита — Стоп: {stop} USDT · R/R: {rr}"
        )
    if fmt == "route":
        return (
            f"{title}\n"
            f"Старт — Вход: {entry} USDT\n"
            f"Маршрут — TP1: {tp1} → TP2: {tp2} → TP3: {tp3} USDT\n"
            f"Выход при ошибке — Стоп: {stop} USDT\n"
            f"Соотношение — R/R: {rr}"
        )
    if fmt == "risk_reward":
        return (
            f"{title}\n"
            f"Риск: Вход {entry} → Стоп {stop} USDT\n"
            f"Потенциал: TP1 {tp1} / TP2 {tp2} / TP3 {tp3} USDT\n"
            f"R/R: {rr}"
        )
    if fmt == "notebook":
        return (
            f"{title}\n"
            f"— Вход: {entry} USDT\n"
            f"— TP1: {tp1} USDT\n"
            f"— TP2: {tp2} USDT\n"
            f"— TP3: {tp3} USDT\n"
            f"— Стоп: {stop} USDT\n"
            f"— R/R: {rr}"
        )
    if fmt == "card":
        return (
            f"┌ {title}\n"
            f"│ Вход: {entry} USDT\n"
            f"│ TP1: {tp1}  │ TP2: {tp2}  │ TP3: {tp3} USDT\n"
            f"│ Стоп: {stop} USDT\n"
            f"└ R/R: {rr}"
        )
    if fmt == "terminal":
        return (
            f"[{title.upper()}]\n"
            f"ENTRY/Вход={entry} USDT\n"
            f"TP1={tp1} | TP2={tp2} | TP3={tp3} USDT\n"
            f"STOP/Стоп={stop} USDT | R/R={rr}"
        )

    # vertical
    separator = "•" if variant_index % 2 else ":"
    return (
        f"{title}\n"
        f"Вход{separator} {entry} USDT\n"
        f"TP1{separator} {tp1} USDT\n"
        f"TP2{separator} {tp2} USDT\n"
        f"TP3{separator} {tp3} USDT\n"
        f"Стоп{separator} {stop} USDT\n"
        f"R/R{separator} {rr}"
    )


def _risk_block(
    ind,
    direction: str,
    levels: Dict[str, float],
    style: PostStyle,
    variant_index: int,
) -> str:
    failure_side = "ниже" if direction == "long" else "выше"
    stop = _fmt_price(levels["stop"])
    support = _fmt_price(ind.support)
    resistance = _fmt_price(ind.resistance)
    sentence = RISK_SENTENCES[variant_index % len(RISK_SENTENCES)]

    variants = (
        f"Отмена сценария: закрепление {failure_side} {stop}. Рабочий диапазон {support}–{resistance}. {sentence}",
        f"Граница ошибки — {stop} USDT. Если рынок закрепится {failure_side}, идея закрыта. Размер позиции рассчитывается до входа.",
        f"Риск-контроль: Стоп уже находится на {stop}; диапазон контроля {support}–{resistance}. {sentence}",
        f"Когда план перестаёт работать: цена принимает область {failure_side} {stop}. После этого сценарий не усредняется. {sentence}",
        f"Условие отмены сценария — закрепление {failure_side} стоп-уровня {stop}. {sentence}",
        f"Защита капитала: допустимый риск задаётся заранее, а техническая ошибка признаётся {failure_side} {stop}. Диапазон: {support}–{resistance}.",
        f"Красная линия: {stop} USDT. Её потеря означает отмену сценария, а не приглашение увеличить позицию. {sentence}",
        f"До входа фиксирую две вещи: Стоп {stop} и допустимый риск. Если цена закрепится {failure_side}, торговая гипотеза больше не действует.",
        f"Отмена сценария наступает не по эмоциям, а после закрепления {failure_side} {stop}. {sentence}",
        f"Риск ограничен уровнем {stop}. Внутри диапазона {support}–{resistance} наблюдаем реакцию; за стопом идею не защищаем словами.",
    )
    selected = variants[(variant_index + len(style.id)) % len(variants)]
    lowered = selected.lower()
    markers = ("отмена сценария", "размер позиции", "допустимого риска", "стоп-уровня")
    if not any(marker in lowered for marker in markers):
        selected += " Размер позиции определяется от допустимого риска до входа."
    return selected


def _context_block(mtf, btc, direction: str, variant_index: int) -> str:
    higher_tf, aligned, total = _higher_tf_context(mtf, direction)
    btc_text = _btc_context_text(btc, direction)
    label = CONTEXT_OPENERS[variant_index % len(CONTEXT_OPENERS)]
    aligned_text = f"{aligned}/{total} доступных старших ТФ совпадают с направлением" if total else "старшие ТФ недоступны"
    variants = (
        f"{label}: {higher_tf}. {btc_text}",
        f"{label} — {aligned_text}. {btc_text}",
        f"Перед исполнением сверяю рынок шире 15M: {higher_tf}. {btc_text}",
        f"Общий фильтр: {btc_text} По таймфреймам получаем {higher_tf}.",
        f"Не изолирую монету от рынка. {btc_text} Структура ТФ: {higher_tf}.",
        f"Сверка контекста: {higher_tf}; отдельно по BTC — {btc_text.removeprefix('BTC: ')}",
        f"На старших периодах: {higher_tf}. Внешний фон: {btc_text}",
        f"{label}. Совпадение направления — {aligned_text}; {btc_text}",
    )
    return variants[variant_index % len(variants)]


def _clause(text: str) -> str:
    return str(text).strip().rstrip(" .!?;:")


def _inline_clause(text: str) -> str:
    value = _clause(text)
    if len(value) > 1 and value[0].isupper() and value[1].islower():
        return value[0].lower() + value[1:]
    return value


def _render_style(
    *,
    style: PostStyle,
    angle: SignalAngle,
    ticker: str,
    direction_label: str,
    angle_copy: Dict[str, str],
    metrics: Dict[str, str],
    context: str,
    level_block: str,
    risk_block: str,
    cta: str,
    personal_note: str,
    human_prefix: str,
) -> str:
    h = angle_copy["hook"]
    thesis = angle_copy["thesis"]
    evidence = angle_copy["evidence"]
    confirm = angle_copy["confirmation"]
    failure = angle_copy["failure"]
    header = f"{ticker} · {direction_label} · {angle.title}"

    if style.id == "market_note":
        return "\n\n".join((header, human_prefix, h + ".", thesis, evidence, context, level_block, f"Триггер: {confirm}", risk_block, cta))

    if style.id == "numbers_first":
        return "\n\n".join((
            f"{ticker}: цифры перед решением ({direction_label})",
            f"Цена {metrics['price']} USDT | 1H {metrics['change_1h']} | 4H {metrics['change_4h']} | объём {metrics['volume']} | RSI {metrics['rsi']} | ADX {metrics['adx']}",
            level_block,
            f"Интерпретация данных: {thesis}",
            f"Проверка движения: {confirm}",
            context,
            risk_block,
            cta,
        ))

    if style.id == "scenario_tree":
        return "\n\n".join((
            f"{ticker} — развилка для {direction_label}",
            h + ".",
            f"Ветка A / продолжение\nЕсли {_inline_clause(confirm)}, работаем по указанному маршруту.",
            f"Ветка B / отказ\nЕсли {_inline_clause(failure)}, вход не исполняется либо сценарий закрывается.",
            f"Основание развилки: {evidence}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "checklist":
        return "\n\n".join((
            f"Чек-лист {ticker} перед {direction_label}",
            f"□ Направление подтверждено структурой\n□ Главный фактор: {angle.title.lower()}\n□ Метрики: {evidence}\n□ Триггер: {confirm}\n□ Контраргумент: {failure}",
            level_block,
            context,
            risk_block,
            personal_note,
            cta,
        ))

    if style.id == "level_focus":
        return "\n\n".join((
            f"{ticker}: сделку решает одна зона",
            h + ".",
            f"Почему смотрю именно сюда: {thesis}",
            f"Что должна сделать цена у границы: {confirm}",
            f"Что будет считаться отказом: {failure}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "thesis":
        return "\n\n".join((
            f"Торговый тезис по {ticker} — {direction_label}",
            thesis,
            f"Аргумент №1: {evidence}\nАргумент №2: {confirm}\nПроверка тезиса: {context}",
            f"Сильный контраргумент: {failure}",
            level_block,
            risk_block,
            cta,
        ))

    if style.id == "risk_first":
        return "\n\n".join((
            f"{ticker} {direction_label}: сначала понять, где мы неправы",
            risk_block,
            f"Только после этого — идея: {_inline_clause(h)}.",
            thesis,
            f"Фактическая база: {evidence}",
            level_block,
            context,
            personal_note,
            cta,
        ))

    if style.id == "compact_brief":
        return "\n\n".join((
            header,
            f"Суть — {_inline_clause(h)}. {thesis}",
            f"Контроль — {confirm} Риск — {failure}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "trader_diary":
        return "\n\n".join((
            f"Запись в журнале: {ticker}, идея {direction_label}",
            f"Что заметил. {h}. {thesis}",
            f"Почему не вхожу вслепую. {confirm}",
            f"Что заставит признать ошибку. {failure}",
            f"Цифры, которые записываю: {evidence}",
            level_block,
            context,
            risk_block,
            personal_note,
            cta,
        ))

    if style.id == "morning_scan":
        return "\n\n".join((
            f"Утренний скан: {ticker} готовит {direction_label}-сценарий",
            f"Что попало в отбор: {_inline_clause(h)}.",
            f"На старте дня вижу следующее: {evidence}",
            f"До сделки не хватает одного действия цены — {_inline_clause(confirm)}",
            level_block,
            context,
            f"Сценарий снимается с наблюдения, если {_inline_clause(failure)}",
            risk_block,
            cta,
        ))

    if style.id == "evening_review":
        return "\n\n".join((
            f"К вечерней сессии: {ticker} / {direction_label}",
            f"За день цена пришла к ситуации: {_inline_clause(h)}.",
            thesis,
            f"Что подтверждают показатели к текущему моменту: {evidence}",
            context,
            level_block,
            f"На следующей реакции отслеживаю: {confirm}",
            risk_block,
            cta,
        ))

    if style.id == "community_question":
        return "\n\n".join((
            f"Вопрос по {ticker}: вы тоже читаете это как {direction_label}?",
            f"Моё основание: {_inline_clause(h)}. {thesis}",
            f"За сценарий говорят: {evidence}",
            f"Против сценария: {failure}",
            context,
            level_block,
            risk_block,
            personal_note,
            cta,
        ))

    if style.id == "battle_plan":
        return "\n\n".join((
            f"План действий по {ticker} ({direction_label})",
            f"До входа: {confirm}",
            f"После входа: соблюдаем маршрут целей без расширения риска.",
            f"Если рынок идёт против: {failure}",
            f"Почему этот план появился: {h}. {evidence}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "price_story":
        return "\n\n".join((
            f"История движения {ticker}: от импульса к торговому плану",
            f"Сначала рынок показал следующее: {_inline_clause(h)}.",
            f"Затем появились подтверждающие детали: {evidence}",
            f"Теперь цена должна пройти следующую проверку: {_inline_clause(confirm)}",
            f"У истории есть и другой финал: {failure}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "calm_analysis":
        return "\n\n".join((
            f"Спокойный разбор {ticker} без погони за свечой",
            human_prefix,
            thesis,
            f"Наблюдаемые данные: {evidence}",
            f"Торопиться нет необходимости. Достаточно дождаться условия: {_inline_clause(confirm)}",
            context,
            level_block,
            risk_block,
            personal_note,
            cta,
        ))

    if style.id == "signal_card":
        return "\n\n".join((
            f"СИГНАЛ-КАРТА | {ticker} | {direction_label}",
            f"Основа: {angle.title}\nСостояние: ожидание подтверждения\nЦена: {metrics['price']} USDT\nОбъём: {metrics['volume']}\nRSI / ADX: {metrics['rsi']} / {metrics['adx']}",
            f"Причина включения в список: {thesis}",
            f"Триггер активации: {confirm}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "decision_matrix":
        return "\n\n".join((
            f"Матрица решения: {ticker} {direction_label}",
            f"ЗА\n+ {h}\n+ {evidence}\n+ {confirm}",
            f"ПРОТИВ\n− {failure}\n− вход без реакции на уровень\n− расширение риска после открытия",
            f"Вывод: идея допустима только при перевесе блока «ЗА» после подтверждения.",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "execution_protocol":
        return "\n\n".join((
            f"Протокол исполнения {ticker} / {direction_label}",
            f"Шаг 1. Зафиксировать наблюдение: {_inline_clause(h)}.\nШаг 2. Проверить: {evidence}\nШаг 3. Дождаться: {confirm}\nШаг 4. Не исполнять либо выйти, если: {failure}",
            level_block,
            context,
            risk_block,
            personal_note,
            cta,
        ))

    if style.id == "conditional_setup":
        return "\n\n".join((
            f"{ticker}: не прогноз, а условие для {direction_label}",
            f"ЕСЛИ цена выполнит условие — {_inline_clause(confirm)}, ТО сценарий получает право на исполнение.",
            f"ПОТОМ отслеживаем маршрут целей из плана.",
            f"ЕСЛИ вместо этого произойдёт следующее — {_inline_clause(failure)}, ТО идея закрывается.",
            f"Почему вообще наблюдаем: {thesis} {evidence}",
            context,
            level_block,
            risk_block,
            cta,
        ))

    if style.id == "indicator_microscope":
        return "\n\n".join((
            f"Под микроскопом: {ticker} и {angle.short_label}",
            f"RSI {metrics['rsi']} | ADX {metrics['adx']} | объём {metrics['volume']} | ATR {metrics['atr_pct']} | VWAP {metrics['vwap']}",
            f"Что означает связка, а не отдельный индикатор: {thesis}",
            f"Практическая проверка: {confirm}",
            f"Слабое место показаний: {failure}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "market_letter":
        return "\n\n".join((
            f"Письмо с рынка: сегодня в фокусе {ticker}",
            f"Картина выглядит так. {h}. {thesis}",
            f"Но отправлять ордер раньше времени не стоит: {confirm}",
            f"Рынок имеет право доказать обратное — {failure}",
            f"Под письмом оставляю сухие данные: {evidence}",
            level_block,
            context,
            risk_block,
            personal_note,
            cta,
        ))

    if style.id == "voice_note":
        return "\n\n".join((
            f"Коротко голосом про {ticker}",
            f"Смотрите, что здесь происходит: {_inline_clause(h)}. Не сама свеча важна — важна реакция после неё.",
            f"По цифрам имеем {evidence}",
            f"Я бы рассматривал {direction_label} только после следующего: {_inline_clause(confirm)}",
            level_block,
            f"А вот здесь уже без вариантов: {failure}",
            risk_block,
            context,
            cta,
        ))

    if style.id == "red_team":
        return "\n\n".join((
            f"Пытаюсь сломать идею по {ticker}, прежде чем открыть {direction_label}",
            f"Главный аргумент против: {failure}",
            f"Почему идея всё же остаётся на столе: {thesis}",
            f"Факты в её пользу: {evidence}",
            f"Что должно пережить проверку контраргументом: {confirm}",
            context,
            level_block,
            risk_block,
            cta,
        ))

    if style.id == "three_gates":
        return "\n\n".join((
            f"Три допуска к сделке: {ticker} {direction_label}",
            f"Ворота 1 — структура\n{thesis}\n\nВорота 2 — подтверждение\n{confirm}\n\nВорота 3 — рынок вокруг\n{context}",
            f"Если хотя бы одни ворота закрыты, сделка не исполняется. Техническая причина отказа: {failure}",
            f"Контрольные показатели: {evidence}",
            level_block,
            risk_block,
            cta,
        ))

    if style.id == "trigger_watch":
        return "\n\n".join((
            f"{ticker} в листе ожидания — триггер для {direction_label} ещё впереди",
            f"Почему монета попала в наблюдение: {_inline_clause(h)}. {evidence}",
            f"Что активирует идею: {confirm}",
            f"Что удалит её из списка: {failure}",
            level_block,
            context,
            risk_block,
            personal_note,
            cta,
        ))

    if style.id == "range_map":
        return "\n\n".join((
            f"Карта диапазона {ticker}",
            f"Нижняя граница: {metrics['support']} USDT\nТекущая цена: {metrics['price']} USDT\nВерхняя граница: {metrics['resistance']} USDT\nРабочее направление: {direction_label}",
            f"Что происходит у края карты: {_inline_clause(h)}.",
            f"Переход в рабочий сценарий: {confirm}\nВозврат в нейтральную зону: {failure}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "momentum_audit":
        return "\n\n".join((
            f"Аудит импульса {ticker} — можно ли доверять {direction_label}?",
            f"Скорость: 1H {metrics['change_1h']}, 4H {metrics['change_4h']}\nСила: ADX {metrics['adx']}\nУчастие объёма: {metrics['volume']}\nПоложение RSI: {metrics['rsi']}",
            f"Итог аудита: {thesis}",
            f"Контрольная проверка: {confirm}",
            f"Причина признать импульс поглощённым: {failure}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    if style.id == "btc_lens":
        return "\n\n".join((
            f"{ticker} через призму общего рынка",
            context,
            f"На самой монете: {_inline_clause(h)}.",
            f"Локальная логика {direction_label}: {thesis}",
            f"Совпадение с фоном нужно подтвердить действием цены: {confirm}",
            f"Если фон и монета разойдутся: {failure}",
            level_block,
            risk_block,
            cta,
        ))

    if style.id == "risk_memo":
        return "\n\n".join((
            f"Риск-мемо по позиции {ticker} / {direction_label}",
            f"Зачем сделка рассматривается: {thesis}",
            f"Наблюдаемая опора: {evidence}",
            f"Условие допуска: {confirm}",
            f"Основной технический риск: {failure}",
            level_block,
            risk_block,
            context,
            f"Решение по размеру позиции принимается отдельно от привлекательности TP3. {personal_note}",
            cta,
        ))

    if style.id == "terminal_feed":
        return "\n\n".join((
            f"[{ticker}] SIGNAL={direction_label} | MODEL={angle.short_label.upper()}",
            f"PRICE={metrics['price']} | RSI={metrics['rsi']} | ADX={metrics['adx']} | VOL={metrics['volume']} | 1H={metrics['change_1h']}",
            f"WHY: {thesis}",
            f"TRIGGER: {confirm}",
            f"FAIL: {failure}",
            level_block,
            context,
            risk_block,
            cta,
        ))

    raise ValueError(f"Unknown post style: {style.id}")


# ---------------------------------------------------------------------------
# Optional AI polish
# ---------------------------------------------------------------------------
def _clean_ai_text(text: str) -> str:
    cleaned = text.strip()
    # Unwrap an accidental Markdown fence without deleting the JSON inside it.
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _polish_with_ai(text: str, basic: str, levels: Dict[str, float], style: PostStyle, angle: SignalAngle) -> str:
    if not MISTRAL_API:
        return text
    required = "\n".join(
        (
            f"Вход: {_fmt_price(levels['entry'])} USDT",
            f"TP1: {_fmt_price(levels['tp1'])} USDT",
            f"TP2: {_fmt_price(levels['tp2'])} USDT",
            f"TP3: {_fmt_price(levels['tp3'])} USDT",
            f"Стоп: {_fmt_price(levels['stop'])} USDT",
            f"R/R: {levels['risk_reward']:.2f}",
        )
    )
    prompt = f"""
Отредактируй пост для Binance Square на русском языке.
Сохрани композицию «{style.title}» и тему «{angle.title}». Не перестраивай текст в стандартную схему
«тезис — уровни — риск — вопрос» и не повторяй одинаковые вводные из типичных криптопостов.
Не добавляй новости, китов, инсайды, гарантии и вероятность прибыли.
Сохрани тикер ${basic.upper()}, направление, вопрос аудитории и все числа ТОЧНО.
Максимум 2 эмодзи. Не добавляй хэштеги.

Обязательные значения должны остаться в тексте:
{required}

Пост:
{text}
""".strip()
    response = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {MISTRAL_API}", "Content-Type": "application/json"},
        json={
            "model": os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.82,
            "max_tokens": 850,
        },
        timeout=45,
    )
    response.raise_for_status()
    polished = _clean_ai_text(response.json()["choices"][0]["message"]["content"])
    if not _contains_required_content(polished, levels):
        raise ValueError("AI response lost mandatory trade levels")
    if f"${basic.upper()}" not in polished.upper():
        raise ValueError("AI response lost the ticker")
    return polished


# ---------------------------------------------------------------------------
# Public generation API
# ---------------------------------------------------------------------------
def generate_post_draft(
    *,
    symbol: str,
    basic: str,
    mtf,
    score,
    memory: Optional[PostMemory] = None,
    levels: Optional[Dict[str, float]] = None,
    btc=None,
    variant_index: int = 0,
) -> GeneratedPost:
    del symbol
    ind = mtf.tf_15m
    if ind is None:
        raise ValueError("15m indicators are required")

    direction = score.direction
    direction_label = "LONG" if direction == "long" else "SHORT"
    levels = dict(levels or _levels(ind, direction))
    levels.setdefault("risk_reward", score.risk_reward)

    recent_signal_types = memory.get_last_signal_types(24) if memory else []
    recent_styles = memory.get_last_post_styles(40) if memory else []
    angle = choose_signal_angle(ind, direction, mtf, recent_signal_types, variant_index)
    style = choose_post_style(recent_styles, variant_index)

    used_ctas = memory.get_last_ctas(40) if memory else []
    cta = _pick_unused(CTA_VARIANTS, used_ctas)
    plan_title = PLAN_TITLES[(variant_index * 7 + len(style.id)) % len(PLAN_TITLES)]
    human_prefix = HUMAN_HOOKS[(variant_index * 5 + len(angle.id)) % len(HUMAN_HOOKS)]
    personal_note = PERSONAL_PHRASES[(variant_index * 3 + len(style.id)) % len(PERSONAL_PHRASES)]

    angle_copy = _angle_content(angle, ind, direction, mtf)
    metrics = _market_metrics(ind)
    context = _context_block(mtf, btc, direction, variant_index)
    level_block = _level_block(levels, plan_title, style, variant_index)
    risk_block = _risk_block(ind, direction, levels, style, variant_index)

    post = _render_style(
        style=style,
        angle=angle,
        ticker=_format_ticker(basic),
        direction_label=direction_label,
        angle_copy=angle_copy,
        metrics=metrics,
        context=context,
        level_block=level_block,
        risk_block=risk_block,
        cta=cta,
        personal_note=personal_note,
        human_prefix=human_prefix,
    )
    post = re.sub(r"[ \t]+\n", "\n", post).strip()

    if ENABLE_AI_POLISH and MISTRAL_API:
        try:
            polished = _polish_with_ai(post, basic, levels, style, angle)
            if len(polished) <= POST_MAX_CHARS:
                post = polished
        except Exception as exc:
            logger.warning("AI polish rejected; using deterministic text: %s", exc)

    hashtags = varied_hashtags(basic, direction, variant_index)
    full_post = _fix_ticker_spacing(f"{post}\n\n{hashtags}").strip()

    if len(full_post) > POST_MAX_CHARS:
        compact_style = next(item for item in POST_STYLES if item.id == "compact_brief")
        level_block = _level_block(levels, plan_title, compact_style, variant_index)
        post = _render_style(
            style=compact_style,
            angle=angle,
            ticker=_format_ticker(basic),
            direction_label=direction_label,
            angle_copy=angle_copy,
            metrics=metrics,
            context=context,
            level_block=level_block,
            risk_block=risk_block,
            cta=cta,
            personal_note=personal_note,
            human_prefix=human_prefix,
        )
        full_post = _fix_ticker_spacing(f"{post}\n\n{hashtags}").strip()
        style = compact_style

    if len(full_post) > POST_MAX_CHARS:
        raise ValueError(f"Post exceeds POST_MAX_CHARS={POST_MAX_CHARS}")
    if not _contains_required_content(full_post, levels):
        raise ValueError("Generated post is incomplete")

    return GeneratedPost(
        text=full_post,
        style_id=style.id,
        signal_type=angle.id,
        angle_title=angle.title,
    )


def generate_post_with_memory(
    *,
    symbol: str,
    basic: str,
    mtf,
    score,
    memory: Optional[PostMemory] = None,
    levels: Optional[Dict[str, float]] = None,
    btc=None,
    variant_index: int = 0,
) -> str:
    """Backward-compatible wrapper returning only text."""
    return generate_post_draft(
        symbol=symbol,
        basic=basic,
        mtf=mtf,
        score=score,
        memory=memory,
        levels=levels,
        btc=btc,
        variant_index=variant_index,
    ).text


# ---------------------------------------------------------------------------
# AI-first short-form generator
# ---------------------------------------------------------------------------
_AI_LAYOUTS = {"setup_first", "levels_first", "trigger_first", "risk_first"}
_AI_FORBIDDEN_PATTERNS = (
    r"\bновост\w*",
    r"\бинсайд\w*",
    r"\bслух\w*",
    r"\bлистинг\w*",
    r"\bпартн[её]рств\w*",
    r"\bкит(?:ы|ов|ам|ами)?\b",
    r"\bмаркетмейкер\w*",
    r"\bгарант\w*",
    r"\bточно\s+(?:выраст|упад|пойд)",
    r"\bбез\s+риска\b",
    r"\bвероятност[ьи]\s+\d",
    r"\bпамп\w*",
    r"\bдамп\w*",
    r"\bинвесторы\s+(?:активно|массово)",
    r"\bтрейдеры\s+(?:активно|массово)",
)
_AI_FIELD_LIMITS = {
    "hook": 110,
    "interpretation": 230,
    "question": 130,
}


def _current_mistral_api_key() -> str:
    return (os.getenv("MISTRAL_API") or os.getenv("MISTRAL_API_KEY") or MISTRAL_API or "").strip()


def _current_content_mode() -> str:
    return os.getenv("CONTENT_MODE", CONTENT_MODE or "deterministic").strip().lower()


def _sanitize_ai_field(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—:;,.\t")
    return text[:limit].rstrip(" -–—:;,.\t")


def _ai_field_is_safe(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower().replace("ё", "е")
    if "$" in text or "#" in text:
        return False
    # All market numbers are inserted by code. Free AI prose must not invent any.
    if re.search(r"\d", text):
        return False
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _AI_FORBIDDEN_PATTERNS):
        return False
    return True


def _translate_btc_bias(value: str) -> str:
    return {
        "bullish": "бычий",
        "bearish": "медвежий",
        "neutral": "нейтральный",
    }.get(str(value).lower(), str(value).lower())


def _fact_catalog(ind, mtf, direction: str, btc, angle: SignalAngle) -> Dict[str, str]:
    bullish = direction == "long"
    above = "выше" if bullish else "ниже"
    ema_relation = "выше" if ind.ema20 > ind.ema50 else "ниже"
    price_ema_relation = "выше" if ind.price > ind.ema20 else "ниже"
    price_vwap_relation = "выше" if ind.price >= ind.vwap else "ниже"
    higher_tf, aligned, total = _higher_tf_context(mtf, direction)

    facts: Dict[str, str] = {
        "setup": f"Сетап: {angle.title.lower()} в направлении {'LONG' if bullish else 'SHORT'}.",
        "trend": (
            f"На 15M EMA20 {ema_relation} EMA50, цена {price_ema_relation} EMA20."
        ),
        "volume": f"Относительный объём: x{ind.volume_relative:.2f}.",
        "momentum": f"RSI {ind.rsi:.1f}, ADX {ind.adx:.1f}.",
        "vwap": f"Цена находится {price_vwap_relation} VWAP {_fmt_price(ind.vwap)}.",
        "changes": f"Динамика: 1H {ind.change_1h:+.2f}%, 4H {ind.change_4h:+.2f}%.",
        "range": (
            f"Рабочий диапазон: {_fmt_price(ind.support)}–{_fmt_price(ind.resistance)}."
        ),
        "mtf": (
            f"Старшие таймфреймы: {aligned}/{total} совпадают с направлением ({higher_tf})."
            if total
            else "Данные старших таймфреймов недоступны."
        ),
        "location": (
            f"Текущая цена {_fmt_price(ind.price)} находится {above} ключевой зоны сценария."
        ),
    }
    if btc is not None:
        facts["btc"] = (
            f"BTC-фон: {_translate_btc_bias(btc.bias)}, 1H {btc.change_1h:+.2f}%, "
            f"4H {btc.change_4h:+.2f}%."
        )
    return facts


def _compact_recent_openings(memory: Optional[PostMemory], limit: int = 8) -> List[str]:
    if memory is None:
        return []
    result: List[str] = []
    for title in memory.get_last_titles(limit * 2):
        compact = PostMemory.normalize_text(title)
        compact = compact.replace("$ticker", "").replace("#", "")
        compact = re.sub(r"\s+", " ", compact).strip()
        if compact and compact not in result:
            result.append(compact[:100])
        if len(result) >= limit:
            break
    return result


def _mistral_prompt(
    *,
    basic: str,
    direction: str,
    fact_catalog: Dict[str, str],
    recent_openings: List[str],
    candidate_count: int,
) -> Tuple[str, str]:
    direction_label = "LONG" if direction == "long" else "SHORT"
    system = (
        "Ты редактор коротких русскоязычных постов о технических торговых сетапах. "
        "Ты не имеешь права использовать внешние знания. Работай только с переданным каталогом фактов. "
        "Не придумывай новости, причины движения, цены, проценты, события, мнения участников рынка, "
        "китов, инсайды, листинги, гарантии или вероятность успеха. "
        "Свободные поля должны быть живыми и человеческими, но без любых цифр, тикеров и хэштегов. "
        "Верни только валидный JSON-объект."
    )
    payload = {
        "task": (
            f"Создай {candidate_count} коротких и заметно разных вариантов подачи сигнала "
            f"для {direction_label} по активу {basic.upper()}."
        ),
        "available_facts": fact_catalog,
        "recent_openings_to_avoid": recent_openings,
        "rules": {
            "use_only_fact_ids_from_available_facts": True,
            "fact_ids_per_candidate": "2-3",
            "allowed_layouts": sorted(_AI_LAYOUTS),
            "hook": "до 90 символов, без цифр, тикера, хэштегов и обещаний",
            "interpretation": (
                "одно-два коротких предложения; объясни, на что смотреть в сетапе, "
                "без новых фактов и без цифр"
            ),
            "question": "один естественный вопрос аудитории, без цифр и без тикера",
            "style": "спокойно, конкретно, без канцелярита, без клише и без рекламного тона",
            "avoid": [
                "сигнал интересный",
                "рынок формирует",
                "не финансовая рекомендация",
                "сценарий выглядит",
                "киты",
                "инсайд",
                "новости",
                "гарантированная прибыль",
            ],
        },
        "json_shape": {
            "candidates": [
                {
                    "layout": "setup_first",
                    "hook": "строка",
                    "interpretation": "строка",
                    "question": "строка с вопросительным знаком",
                    "fact_ids": ["setup", "volume"],
                }
            ]
        },
    }
    return system, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _request_mistral_candidates(
    *,
    basic: str,
    direction: str,
    fact_catalog: Dict[str, str],
    recent_openings: List[str],
    candidate_count: int,
) -> List[Dict[str, Any]]:
    api_key = _current_mistral_api_key()
    if not api_key:
        return []

    system, user = _mistral_prompt(
        basic=basic,
        direction=direction,
        fact_catalog=fact_catalog,
        recent_openings=recent_openings,
        candidate_count=candidate_count,
    )
    body = {
        "model": os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": AI_TEMPERATURE,
        "presence_penalty": 0.20,
        "frequency_penalty": 0.20,
        "max_tokens": 1600,
    }

    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            if attempt:
                body["temperature"] = min(0.35, AI_TEMPERATURE)
            response = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=AI_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text", "")) if isinstance(item, dict) else str(item)
                    for item in content
                )
            parsed = json.loads(_clean_ai_text(str(content)))
            candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else []
            if not isinstance(candidates, list):
                raise ValueError("Mistral response has no candidates array")
            return [item for item in candidates if isinstance(item, dict)]
        except Exception as exc:
            last_error = exc
            logger.warning("Mistral generation attempt %s failed: %s", attempt + 1, exc)
    if last_error:
        raise RuntimeError(f"Mistral generation failed: {last_error}")
    return []


def _parse_ai_candidate(
    raw: Dict[str, Any],
    *,
    fact_catalog: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    layout = str(raw.get("layout", "")).strip().lower()
    if layout not in _AI_LAYOUTS:
        return None

    hook = _sanitize_ai_field(raw.get("hook"), _AI_FIELD_LIMITS["hook"])
    interpretation = _sanitize_ai_field(
        raw.get("interpretation"), _AI_FIELD_LIMITS["interpretation"]
    )
    question = _sanitize_ai_field(raw.get("question"), _AI_FIELD_LIMITS["question"])
    if question and not question.endswith("?"):
        question += "?"

    if not all(_ai_field_is_safe(item) for item in (hook, interpretation, question)):
        return None

    fact_ids_raw = raw.get("fact_ids", [])
    if not isinstance(fact_ids_raw, list):
        return None
    fact_ids: List[str] = []
    for value in fact_ids_raw:
        item = str(value).strip()
        if item in fact_catalog and item not in fact_ids:
            fact_ids.append(item)
    if not 2 <= len(fact_ids) <= 3:
        return None

    return {
        "layout": layout,
        "hook": hook,
        "interpretation": interpretation,
        "question": question,
        "fact_ids": fact_ids,
    }


def _ai_hashtag_line(angle: SignalAngle, direction: str) -> str:
    angle_tag = {
        "breakout": "#PriceAction",
        "liquidity_reclaim": "#Liquidity",
        "pullback": "#TrendTrading",
        "trend_continuation": "#TrendTrading",
        "volume_impulse": "#VolumeAnalysis",
        "mtf_alignment": "#TechnicalAnalysis",
        "vwap_control": "#TechnicalAnalysis",
        "range_edge": "#PriceAction",
        "momentum": "#Momentum",
        "volatility_expansion": "#Volatility",
    }.get(angle.id, "#TechnicalAnalysis")
    direction_tag = "#Long" if direction == "long" else "#Short"
    return f"{angle_tag} {direction_tag}"


def _render_ai_post(
    *,
    basic: str,
    direction: str,
    levels: Dict[str, float],
    angle: SignalAngle,
    angle_copy: Dict[str, str],
    fact_catalog: Dict[str, str],
    candidate: Dict[str, Any],
) -> str:
    direction_label = "LONG" if direction == "long" else "SHORT"
    ticker = _format_ticker(basic)
    facts = " ".join(fact_catalog[item] for item in candidate["fact_ids"])
    entry = _fmt_price(levels["entry"])
    targets = " / ".join(
        _fmt_price(levels[key]) for key in ("tp1", "tp2", "tp3")
    )
    stop = _fmt_price(levels["stop"])
    rr = f"{levels['risk_reward']:.2f}"
    levels_block = (
        f"Вход: {entry} USDT\n"
        f"Цели: {targets} USDT\n"
        f"Стоп-лосс: {stop} USDT\n"
        f"R/R: {rr}"
    )
    trigger = _clause(angle_copy["confirmation"])
    invalidation = "Закрепление за стоп-уровнем отменяет идею; без усреднения."
    header = f"{ticker} — {direction_label}: {candidate['hook']}"
    analysis = candidate["interpretation"]
    question = candidate["question"]
    tags = _ai_hashtag_line(angle, direction)

    if candidate["layout"] == "levels_first":
        blocks = (
            header,
            levels_block,
            facts,
            analysis,
            f"Триггер: {trigger}.",
            f"Отмена: {invalidation}",
            question,
            tags,
        )
    elif candidate["layout"] == "trigger_first":
        blocks = (
            header,
            f"Триггер: {trigger}.",
            facts,
            analysis,
            levels_block,
            f"Отмена: {invalidation}",
            question,
            tags,
        )
    elif candidate["layout"] == "risk_first":
        blocks = (
            header,
            f"Сначала риск: {invalidation}",
            facts,
            levels_block,
            analysis,
            f"Триггер: {trigger}.",
            question,
            tags,
        )
    else:
        blocks = (
            header,
            facts,
            analysis,
            levels_block,
            f"Триггер: {trigger}.",
            f"Отмена: {invalidation}",
            question,
            tags,
        )

    post = "\n\n".join(part.strip() for part in blocks if str(part).strip())
    post = _fix_ticker_spacing(re.sub(r"[ \t]+\n", "\n", post).strip())
    return post


def _ticker_count(text: str, basic: str) -> int:
    pattern = rf"(?<![A-Za-z0-9_])\${re.escape(str(basic).upper())}(?![A-Za-z0-9_])"
    return len(re.findall(pattern, text.upper()))


def _numeric_tokens(text: str) -> List[str]:
    return re.findall(r"(?<![A-Za-zА-Яа-я])[-+]?\d+(?:[.,]\d+)?", text)


def _allowed_numeric_tokens(
    *,
    levels: Dict[str, float],
    fact_catalog: Dict[str, str],
    trigger: str,
) -> set[str]:
    source = " ".join(
        list(fact_catalog.values())
        + [trigger]
        + [_fmt_price(levels[key]) for key in ("entry", "tp1", "tp2", "tp3", "stop")]
        + [f"{levels['risk_reward']:.2f}"]
    )
    allowed = {token.replace(",", ".") for token in _numeric_tokens(source)}
    # Structural labels are also deterministic and safe.
    allowed.update({"1", "2", "3", "4", "15", "20", "50", "200"})
    return allowed


def _validate_ai_post_contract(
    text: str,
    *,
    basic: str,
    direction: str,
    levels: Dict[str, float],
    fact_catalog: Dict[str, str],
    trigger: str,
) -> Tuple[bool, Tuple[str, ...]]:
    reasons: List[str] = []
    ticker_mentions = _ticker_count(text, basic)
    if not 1 <= ticker_mentions <= 3:
        reasons.append(f"ticker mentions {ticker_mentions}, expected 1-3")

    lowered = text.lower().replace("ё", "е")
    for marker in ("вход:", "цели:", "стоп-лосс:"):
        if marker not in lowered:
            reasons.append(f"missing {marker}")

    for key in ("entry", "tp1", "tp2", "tp3", "stop"):
        value = _fmt_price(levels[key])
        if value not in text:
            reasons.append(f"missing {key} value {value}")
    rr = f"{levels['risk_reward']:.2f}"
    if rr not in text:
        reasons.append(f"missing R/R {rr}")

    direction_terms = ("LONG", "ЛОНГ") if direction == "long" else ("SHORT", "ШОРТ")
    if not any(term in text.upper() for term in direction_terms):
        reasons.append("missing direction")

    if text.count("?") != 1:
        reasons.append("expected exactly one audience question")
    if len(re.findall(r"#[A-Za-zА-Яа-я0-9_]+", text)) > 2:
        reasons.append("too many hashtags")
    if len(text) > POST_MAX_CHARS:
        reasons.append(f"post too long {len(text)}")
    if len(text) < 260:
        reasons.append(f"post too short {len(text)}")

    for pattern in _AI_FORBIDDEN_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            reasons.append(f"forbidden claim pattern {pattern}")

    allowed_numbers = _allowed_numeric_tokens(
        levels=levels,
        fact_catalog=fact_catalog,
        trigger=trigger,
    )
    unexpected = sorted(
        {
            token
            for token in (item.replace(",", ".") for item in _numeric_tokens(text))
            if token not in allowed_numbers
        }
    )
    if unexpected:
        reasons.append("unexpected numeric facts: " + ", ".join(unexpected))

    return not reasons, tuple(reasons)


def _generate_ai_posts(
    *,
    basic: str,
    mtf,
    score,
    levels: Dict[str, float],
    memory: Optional[PostMemory],
    btc,
    candidate_count: int,
) -> List[GeneratedPost]:
    ind = mtf.tf_15m
    if ind is None:
        return []
    direction = score.direction
    recent_signal_types = memory.get_last_signal_types(24) if memory else []
    angle = choose_signal_angle(ind, direction, mtf, recent_signal_types, 0)
    angle_copy = _angle_content(angle, ind, direction, mtf)
    facts = _fact_catalog(ind, mtf, direction, btc, angle)
    raw_candidates = _request_mistral_candidates(
        basic=basic,
        direction=direction,
        fact_catalog=facts,
        recent_openings=_compact_recent_openings(memory),
        candidate_count=candidate_count,
    )

    posts: List[GeneratedPost] = []
    seen: set[str] = set()
    for raw in raw_candidates:
        parsed = _parse_ai_candidate(raw, fact_catalog=facts)
        if parsed is None:
            continue
        post = _render_ai_post(
            basic=basic,
            direction=direction,
            levels=levels,
            angle=angle,
            angle_copy=angle_copy,
            fact_catalog=facts,
            candidate=parsed,
        )
        valid, reasons = _validate_ai_post_contract(
            post,
            basic=basic,
            direction=direction,
            levels=levels,
            fact_catalog=facts,
            trigger=angle_copy["confirmation"],
        )
        signature = PostMemory.normalize_text(post)
        if not valid:
            logger.warning("Rejected unsafe AI post: %s", "; ".join(reasons))
            continue
        if signature in seen:
            continue
        seen.add(signature)
        posts.append(
            GeneratedPost(
                text=post,
                style_id=f"ai_{parsed['layout']}",
                signal_type=angle.id,
                angle_title=angle.title,
            )
        )
        if len(posts) >= candidate_count:
            break
    return posts


def generate_post_candidates(
    *,
    symbol: str,
    basic: str,
    mtf,
    score,
    memory: Optional[PostMemory] = None,
    levels: Optional[Dict[str, float]] = None,
    btc=None,
    variant_count: int = 8,
) -> List[GeneratedPost]:
    """Generate a small batch of fact-locked posts, preferring one Mistral call.

    Mistral writes the hook, interpretation and question. All market facts, prices,
    levels, direction, ticker count and risk wording remain code-controlled.
    Invalid or unavailable AI output automatically falls back to deterministic posts.
    """
    ind = mtf.tf_15m
    if ind is None:
        raise ValueError("15m indicators are required")
    levels = dict(levels or _levels(ind, score.direction))
    levels.setdefault("risk_reward", score.risk_reward)
    requested = max(2, min(int(variant_count), 16))

    posts: List[GeneratedPost] = []
    mode = _current_content_mode()
    api_key = _current_mistral_api_key()
    if mode in {"ai", "ai_first", "mistral"} and api_key:
        try:
            posts = _generate_ai_posts(
                basic=basic,
                mtf=mtf,
                score=score,
                levels=levels,
                memory=memory,
                btc=btc,
                candidate_count=min(AI_VARIANTS, requested),
            )
            logger.info("Mistral produced %s valid fact-locked candidates", len(posts))
        except Exception as exc:
            logger.warning("AI-first generation unavailable; deterministic fallback: %s", exc)

    if len(posts) >= AI_MIN_VALID:
        return posts

    # Supplement or replace failed AI output. The bot must never stop publishing
    # only because the external model is temporarily unavailable.
    existing_signatures = {PostMemory.normalize_text(item.text) for item in posts}
    fallback_target = max(4, requested)
    fallback_posts = _generate_compact_fallback_posts(
        basic=basic,
        mtf=mtf,
        score=score,
        levels=levels,
        memory=memory,
        btc=btc,
        candidate_count=fallback_target,
    )
    for draft in fallback_posts:
        signature = PostMemory.normalize_text(draft.text)
        if signature in existing_signatures:
            continue
        existing_signatures.add(signature)
        posts.append(draft)
        if len(posts) >= fallback_target:
            break
    return posts


def _generate_compact_fallback_posts(
    *,
    basic: str,
    mtf,
    score,
    levels: Dict[str, float],
    memory: Optional[PostMemory],
    btc,
    candidate_count: int,
) -> List[GeneratedPost]:
    """Offline-safe concise posts used only when Mistral is unavailable."""
    ind = mtf.tf_15m
    if ind is None:
        return []
    direction = score.direction
    recent_signal_types = memory.get_last_signal_types(24) if memory else []
    angles = detect_signal_angles(ind, direction, mtf)
    hooks = (
        "Импульс есть, но решает реакция на уровне",
        "Здесь важнее подтверждение, чем скорость движения",
        "План понятен: вход только после реакции цены",
        "Сетап без догоняния свечи и лишнего риска",
        "Цена подошла к зоне, где станет понятен следующий шаг",
        "Сначала условия сделки, потом эмоции",
        "Точка входа есть, но рынок должен её подтвердить",
        "Сильный график не отменяет дисциплину",
    )
    interpretations = (
        "Не хочу догонять движение. Важнее увидеть, что цена принимает рабочую зону и не возвращается обратно.",
        "Сам сигнал уже виден, но качество сделки определит реакция после входа, а не красивая свеча до него.",
        "Здесь логичнее ждать исполнения условия, чем угадывать продолжение заранее.",
        "Преимущество сохраняется, пока структура не ломается и риск остаётся заранее ограниченным.",
        "Сделка имеет смысл только по плану: без подтверждения лучше оставить её наблюдением.",
        "Главная задача — не пропустить движение, а не войти в момент, когда соотношение риска уже испорчено.",
        "Уровни дают понятную развилку: либо рынок подтверждает идею, либо вход отменяется.",
        "Этот сетап стоит оценивать по реакции цены, а не по уверенности в направлении.",
    )
    questions = (
        "Вы бы ждали ретест или работали сразу после подтверждения?",
        "Для вас здесь важнее объём или удержание уровня?",
        "Такой вход вы бы исполняли лимитно или после реакции?",
        "Где для вас проходит граница между сигналом и погоней за ценой?",
        "Вы бы фиксировали часть позиции на первой цели?",
        "Какое подтверждение для вас здесь обязательное?",
        "Считаете этот план достаточно чистым для входа?",
        "Что могло бы заставить вас пропустить эту сделку?",
    )
    layouts = ("setup_first", "levels_first", "trigger_first", "risk_first")
    posts: List[GeneratedPost] = []
    for index in range(max(2, candidate_count)):
        angle = angles[index % len(angles)] if angles else choose_signal_angle(
            ind, direction, mtf, recent_signal_types, index
        )
        angle_copy = _angle_content(angle, ind, direction, mtf)
        facts = _fact_catalog(ind, mtf, direction, btc, angle)
        fact_priority = [
            "setup",
            "trend",
            "volume",
            "mtf",
            "momentum",
            "vwap",
            "changes",
            "range",
            "btc",
        ]
        available = [key for key in fact_priority if key in facts]
        shift = index % max(1, len(available))
        rotated = available[shift:] + available[:shift]
        selected = []
        for key in ("setup", *rotated):
            if key in facts and key not in selected:
                selected.append(key)
            if len(selected) >= 3:
                break
        candidate = {
            "layout": layouts[index % len(layouts)],
            "hook": hooks[index % len(hooks)],
            "interpretation": interpretations[(index * 3) % len(interpretations)],
            "question": questions[(index * 5) % len(questions)],
            "fact_ids": selected,
        }
        post = _render_ai_post(
            basic=basic,
            direction=direction,
            levels=levels,
            angle=angle,
            angle_copy=angle_copy,
            fact_catalog=facts,
            candidate=candidate,
        )
        valid, reasons = _validate_ai_post_contract(
            post,
            basic=basic,
            direction=direction,
            levels=levels,
            fact_catalog=facts,
            trigger=angle_copy["confirmation"],
        )
        if not valid:
            logger.debug("Compact fallback rejected: %s", "; ".join(reasons))
            continue
        posts.append(
            GeneratedPost(
                text=post,
                style_id=f"fallback_{candidate['layout']}",
                signal_type=angle.id,
                angle_title=angle.title,
            )
        )
        if len(posts) >= candidate_count:
            break
    return posts
