"""Fact-based Binance Square post generator with meaningful content diversity."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import random
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from content_variation import (
    CTA_VARIANTS,
    PLAN_TITLES,
    POST_STYLES,
    PostStyle,
    SignalAngle,
    choose,
    choose_post_style,
    choose_signal_angle,
    hashtags as varied_hashtags,
    HUMAN_HOOKS,
    PERSONAL_PHRASES,
    RISK_SENTENCES,
    CONTEXT_OPENERS,
)
from indicators import build_trade_levels
from memory import PostMemory
from ai_pipeline import SYSTEM_PROMPT as REACH_SYSTEM_PROMPT
from ai_author import author_post, validate

logger = logging.getLogger(__name__)

MISTRAL_API = os.getenv("MISTRAL_API", "").strip()
ENABLE_AI_POLISH = os.getenv("ENABLE_AI_POLISH", "0").strip().lower() in {"1", "true", "yes"}
AI_VARIANTS = int(os.getenv("AI_VARIANTS", "1"))
POST_MAX_CHARS = int(os.getenv("POST_MAX_CHARS", "1600"))


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
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _polish_with_ai(text: str, basic: str, levels: Dict[str, float], style: PostStyle, angle: SignalAngle) -> str:
    """Mistral Author Mode: creates the social post instead of polishing a template."""
    if not MISTRAL_API:
        return text

    facts = {
        "ticker": f"${basic.upper()}",
        "direction": angle.title,
        "style": style.title,
        "levels": {
            "entry": _fmt_price(levels["entry"]),
            "tp1": _fmt_price(levels["tp1"]),
            "tp2": _fmt_price(levels["tp2"]),
            "tp3": _fmt_price(levels["tp3"]),
            "stop": _fmt_price(levels["stop"]),
            "rr": f"{levels['risk_reward']:.2f}",
        },
    }

    prompt = f"""
Ты автор криптопостов для Binance Square. Напиши новый пост с нуля от лица живого трейдера.

Не редактируй старый текст. Старый черновик игнорируй — он содержит шаблоны.

Цель:
остановить скролл, вызвать обсуждение и при этом сохранить точность сделки.

Правила:
- первая строка должна быть наблюдением, мнением или конфликтом;
- не начинай с "$COIN LONG/SHORT";
- не используй стиль отчёта;
- не используй слова: "матрица решения", "сверка контекста", "навигация позиции", "допуски", "ворота";
- не придумывай новости и причины движения;
- не меняй ни одной цифры сделки.

Структура:
1. Хук
2. Почему ситуация интересна
3. Короткое объяснение
4. Сценарий
5. Что отменит идею
6. Вопрос аудитории

Данные:
{json.dumps(facts, ensure_ascii=False, indent=2)}

Старый черновик для справки:
{text}
""".strip()

    try:
        response = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_API}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
                "temperature": 0.85,
                "messages": [
                    {"role": "system", "content": "Ты пишешь естественные криптопосты для социальной сети."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=45,
        )
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"].strip()
        return _fix_ticker_spacing(result)
    except Exception:
        logger.exception("Mistral author mode failed")
        return text

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

    author_data = {
        "symbol": basic,
        "direction": direction_label,
        "entry": levels.get("entry"),
        "stop": levels.get("stop"),
        "tp1": levels.get("tp1"),
        "tp2": levels.get("tp2"),
        "tp3": levels.get("tp3"),
        "rr": levels.get("risk_reward"),
        "market_context": str(context),
        "metrics": str(metrics)
    }

    post = None
    author_mode_used = False
    if ENABLE_AI_POLISH and MISTRAL_API:
        try:
            candidate = author_post(author_data)
            if validate(candidate, author_data):
                post = candidate
                author_mode_used = True
        except Exception as exc:
            logger.warning("Author mode failed, fallback enabled: %s", exc)

    if post is None:
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

# V7.1: Author Mode output is final after validation. No second AI polish pass.
    hashtags = varied_hashtags(basic, direction, variant_index)
    full_post = _fix_ticker_spacing(f"{post}\n\n{hashtags}").strip()

    if len(full_post) > POST_MAX_CHARS and not author_mode_used:
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
