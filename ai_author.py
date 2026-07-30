import os, json, requests

SYSTEM = """
Ты пишешь посты для Binance Square как живой опытный трейдер.

Главная цель — не выглядеть как бот или аналитический отчёт.
Пиши как человек, который наблюдает рынок и делится идеей с другими трейдерами.

Правила:
- не начинай с тикера и технического отчёта;
- первые 2 строки должны вызывать интерес;
- никаких гарантий прибыли;
- не выдумывай новости;
- не используй таблицы и шаблонные блоки;
- стиль: короткие абзацы, личное мнение, живой язык.

Структура:
1. Наблюдение или мнение о ситуации.
2. Почему монета сейчас интересна.
3. Что подтверждает сценарий.
4. Сценарий сделки с уровнями.
5. Уровень отмены идеи.
6. Вопрос аудитории.

Обязательно сохрани смысл торговых данных, но адаптируй их для человека.
"""


def author_post(data):
    key = os.getenv("MISTRAL_API", "").strip()
    if not key:
        return None

    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
            "temperature": 0.95,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)}
            ]
        },
        timeout=45
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def validate(text, data):
    if not text:
        return False

    t = text.upper()
    symbol = str(data.get("symbol", "")).upper().replace("USDT", "")

    if symbol not in t:
        return False

    direction = str(data.get("direction", "")).upper()

    if direction == "LONG":
        if not any(x in t for x in ["LONG", "BUY", "ПОКУПК", "БЫЧ", "РОСТ"]):
            return False
    else:
        if not any(x in t for x in ["SHORT", "SELL", "ПРОДА", "МЕДВЕЖ"]):
            return False

    bad = [
        "ГАРАНТИРОВАН",
        "100%",
        "ТОЧНО ЗАРАБОТАЕТ",
        "МАТРИЦА РЕШЕНИЯ",
        "СВЕРКА КОНТЕКСТА"
    ]

    if any(x in t for x in bad):
        return False

    if len(text) < 350:
        return False

    return True
