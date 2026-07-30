"""Content strategy and anti-repetition assets for Binance Square posts.

Variation is built on three independent axes:
1. truthful signal angle — what the market setup is about;
2. editorial layout — how the post is structured;
3. language palette — how facts, risk and execution are phrased.

The generator deliberately avoids relying on synonym swapping alone. The first
30 candidates use 30 different layouts before a layout may repeat.
"""
from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Dict, Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class SignalAngle:
    id: str
    title: str
    short_label: str
    weight: float


@dataclass(frozen=True)
class PostStyle:
    id: str
    title: str
    family: str
    level_format: str
    length: str = "medium"


# Every entry has a materially different composition, not just a new heading.
POST_STYLES: Tuple[PostStyle, ...] = (
    PostStyle("market_note", "Живая рыночная заметка", "note", "vertical"),
    PostStyle("numbers_first", "Разбор через цифры", "data", "compact_grid"),
    PostStyle("scenario_tree", "Два пути рынка", "branches", "inline"),
    PostStyle("checklist", "Проверка перед сделкой", "checklist", "numbered"),
    PostStyle("level_focus", "Охота за уровнем", "level", "route"),
    PostStyle("thesis", "Логика движения", "thesis", "vertical"),
    PostStyle("risk_first", "Сначала защита капитала", "risk", "risk_reward"),
    PostStyle("compact_brief", "Быстрый трейдерский бриф", "brief", "compact_grid", "short"),
    PostStyle("trader_diary", "Запись трейдера", "diary", "notebook"),
    PostStyle("morning_scan", "Утренний скан рынка", "scan", "route"),
    PostStyle("evening_review", "Вечерний обзор", "review", "vertical"),
    PostStyle("community_question", "Обсуждение с комьюнити", "community", "inline"),
    PostStyle("battle_plan", "План действий", "action", "numbered"),
    PostStyle("price_story", "История цены", "story", "notebook"),
    PostStyle("calm_analysis", "Спокойный анализ", "calm", "vertical"),
    PostStyle("signal_card", "Карточка сигнала", "card", "card"),
    PostStyle("decision_matrix", "Матрица решения", "matrix", "risk_reward"),
    PostStyle("execution_protocol", "Протокол исполнения", "protocol", "numbered"),
    PostStyle("conditional_setup", "Условный сценарий", "conditional", "route"),
    PostStyle("indicator_microscope", "Под микроскопом", "microscope", "compact_grid"),
    PostStyle("market_letter", "Письмо с рынка", "letter", "notebook"),
    PostStyle("voice_note", "Голосовая заметка", "voice", "inline"),
    PostStyle("red_team", "Проверка контраргументом", "red_team", "risk_reward"),
    PostStyle("three_gates", "Три допуска к сделке", "gates", "numbered"),
    PostStyle("trigger_watch", "Ждём триггер", "trigger", "route"),
    PostStyle("range_map", "Карта диапазона", "map", "card"),
    PostStyle("momentum_audit", "Аудит импульса", "audit", "compact_grid"),
    PostStyle("btc_lens", "Сигнал через контекст BTC", "btc", "inline"),
    PostStyle("risk_memo", "Риск-мемо", "memo", "risk_reward"),
    PostStyle("terminal_feed", "Терминальная лента", "terminal", "terminal", "short"),
)


PLAN_TITLES = (
    "🎯 План по уровням",
    "📍 Карта сделки",
    "План исполнения",
    "Ключевые цены",
    "Маршрут сценария",
    "Рабочие отметки",
    "Параметры идеи",
    "Ценовой план",
    "Торговая схема",
    "Границы сделки",
    "Что ставим на карту",
    "Навигация по позиции",
)

HUMAN_HOOKS = (
    "Посмотрел структуру движения: здесь интересна реакция, а не сама яркая свеча.",
    "Без попытки угадать рынок — фиксирую только условия, при которых идея остаётся рабочей.",
    "Цена подошла к точке, где ближайший сценарий станет намного понятнее.",
    "Сейчас важнее не направление на глаз, а то, удержится ли ключевая зона.",
    "В этом сетапе нет магии: есть уровень, подтверждение и заранее известная точка ошибки.",
    "Оставлю наблюдение до того, как рынок сам покажет, кто контролирует участок графика.",
    "Не спешу за импульсом: сначала проверяю, есть ли у движения опора.",
    "На графике сложилась понятная развилка, поэтому разложу её без лишнего шума.",
    "Сигнал интересный, но вход имеет смысл только при выполнении конкретного условия.",
    "Рынок дал повод открыть график, но ещё не дал повод забыть о риске.",
    "Смотрю не на красивую свечу, а на то, что цена делает после неё.",
    "Здесь можно построить план без прогнозов в стиле «точно пойдёт».",
)

