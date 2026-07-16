"""Post text builder — direction-aware, uses IndicatorResult + SignalScore.
Combines rich templates from v1 with modern structure from v2.
Exposes:
  generate_post_with_memory(symbol, basic, mtf, score, memory, levels) -> str
"""
from __future__ import annotations
import logging
import random
import re
import requests
import os
from typing import Dict, Optional, List
from memory import PostMemory
logger = logging.getLogger(__name__)

MISTRAL_API = os.getenv("MISTRAL_API")

# ---------- formatting helper ----------
def _fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.4f}".rstrip("0").rstrip(".")
    if p >= 0.01:
        return f"{p:.5f}".rstrip("0").rstrip(".")
    return f"{p:.8f}".rstrip("0").rstrip(".")


# ---------- compute levels (from v2) ----------
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
        if ind.resistance and ind.resistance > entry:
            tp3 = min(tp3, float(ind.resistance) * 1.02)
    else:  # short
        entry = price
        stop = max(entry + atr * 1.5, float(ind.resistance) if ind.resistance else entry + atr * 1.5)
        risk = max(stop - entry, atr)
        tp1 = entry - risk * 1.5
        tp2 = entry - risk * 2.5
        tp3 = entry - risk * 4.0
        if ind.support and ind.support < entry:
            tp3 = max(tp3, float(ind.support) * 0.98)

    return {"entry": entry, "tp1": tp1, "tp2": tp2, "tp3": tp3, "stop": stop}


# ---------- huge template pools (from v1) ----------
HOOKS = [
    "Внимание! {symbol} резко вырос/упал – что дальше?",
    "Только что произошло нечто важное для {symbol}",
    "Этот сигнал по {symbol} я ждал неделю",
    "Как заработать на движении {symbol}?",
    "Крупные игроки начали скупать {symbol}",
    "Технический анализ {symbol} – бычий разворот?",
    "Осторожно: {symbol} готовится к сильному движению",
    "Не упусти момент с {symbol}",
    "Что скрывает график {symbol}?",
    "Прибыльная возможность на {symbol}",
    "Как я нашел эту точку входа на {symbol}",
    "Почему я выбираю {symbol} сегодня",
    "Важное предупреждение по {symbol}",
    "История повторяется: {symbol} снова на уровне",
    "Мой прогноз по {symbol} на сегодня",
    "Смотрите, что происходит с {symbol}",
    "Кто следует за {symbol}?",
    "Этот паттерн на {symbol} предвещает рост",
    "Сегодня особенный день для {symbol}",
    "Как я использую {symbol} для заработка",
    "Топ-причина следить за {symbol}",
    "Разбор {symbol} – отличный момент",
    "Необычная активность на {symbol}",
    "Почему все говорят о {symbol}?",
    "Мой опыт торговли {symbol}",
    "Готовьтесь: {symbol} может удивить",
    "На что обратить внимание по {symbol}",
    "Движение {symbol} – что дальше?",
    "Сигнал для входа в {symbol}",
    "Рынок {symbol} подает знаки",
    "Взгляд на {symbol} от профессионала",
    "Как я анализирую {symbol}",
    "Этот инструмент показывает рост {symbol}",
    "Не пропустите движение {symbol}",
    "Что говорят индикаторы о {symbol}",
    "Критический момент для {symbol}",
    "Мой прогноз по {symbol} на неделю",
    "Как {symbol} может принести прибыль",
    "Следуй за ликвидностью – {symbol}",
    "Почему я не продаю {symbol}",
    "Идеальная точка входа в {symbol}",
    "Как {symbol} бьет рекорды",
    "Анализ настроений по {symbol}",
    "Используй шанс с {symbol}",
    "Что скрывается за движением {symbol}?",
    "Почему {symbol} недооценен",
    "Как я заработал на {symbol}",
    "Важный уровень на {symbol}",
    "Пора обратить внимание на {symbol}",
    "Сигнал для выхода из {symbol}",
    "Куда пойдет {symbol} сегодня?",
    "Обзор {symbol} – все детали",
    "Этот график говорит о росте {symbol}",
    "Как использовать {symbol} в портфеле",
    "Редкая возможность с {symbol}",
    "Почему {symbol} может обвалиться",
    "Как я защищаю свои позиции по {symbol}",
    "Прогноз {symbol} от эксперта",
    "Что будет, если {symbol} пробьет уровень?",
    "Мой секрет по {symbol}",
    "Время покупать {symbol}?",
    "Как {symbol} влияет на рынок",
    "Трейдеры выбирают {symbol}",
    "Этот сигнал по {symbol} – 90% точности",
    "Активность {symbol} зашкаливает",
    "Как {symbol} изменит ваш день",
    "Почему я ждал именно этого движения {symbol}",
    "Что делать с {symbol} сейчас",
    "История {symbol} повторяется",
    "Как я оцениваю {symbol}",
    "Ваш шанс с {symbol}",
    "Разбор движения {symbol}",
    "Как {symbol} обманывает многих",
    "Почему я ставлю на {symbol}",
    "Смотрите на {symbol} внимательнее",
    "Как {symbol} может стать вашим фаворитом",
    "Прогноз по {symbol} на день",
    "Кто управляет {symbol}?",
    "Как {symbol} удивил рынок",
    "Используйте момент с {symbol}",
    "Что важно знать о {symbol}",
    "Как я определяю тренд {symbol}",
    "Неожиданный поворот {symbol}",
    "Почему {symbol} в центре внимания",
    "Сигнал для сделки с {symbol}",
    "Анализ движения {symbol}",
    "Как {symbol} достиг максимума",
    "Секрет успеха {symbol}",
    "Что говорят киты о {symbol}",
    "Почему я держу {symbol}",
    "Лучший момент для {symbol}",
    "Как {symbol} обгоняет рынок",
    "Почему {symbol} опасен для слабых",
    "Как я читаю график {symbol}",
    "Этот паттерн на {symbol} – ключ",
    "Время действовать с {symbol}",
    "Прогноз {symbol} на ближайшие часы",
    "Что ждет {symbol} дальше",
    "Я покупаю {symbol} – вот почему",
    "Не упустите {symbol}",
    "Как {symbol} изменит правила игры",
    "Сигнал для входа в {symbol} сегодня",
    "Почему {symbol} стоит вашего внимания",
    "Как я зарабатываю на {symbol}",
]

