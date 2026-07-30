import os, json, requests

SYSTEM = """
Ты пишешь короткие торговые сигналы для Binance Square как опытный трейдер.

Цель: живой человеческий язык, но полноценный сигнал.

ОБЯЗАТЕЛЬНО в каждом посте:
- тикер монеты
- направление LONG или SHORT
- вход или зона входа
- цели TP
- стоплосс SL

Ограничения:
- длина 700-1200 символов
- не пиши статьи и длинные объяснения
- не используй таблицы
- не обещай прибыль
- не используй "100%" и гарантии
- сначала короткий человеческий комментарий, затем сигнал

Формат:
Короткое наблюдение.

🪙 TICKER
📊 LONG/SHORT
Вход: ...
🎯 TP1: ... TP2: ...
🛑 SL: ...

Комментарий по сценарию и рискам.
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
            "max_tokens": 700,
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
