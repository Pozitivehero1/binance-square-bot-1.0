"""Human-first editorial strategy for Binance Square.

The market engine decides whether a setup is worth publishing. This module only
controls presentation: a short live reaction, a level story, a contrarian note,
a two-scenario post and a few slower educational formats.

The default feed is intentionally biased toward short, opinionated, chart-led
posts. Technical memo formats remain available for variety, but they have lower
priority and are never required just to satisfy a rotation counter.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Dict, Iterable, List, Sequence


@dataclass(frozen=True)
class ContentFormat:
    id: str
    title: str
    family: str
    visual_style: str
    question_mode: str = "optional"  # optional | none
    weight: float = 1.0
    requires_btc: bool = False
    requires_previous_symbol: bool = False


CONTENT_FORMATS: Sequence[ContentFormat] = (
    ContentFormat("hot_reaction", "Живая реакция", "reaction", "clean_chart", "optional", 1.90),
    ContentFormat("one_problem", "Один риск", "opinion", "clean_chart", "optional", 1.85),
    ContentFormat("crowd_trap", "Ловушка движения", "opinion", "context_chart", "optional", 1.80),
    ContentFormat("chart_story", "История графика", "story", "clean_chart", "optional", 1.70),
    ContentFormat("why_wait", "Почему жду", "decision", "clean_chart", "optional", 1.65),
    ContentFormat("level_story", "Один уровень", "level", "level_map", "optional", 1.55),
    ContentFormat("contrarian_take", "Неочевидный взгляд", "opinion", "clean_chart", "optional", 1.50),
    ContentFormat("mistake_to_avoid", "Ошибка входа", "education", "clean_chart", "optional", 1.45),
    ContentFormat("signal_vs_trade", "Сигнал не равен сделке", "decision", "split_scenario", "optional", 1.35),
    ContentFormat("trader_journal", "Запись трейдера", "personality", "journal_card", "optional", 1.30),
    ContentFormat("two_scenarios", "Два сценария", "scenario", "split_scenario", "optional", 1.25),
    ContentFormat("liquidity_map", "Карта уровней", "level", "level_map", "optional", 1.15),
    ContentFormat("market_context", "Контекст рынка", "context", "context_chart", "none", 1.05, requires_btc=True),
    ContentFormat("follow_up", "Продолжение идеи", "followup", "followup_card", "optional", 1.15, requires_previous_symbol=True),
    ContentFormat("risk_memo", "Риск-мемо", "risk", "risk_card", "none", 0.80),
    ContentFormat("indicator_lesson", "Индикатор без магии", "education", "indicator_card", "none", 0.75),
    ContentFormat("data_brief", "Коротко по данным", "data", "pulse_card", "none", 0.70),
    ContentFormat("setup_plan", "Торговый план", "setup", "clean_chart", "none", 0.65),
    ContentFormat("execution_protocol", "Исполнение", "process", "data_card", "none", 0.60),
)

FORMAT_BY_ID = {item.id: item for item in CONTENT_FORMATS}

TECHNICAL_FEED_FORMATS = {
    "risk_memo", "indicator_lesson", "data_brief", "setup_plan", "execution_protocol",
}



AUTHOR_VOICES = {
    "calm": {
        "label": "спокойный аналитик",
        "principle": "Мне не нужно угадать свечу. Нужны понятный уровень и понятная отмена идеи.",
        "notes": (
            "Я лучше пропущу движение, чем куплю его после того, как риск уже испорчен.",
            "Сначала хочу увидеть реакцию цены. Без неё хорошая картинка для меня ничего не значит.",
            "Мне не нужна идеальная точка — нужна точка, где заранее понятно, что делать при ошибке.",
            "Если рынок не даёт чистого подтверждения, я спокойно остаюсь без сделки.",
            "После сильной свечи мне важнее качество ретеста, чем желание успеть в движение.",
        ),
    },
    "direct": {
        "label": "прямой трейдер",
        "principle": "Нет подтверждения — нет ордера. Уровень сломан — идея закончилась.",
        "notes": (
            "Я не догоняю свечу. Пусть рынок сначала даст нормальную цену для риска.",
            "Здесь всё просто: уровень держат — смотрю дальше, не держат — прохожу мимо.",
            "Верное направление не спасает плохой вход, поэтому спешить здесь не хочу.",
            "Если цена не подтверждает идею, я не пытаюсь уговорить график.",
            "После такого импульса мне нужен ретест, а не ещё одна зелёная свеча.",
            "Пусть движение уйдёт без меня — покупать чужую спешку я не собираюсь.",
        ),
    },
    "analytical": {
        "label": "системный аналитик",
        "principle": "Направление задаёт структура, а сделку решают уровень, подтверждение и риск.",
        "notes": (
            "Сам сигнал выглядит нормально, но исполнение для меня важнее оценки индикаторов.",
            "Я отделяю идею от точки входа: хорошая идея легко становится плохой сделкой.",
            "Связка факторов полезна только пока цена подтверждает отмеченную границу.",
            "Сначала смотрю на структуру, потом на реакцию цены и только затем на ордер.",
            "Рынок уже дал достаточно данных; теперь важнее увидеть, удержится ли уровень.",
        ),
    },
    "contrarian": {
        "label": "контрарный наблюдатель",
        "principle": "Чем очевиднее движение, тем строже я отношусь к цене входа.",
        "notes": (
            "Когда свеча выглядит слишком убедительно, я первым делом проверяю, не опоздал ли вход.",
            "Толпа может угадать направление и всё равно купить по плохой цене.",
            "Сильный импульс привлекает внимание, но мне интереснее то, что произойдёт на первом ретесте.",
            "Я не спорю с движением — просто не готов платить за него любую цену.",
            "Самый опасный момент часто начинается тогда, когда сделка кажется очевидной.",
        ),
    },
}


def get_author_voice() -> Dict[str, object]:
    voice_id = os.getenv("AUTHOR_VOICE", "direct").strip().lower()
    return AUTHOR_VOICES.get(voice_id, AUTHOR_VOICES["direct"])


def author_note(index: int = 0) -> str:
    notes = get_author_voice()["notes"]
    return str(notes[index % len(notes)])


def author_principle() -> str:
    return str(get_author_voice()["principle"])


def eligible_formats(*, has_btc: bool, has_previous_symbol: bool) -> List[ContentFormat]:
    result: List[ContentFormat] = []
    allow_technical = os.getenv("ALLOW_TECHNICAL_FORMATS", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }
    for item in CONTENT_FORMATS:
        if item.requires_btc and not has_btc:
            continue
        if item.requires_previous_symbol and not has_previous_symbol:
            continue
        # The old bot kept drifting back to memo/data formats merely because they
        # were "fresh" in the rotation. They are opt-in now. Default output stays
        # social/feed-first even after hundreds of posts.
        if item.id in TECHNICAL_FEED_FORMATS and not allow_technical:
            continue
        result.append(item)
    return result


def rank_formats(
    recent_format_ids: Iterable[str],
    *,
    has_btc: bool,
    has_previous_symbol: bool,
) -> List[ContentFormat]:
    """Rank formats by freshness while preserving a strong feed-first bias."""
    recent = [str(value) for value in recent_format_ids if value]
    frequency: Dict[str, int] = {}
    last_seen: Dict[str, int] = {}
    for position, format_id in enumerate(recent):
        frequency[format_id] = frequency.get(format_id, 0) + 1
        last_seen[format_id] = position

    formats = eligible_formats(has_btc=has_btc, has_previous_symbol=has_previous_symbol)
    return sorted(
        formats,
        key=lambda item: (
            frequency.get(item.id, 0) / max(item.weight, 0.1),
            -item.weight,
            last_seen.get(item.id, -1),
            item.id,
        ),
    )


def choose_formats(
    recent_format_ids: Iterable[str],
    count: int,
    *,
    has_btc: bool,
    has_previous_symbol: bool,
) -> List[ContentFormat]:
    """Pick a varied batch without forcing dry dashboard-like formats first."""
    ranked = rank_formats(
        recent_format_ids,
        has_btc=has_btc,
        has_previous_symbol=has_previous_symbol,
    )
    if not ranked:
        return [FORMAT_BY_ID["hot_reaction"]]

    target = max(1, int(count))
    chosen: List[ContentFormat] = []
    family_counts: Dict[str, int] = {}
    visual_counts: Dict[str, int] = {}

    # First pass: strong editorial diversity, but human formats retain their weight.
    for item in ranked:
        if family_counts.get(item.family, 0) >= 2:
            continue
        if visual_counts.get(item.visual_style, 0) >= 3:
            continue
        chosen.append(item)
        family_counts[item.family] = family_counts.get(item.family, 0) + 1
        visual_counts[item.visual_style] = visual_counts.get(item.visual_style, 0) + 1
        if len(chosen) >= target:
            return chosen

    # Second pass: fill with the remaining best-ranked formats.
    used = {item.id for item in chosen}
    for item in ranked:
        if item.id in used:
            continue
        chosen.append(item)
        if len(chosen) >= target:
            return chosen

    index = 0
    while len(chosen) < target:
        chosen.append(ranked[index % len(ranked)])
        index += 1
    return chosen


def question_required(format_id: str) -> bool:
    # Human feed edition never forces a question. Forced CTAs were one of the
    # strongest signals that the old feed was template-generated.
    return False


def question_forbidden(format_id: str) -> bool:
    item = FORMAT_BY_ID.get(format_id)
    return bool(item and item.question_mode == "none")


def visual_style_for(format_id: str) -> str:
    item = FORMAT_BY_ID.get(format_id)
    return item.visual_style if item else "clean_chart"


def _move(value: float) -> str:
    value = float(value)
    if abs(value) >= 1.0:
        return f"{value:+.1f}%"
    return f"{value:+.2f}%"


def headline_candidates(
    *,
    ticker: str,
    direction: str,
    format_id: str,
    key_level: str,
    risk_pct: str,
    reward_pct: str,
    rsi: float,
    adx: float,
    price_vs_vwap: str,
    angle_title: str,
    change_15m: float = 0.0,
    volume_spike: float = 1.0,
    attention_label: str = "",
) -> List[str]:
    """Build human, consequence-led openings from code-controlled facts."""
    del risk_pct, reward_pct, angle_title, attention_label
    side = "LONG" if direction == "long" else "SHORT"
    movement = _move(change_15m)
    candidates: List[str] = []

    # Live-event openings come first, but direction and tape must agree. A +7%
    # impulse with a SHORT setup is not "chasing SHORT"; it is a possible
    # exhaustion/rejection story. Likewise a sharp sell-off with a LONG setup.
    move_aligned = (direction == "long" and change_15m > 0) or (direction == "short" and change_15m < 0)
    if abs(change_15m) >= 1.0:
        if move_aligned:
            candidates.extend((
                f"{ticker} уже {movement} за 15 минут. Я бы здесь не догонял {side}",
                f"{ticker}: {movement} за 15 минут — движение сильное, но вход уже спорный",
                f"После {movement} за 15 минут в {ticker} мне важнее ретест, чем новая свеча",
            ))
        elif direction == "short":
            candidates.extend((
                f"{ticker} уже {movement} за 15 минут. SHORT мне интересен только если импульс начнёт ломаться",
                f"После {movement} за 15 минут в {ticker} я не шорчу свечу — сначала нужен слом импульса",
                f"{ticker} резко вырос, но сам по себе рост ещё не повод открывать SHORT",
            ))
        else:
            candidates.extend((
                f"{ticker} уже {movement} за 15 минут. LONG мне интересен только если продавцы начнут терять контроль",
                f"После {movement} за 15 минут в {ticker} я не ловлю дно — сначала нужен возврат уровня",
                f"{ticker} резко снизился, но сам по себе слив ещё не повод открывать LONG",
            ))
    elif abs(change_15m) >= 0.35:
        candidates.extend((
            f"{ticker} начал двигаться, но я бы сначала посмотрел на {key_level}",
            f"В {ticker} появилось движение. Для меня сейчас всё упирается в {key_level}",
        ))

    if volume_spike >= 3.0:
        candidates.append(
            f"Объём в {ticker} резко вырос. Я смотрю не на свечу, а на то, удержат ли {key_level}"
        )

    by_format = {
        "hot_reaction": (
            f"{ticker} ожил. Но сильная свеча сама по себе для меня ещё не сделка",
            f"Сейчас в {ticker} есть движение — я бы сначала проверил его на ретесте",
        ),
        "one_problem": (
            f"Есть одна причина, почему я не тороплюсь с {ticker}",
            f"{ticker} выглядит сильно, но один момент делает вход для меня спорным",
        ),
        "crowd_trap": (
            f"Когда {ticker} выглядит слишком очевидно, я первым делом ищу плохой вход",
            f"Толпа может угадать направление {ticker} и всё равно купить слишком поздно",
        ),
        "chart_story": (
            f"У {ticker} сейчас не нужен прогноз — нужен ответ цены на {key_level}",
            f"Одна реакция у {key_level} скажет по {ticker} больше, чем пять индикаторов",
        ),
        "why_wait": (
            f"Не догоняю {ticker}: направление есть, нормальной точки входа пока нет",
            f"Движение {ticker} уже началось. Я лучше подожду цену, чем куплю спешку",
        ),
        "level_story": (
            f"Один уровень решит сценарий по {ticker}: {key_level}",
            f"Пока {ticker} рядом с {key_level}, реакция цены важнее любого прогноза",
        ),
        "contrarian_take": (
            f"Чем сильнее выглядит {ticker}, тем меньше мне хочется догонять эту свечу",
            f"Главный риск по {ticker} сейчас — не направление, а цена входа",
        ),
        "mistake_to_avoid": (
            f"Верный прогноз по {ticker} легко испортить одним поздним входом",
            f"Самая дорогая ошибка по {ticker} начинается после убедительной свечи",
        ),
        "signal_vs_trade": (
            f"Сигнал по {ticker} уже есть. Для сделки мне всё ещё не хватает подтверждения",
            f"По {ticker} уже есть сигнал, но открывать сделку я пока не спешу",
        ),
        "trader_journal": (
            f"Записываю план по {ticker} до того, как движение заставит спешить",
            f"По {ticker} у меня есть идея, но вход хочу увидеть от цены, а не от эмоций",
        ),
        "two_scenarios": (
            f"У {ticker} сейчас два нормальных сценария — угадывать третий не хочу",
            f"По {ticker} важнее план на оба исхода, чем уверенность в одном направлении",
        ),
        "liquidity_map": (
            f"По {ticker} сейчас важнее две ценовые границы, чем следующая свеча",
            f"Не угадываю движение {ticker} — смотрю, что цена сделает у {key_level}",
        ),
        "market_context": (
            f"Локально {ticker} выглядит интересно, но сначала сверяю его с общим рынком",
            f"Перед сделкой по {ticker} я бы сначала проверил, не мешает ли фон BTC",
        ),
        "follow_up": (
            f"Возвращаюсь к {ticker}: после нового движения старый план уже нужно пересчитать",
            f"Структура {ticker} изменилась — смотрю, что осталось от прошлой идеи",
        ),
        "risk_memo": (
            f"По {ticker} меня сейчас интересует не цель, а сколько стоит ошибка",
            f"Перед {side} по {ticker} я сначала проверяю, где идея перестанет быть рабочей",
        ),
        "indicator_lesson": (
            f"RSI {rsi:.0f} по {ticker} выглядит убедительно, но точку входа решает не он",
            f"ADX {adx:.0f} показывает силу {ticker}, а сделку всё равно решает цена",
        ),
        "data_brief": (
            f"Коротко по {ticker}: что в данных действительно важно для сделки",
            f"По {ticker} сейчас достаточно трёх вещей: движение, уровень и отмена идеи",
        ),
        "setup_plan": (
            f"По {ticker} план простой: подтверждение, цель и заранее понятная отмена",
            f"Ордер по {ticker} для меня появится только после реакции у {key_level}",
        ),
        "execution_protocol": (
            f"Перед ордером по {ticker} я бы проверил одну вещь: цена всё ещё даёт нормальный риск?",
            f"Сделка по {ticker} либо проходит проверку уровнем, либо её просто нет",
        ),
    }
    candidates.extend(by_format.get(format_id, by_format["hot_reaction"]))

    # Indicator-led openings are reserved for slower analytical formats. Human
    # reaction posts should not randomly fall back to a terminal-like VWAP title.
    if format_id in {"market_context", "indicator_lesson", "data_brief", "setup_plan", "execution_protocol"}:
        candidates.append(
            f"{ticker} сейчас {price_vs_vwap} VWAP, но для меня сделку всё равно решает {key_level}"
        )
    return candidates


def choose_headline(candidates: Sequence[str], recent_titles: Iterable[str], index: int = 0) -> str:
    def signature(value: str) -> str:
        text = str(value).strip().lower().replace("ё", "е")
        text = re.sub(r"\$[a-z0-9]+", "$ticker", text)
        text = re.sub(r"\b[+-]?\d+(?:[.,]\d+)?%?", "#", text)
        text = re.sub(r"\s+", " ", text)
        return text

    used = {signature(title) for title in recent_titles if title}
    available = [item for item in candidates if signature(item) not in used]
    pool = available or list(candidates)
    return pool[index % len(pool)]