CTA_LIST = [
    "А вы уже зашли в позицию?",
    "Что думаете по этому сигналу?",
    "Поделитесь своим мнением в комментариях",
    "Кто тоже торгует эту монету?",
    "Ждёте рост или падение?",
    "Какой у вас стоп-лосс?",
    "Кто уже в сделке?",
    "Какой ваш прогноз?",
    "Стоит ли брать этот сигнал?",
    "А вы согласны с анализом?",
    "Кто еще видит этот паттерн?",
    "Как думаете, пробьет уровень?",
    "Что по этому поводу думают трейдеры?",
    "Ждём комментариев!",
    "А у вас есть эта монета?",
    "Ставьте лайк, если тоже следите",
    "Ваше мнение очень важно!",
    "Какой таймфрейм вы используете?",
    "Согласны с целью?",
    "Может быть, я ошибаюсь?",
    "А вы как считаете?",
    "Ждем вашего фидбека!",
    "Кто уже взял?",
    "Какой ваш take-profit?",
    "Когда вы выйдете из сделки?",
    "У кого похожий анализ?",
    "Какие у вас риски?",
    "Какой процент вы рискуете?",
    "Это ваш фаворит?",
    "Кого еще анализируете?",
    "Как считаете, стоит докупить?",
    "Верите в рост?",
    "Что говорят ваши индикаторы?",
    "Какой новостной фон?",
    "Что скажете по этому уровню?",
]

STYLES = [
    "энергичный, с короткими предложениями, много эмодзи",
    "спокойный, аналитический, с цифрами",
    "разговорный, обращение к читателю, вопросы",
    "ироничный, с юмором, нестандартные сравнения",
    "вдохновляющий, с мотивацией",
    "с тревожным подтекстом, предупреждающий",
    "детальный, с объяснением каждого индикатора",
    "лаконичный, только суть, без воды",
    "эмоциональный, с восклицаниями",
    "уверенный, авторитетный, с фактами",
    "скептический, с сомнениями",
    "прогнозирующий, с конкретными цифрами",
]