PERSONAL_PHRASES = (
    "Я бы не торопился до реакции на отмеченной границе.",
    "Для меня решающий момент — сохранение структуры после первого теста.",
    "Лично я предпочту пропустить вход, если подтверждение окажется слабым.",
    "Главное здесь — не увеличивать риск только потому, что картинка выглядит убедительно.",
    "Буду считать идею живой, пока рынок не нарушил условие отмены.",
    "Мне важнее качество реакции, чем скорость достижения первой цели.",
    "В таком движении лучше опоздать на подтверждение, чем рано попасть в ложный импульс.",
    "Сам по себе сигнал не повод входить без заранее рассчитанного размера позиции.",
    "Пока наблюдение выглядит чище, чем попытка входить на эмоциях.",
    "Я бы оценивал сделку по исполнению плана, а не по тому, дошла ли цена до TP3.",
)

CTA_VARIANTS = (
    "Вы бы заходили после подтверждения или ждали повторный тест?",
    "Какая отметка для вас окончательно подтверждает этот сценарий?",
    "Что здесь сильнее: продолжение импульса или риск возврата в диапазон?",
    "Этот сетап уже готов к работе или ему не хватает ещё одного сигнала?",
    "Какое подтверждение вы считаете обязательным перед входом?",
    "Где для вас проходит точка, после которой идею нужно без споров закрывать?",
    "Какой из двух сценариев вы бы сделали базовым на ближайшие часы?",
    "Видите аргумент против выбранного направления?",
    "Вы бы сократили позицию на TP1 или держали план без изменений?",
    "Что важнее в этой ситуации: объём, уровень или согласование таймфреймов?",
    "На каком этапе вы бы перевели сделку в безубыток?",
    "Такой вход вы бы исполняли лимитным ордером или только после реакции?",
    "Насколько для вас критичен текущий фон BTC?",
    "Вы бы пропустили сигнал при слабом ретесте?",
    "Какой риск на сделку считаете разумным для такой волатильности?",
    "Есть ли на графике условие, которое я недооценил?",
    "Как вы читаете эту структуру: накопление или подготовка к продолжению?",
    "Где бы вы искали повторный вход, если первая точка уйдёт без нас?",
    "Считаете ли вы текущий R/R достаточным для исполнения?",
    "Какой из уровней здесь важнее самой точки входа?",
)

TAG_GROUPS = (
    ("#TechnicalAnalysis", "#Trading"),
    ("#CryptoTrading", "#MarketUpdate"),
    ("#Altcoins", "#PriceAction"),
    ("#Crypto", "#TradingPlan"),
    ("#MarketAnalysis", "#RiskManagement"),
    ("#TradingSetup", "#CryptoMarket"),
    ("#ChartAnalysis", "#TradeIdea"),
    ("#RiskControl", "#MarketStructure"),
    ("#TradingStrategy", "#CryptoAnalysis"),
    ("#PriceLevels", "#TradeManagement"),
)


RISK_SENTENCES = (
    "Размер позиции считаю от допустимого убытка, а не от уверенности в картинке.",
    "Даже хороший сетап не оправдывает увеличение заранее установленного риска.",
    "Стоп здесь является частью идеи, а не запасным решением после входа.",
    "Если условие отмены выполнено, сценарий закрывается без усреднения.",
    "Риск фиксируется до открытия позиции; переносить границу ошибки дальше нельзя.",
    "План теряет смысл, если после входа менять стоп под эмоции рынка.",
    "Объём позиции должен переживать стоп без ущерба для торгового плана.",
    "Сделка допускается только с заранее понятным денежным риском.",
    "Ни один индикатор не отменяет необходимости ограничить убыток.",
    "Лучше пропустить движение, чем входить с неприемлемым размером стопа.",
)

CONTEXT_OPENERS = (
    "Фон рынка",
    "Что происходит вокруг сигнала",
    "Старший контекст",
    "Фильтр общего рынка",
    "Картина за пределами 15M",
    "Проверка фона",
    "Внешняя среда сделки",
    "Контекст перед исполнением",
)


def choose(items: Sequence[str], used: Iterable[str] | None = None) -> str:
    values = list(items)
    if not values:
        raise ValueError("Cannot choose from an empty sequence")
    used_set = {str(item) for item in (used or []) if item}
    available = [item for item in values if item not in used_set]
    return random.choice(available or values)


def hashtags(symbol: str, direction: str, variant_index: int = 0) -> str:
    direction_tag = "LONG" if direction == "long" else "SHORT"
    group = TAG_GROUPS[variant_index % len(TAG_GROUPS)]
    tags = [f"#{symbol.upper()}", f"#{direction_tag}", *group]
    # Rotate the order to prevent an identical footer fingerprint.
    shift = variant_index % len(tags)
    tags = tags[shift:] + tags[:shift]
    return " ".join(tags)


