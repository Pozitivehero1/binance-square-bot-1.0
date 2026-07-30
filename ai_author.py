import os, json, requests

SYSTEM = """
Ты пишешь посты для Binance Square как живой трейдер.

Твоя задача: создать человеческий торговый пост, а не автоматический отчёт.

ВАЖНЫЕ ПРАВИЛА:
- Никогда не придумывай цены, уровни, индикаторы или статистику.
- Все цифры бери только из входных данных.
- Первый раз актив укажи в формате $TICKER (например $ONDO).
- Обязательно сохрани блок:

📌 План сделки
Вход: ...
Цели: TP1 ..., TP2 ..., TP3 ...
Стоплосс: ...

- Не меняй значения уровней.
- Не добавляй свои прогнозы.

Стиль:
- короткое человеческое вступление;
- объясни почему сейчас интересен актив;
- расскажи риск и что сломает сценарий;
- один вопрос аудитории в конце.

Не используй:
- "гарантированная прибыль"
- "100%"
- "точно вырастет"
- "точно упадет"
- канцелярские фразы вроде "аргумент №1", "проверка тезиса", "матрица".

Пиши как опытный трейдер в соцсети.
"""

def author_post(data):
    key=os.getenv("MISTRAL_API","").strip()
    if not key:
        return None
    r=requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization":f"Bearer {key}"},
        json={
            "model":os.getenv("MISTRAL_MODEL","mistral-small-latest"),
            "temperature":0.9,
            "max_tokens":900,
            "messages":[
                {"role":"system","content":SYSTEM},
                {"role":"user","content":json.dumps(data,ensure_ascii=False)}
            ]
        },
        timeout=45
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def validate(text,data):
    """Light validation: protect trading facts, do not force a template."""
    if not text:
        return False
    upper=text.upper()
    ticker="$"+str(data.get("symbol","")).upper().replace("USDT","")
    required=[ticker]
    for item in required:
        if item not in upper:
            return False
    if not any(x in upper for x in ["LONG","SHORT","ЛОНГ","ШОРТ"]):
        return False
    # Human posts may format levels differently. Check presence of trade block concepts only.
    if not any(x in upper for x in ["ВХОД","ENTRY"]):
        return False
    if not any(x in upper for x in ["TP1","TP 1","ЦЕЛ","TARGET"]):
        return False
    if not any(x in upper for x in ["СТОП","SL","STOP","СТОПЛОСС"]):
        return False
    return len(text.strip()) >= 200