STRUCTURES = [
    "hook → проблема → решение → вход → цели → стоп → вывод → CTA",
    "hook → анализ → сигнал → риск → цель → CTA",
    "hook → индикаторы → уровень → вход → цели → стоп → вывод → CTA",
    "hook → сценарий → вход → TP1/TP2/TP3 → стоп → вывод → CTA",
    "hook → новости → техника → вход → управление риском → CTA",
    "hook → эмоции → факты → вход → цели → стоп → вывод → CTA",
    "hook → вопрос → ответ → сигнал → риск → прибыль → CTA",
    "hook → история → паттерн → вход → цели → стоп → CTA",
]


# ---------- main generator ----------
def generate_post_with_memory(
    *,
    symbol: str,
    basic: str,
    mtf,
    score,
    memory: Optional[PostMemory] = None,
    levels: Optional[Dict[str, float]] = None,
) -> str:
    """Assemble a Binance Square-style post using rich templates and memory."""
    ind = mtf.tf_15m
    direction = score.direction
    lv = levels or _levels(ind, direction)

    # Avoid re-using hooks/CTAs/styles that appeared recently
    used_titles = memory.get_last_titles(20) if memory else []
    used_ctas = memory.get_last_ctas(20) if memory else []
    used_styles = memory.get_last_styles(20) if memory else []

    # Choose hook
    available_hooks = [h for h in HOOKS if h not in used_titles]
    if not available_hooks:
        available_hooks = HOOKS
    hook_template = random.choice(available_hooks)
    hook = hook_template.format(symbol=f"${basic}")

    # Choose style
    available_styles = [s for s in STYLES if s not in used_styles]
    if not available_styles:
        available_styles = STYLES
    style = random.choice(available_styles)

    # Choose CTA
    available_ctas = [c for c in CTA_LIST if c not in used_ctas]
    if not available_ctas:
        available_ctas = CTA_LIST
    cta = random.choice(available_ctas)

    # Choose structure
    structure = random.choice(STRUCTURES)

    # Build parts dictionary (rich content)
    price = ind.price
    change_1h = ind.change_1h
    rsi = ind.rsi
    ema20 = ind.ema20
    ema50 = ind.ema50
    ema200 = ind.ema200
    adx = ind.adx
    vwap = ind.vwap
    vol_rel = ind.volume_relative
    confidence = score.confidence
    rr = ind.risk_reward

    direction_emoji = "🚀" if direction == "long" else "🔻"
    direction_word = "бычий" if direction == "long" else "медвежий"

    # Build reason text based on patterns
    signal_reason = []
    if ind.pullback:
        signal_reason.append("откат к EMA20")
    if ind.breakout_up and direction == "long":
        signal_reason.append("пробой сопротивления")
    if ind.breakout_down and direction == "short":
        signal_reason.append("пробой поддержки")
    if ind.liquidity_sweep:
        signal_reason.append("свип ликвидности")
    if ind.trend_continuation:
        signal_reason.append("продолжение тренда")
    if not signal_reason:
        signal_reason.append("смешанные сигналы, но индикаторы указывают на движение")
    reason_text = ", ".join(signal_reason)

    parts = {
        "hook": hook,
        "problem": f"На {basic} сформировался сигнал, который нельзя игнорировать.",
        "analysis": f"Цена {_fmt_price(price)} USDT, изменение за час {change_1h:+.2f}%. RSI {rsi:.1f}, EMA20 {_fmt_price(ema20)}, EMA50 {_fmt_price(ema50)}. ADX {adx:.1f} указывает на {'сильный' if adx > 25 else 'слабый'} тренд.",
        "signal": f"Основная причина: {reason_text}.",
        "entry": f"Вход по текущей цене {_fmt_price(lv['entry'])} USDT.",
        "tp1": f"TP1: {_fmt_price(lv['tp1'])} USDT",
        "tp2": f"TP2: {_fmt_price(lv['tp2'])} USDT",
        "tp3": f"TP3: {_fmt_price(lv['tp3'])} USDT",
        "stop": f"Стоп-лосс: {_fmt_price(lv['stop'])} USDT",
        "risk": f"Риск/прибыль: {rr:.1f}",
        "conclusion": f"Итог: {direction_word.capitalize()} сценарий. Уровень уверенности: {confidence:.1f}%.",
        "cta": cta,
        "TP1/TP2/TP3": f"Цели: {_fmt_price(lv['tp1'])} / {_fmt_price(lv['tp2'])} / {_fmt_price(lv['tp3'])} USDT",
        "индикаторы": f"RSI {rsi:.0f}, ADX {adx:.0f}, объём x{vol_rel:.2f}",
        "уровень": f"Ближайший уровень: {'сопротивление' if direction=='long' else 'поддержка'} {_fmt_price(ind.resistance if direction=='long' else ind.support)}",
        "сценарий": f"{direction_word.capitalize()} сценарий с целями {_fmt_price(lv['tp1'])} → {_fmt_price(lv['tp2'])} → {_fmt_price(lv['tp3'])}",
        "решение": "Рекомендую рассмотреть вход с соблюдением риск-менеджмента.",
        "вывод": f"Сигнал {direction_word}, R/R {rr:.1f}, уверенность {confidence:.0f}%.",
        "вопрос": f"Как думаете, {basic} достигнет {_fmt_price(lv['tp1'])}?",
        "ответ": f"Мой анализ показывает, что {basic} имеет потенциал для движения к {_fmt_price(lv['tp1'])}.",
    }

    # Build post according to structure
    order = [x.strip() for x in structure.split("→")]
    text_lines = []
    for item in order:
        if item in parts:
            text_lines.append(parts[item])

    # Add emojis randomly to some lines
    emojis = ["🔥", "💰", "💎", "📊", "⚡", "🎯", "💡", "🚨", "🟢", "🔴"]
    for i, line in enumerate(text_lines):
        if random.random() > 0.5 and not any(e in line for e in emojis):
            emoji = random.choice(emojis)
            text_lines[i] = f"{emoji} {line}"

    post = "\n".join(text_lines)

    # Optionally add referral link
    ref_link = os.getenv("REFERRAL_LINK")
    if ref_link:
        post += f"\n\nТоргуйте на Binance по моей ссылке: {ref_link}"

    # Deduplication: if post is similar to recent, replace hook and CTA
    if memory and memory.is_similar(post):
        new_hook = random.choice([h for h in HOOKS if h != hook_template]).format(symbol=f"${basic}")
        post = post.replace(hook, new_hook, 1)
        new_cta = random.choice([c for c in CTA_LIST if c != cta])
        post = post.replace(cta, new_cta, 1)

    # Length control
    if len(post) > 700:
        post = post[:700]
    elif len(post) < 500:
        # Add extra analysis
        extra = f" Дополнительно: цена выше VWAP ({_fmt_price(vwap)}), что подтверждает {direction_word} настрой. Объём в {vol_rel:.1f}x выше среднего."
        post += extra

    # Clean up double spaces/newlines
    post = re.sub(r'\n{2,}', '\n\n', post)

    # Optionally polish with Mistral AI (if API key exists)
    if MISTRAL_API:
        try:
            post = _polish_with_ai(post, basic, style)
        except Exception as e:
            logger.error(f"AI polish failed: {e}")

    return post


# ---------- Mistral polishing (from v1) ----------
def _polish_with_ai(text: str, basic: str, style: str) -> str:
    """Send draft to Mistral for style improvement."""
    prompt = f"""
Ты — опытный крипто-журналист. Перепиши следующий пост в стиле: {style}.
Сохрани все ключевые цифры и факты. Сделай текст живым, человечным, без шаблонов.
Используй смайлы где уместно. Убери любые маркеры списков. Длина 300-500 символов.
Пост должен начинаться с заголовка, затем идти анализ, сигнал, вход, цели, стоп, вывод и CTA.
Указывай конкретный вход, цели и стоп-лосс.
Все упоминания монеты делай строго как ${basic}.

Текст для переработки:
{text}
"""
    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {MISTRAL_API}",
            "Content-Type": "application/json"
        },
        json={
            "model": "mistral-small",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,
            "max_tokens": 400
        },
        timeout=60
    )
    r.raise_for_status()
    data = r.json()
    polished = data["choices"][0]["message"]["content"]
    # Remove markdown artifacts
    for ch in ['*', '_', '`', '#']:
        polished = polished.replace(ch, '')
    return polished.strip()