def _higher_tf_alignment_count(mtf, direction: str) -> Tuple[int, int]:
    aligned = 0
    total = 0
    for indicator in (getattr(mtf, "tf_1h", None), getattr(mtf, "tf_4h", None), getattr(mtf, "tf_1d", None)):
        if indicator is None:
            continue
        total += 1
        is_aligned = indicator.ema20 > indicator.ema50 if direction == "long" else indicator.ema20 < indicator.ema50
        if is_aligned:
            aligned += 1
    return aligned, total


def detect_signal_angles(ind, direction: str, mtf=None) -> List[SignalAngle]:
    """Return truthful, eligible content angles ordered by relevance."""
    angles: List[SignalAngle] = []
    is_long = direction == "long"

    if (is_long and ind.breakout_up) or ((not is_long) and ind.breakout_down):
        angles.append(SignalAngle("breakout", "Пробой ключевой границы", "пробой", 10.0))

    if (is_long and ind.liquidity_sweep_down) or ((not is_long) and ind.liquidity_sweep_up):
        angles.append(SignalAngle("liquidity_reclaim", "Снятие ликвидности и возврат", "свип", 9.5))

    if (is_long and ind.pullback_long) or ((not is_long) and ind.pullback_short):
        angles.append(SignalAngle("pullback", "Откат внутри тренда", "откат", 9.0))

    if (is_long and ind.trend_continuation_long) or ((not is_long) and ind.trend_continuation_short):
        angles.append(SignalAngle("trend_continuation", "Продолжение тренда", "продолжение", 7.2))

    if ind.volume_relative >= 1.35:
        weight = 8.6 if ind.volume_relative >= 1.8 else 7.4
        angles.append(SignalAngle("volume_impulse", "Импульс на повышенном объёме", "объём", weight))

    aligned, total = _higher_tf_alignment_count(mtf, direction) if mtf is not None else (0, 0)
    if total >= 2 and aligned >= 2:
        angles.append(SignalAngle("mtf_alignment", "Согласованность таймфреймов", "MTF", 8.0 + aligned * 0.2))

    vwap_supports = ind.price >= ind.vwap if is_long else ind.price <= ind.vwap
    if vwap_supports:
        distance = abs(ind.price - ind.vwap) / max(ind.atr, 1e-12)
        if distance <= 1.4:
            angles.append(SignalAngle("vwap_control", "Контроль цены относительно VWAP", "VWAP", 6.8))

    key_level = ind.resistance if is_long else ind.support
    level_distance = abs(ind.price - key_level) / max(ind.atr, 1e-12)
    if level_distance <= 1.8:
        angles.append(SignalAngle("range_edge", "Реакция у границы диапазона", "граница", 7.0))

    momentum_supports = (
        (is_long and ind.macd_hist > 0 and ind.rsi >= 48)
        or ((not is_long) and ind.macd_hist < 0 and ind.rsi <= 52)
    )
    if momentum_supports:
        angles.append(SignalAngle("momentum", "Подтверждение импульса", "моментум", 6.5))

    atr_pct = ind.atr / ind.price * 100.0 if ind.price else 0.0
    if ind.adx >= 24 and atr_pct >= 0.45:
        angles.append(SignalAngle("volatility_expansion", "Расширение волатильности", "волатильность", 6.2))

    if not angles:
        angles.append(SignalAngle("trend_structure", "Структура тренда", "структура", 5.0))

    best: Dict[str, SignalAngle] = {}
    for angle in angles:
        previous = best.get(angle.id)
        if previous is None or angle.weight > previous.weight:
            best[angle.id] = angle
    return sorted(best.values(), key=lambda item: item.weight, reverse=True)


def choose_signal_angle(ind, direction: str, mtf=None, recent_ids: Iterable[str] | None = None, variant_index: int = 0) -> SignalAngle:
    candidates = detect_signal_angles(ind, direction, mtf)
    recent = list(recent_ids or [])
    recent_penalty: Dict[str, float] = {}
    for offset, angle_id in enumerate(reversed(recent[-18:])):
        recent_penalty[angle_id] = max(recent_penalty.get(angle_id, 0.0), 4.5 - min(offset, 8) * 0.4)

    scored = []
    for angle in candidates:
        scored.append((angle.weight - recent_penalty.get(angle.id, 0.0), angle))
    scored.sort(key=lambda item: item[0], reverse=True)
    exploration_pool = [item[1] for item in scored[: min(8, len(scored))]]
    return exploration_pool[variant_index % len(exploration_pool)]


def choose_post_style(recent_ids: Iterable[str] | None = None, variant_index: int = 0) -> PostStyle:
    recent = list(recent_ids or [])
    frequency: Dict[str, int] = {}
    last_seen: Dict[str, int] = {}
    for position, style_id in enumerate(recent):
        frequency[style_id] = frequency.get(style_id, 0) + 1
        last_seen[style_id] = position

    ordered = sorted(
        POST_STYLES,
        key=lambda style: (
            frequency.get(style.id, 0),
            last_seen.get(style.id, -1),
            POST_STYLES.index(style),
        ),
    )
    return ordered[variant_index % len(ordered)]
