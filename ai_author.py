import os, json, requests, re

SYSTEM = """
Ты пишешь посты для Binance Square как живой опытный трейдер.
Не пиши как отчёт и не используй шаблоны.

Запрещено:
- Матрица решения
- ЗА / ПРОТИВ
- Сверка контекста
- навигация позиции
- контекст перед исполнением

Начало должно содержать наблюдение, конфликт или мнение.
Структура:
1. сильный первый абзац
2. почему ситуация интересна
3. что подтверждает сценарий
4. уровни сделки
5. что отменит идею
6. вопрос аудитории

Не меняй цены и направление.
Не придумывай новости.
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
            "temperature":0.85,
            "messages":[
                {"role":"system","content":SYSTEM},
                {"role":"user","content":json.dumps(data,ensure_ascii=False)}
            ]
        },timeout=45)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def validate(text, data):
    if not text:
        return False
    if str(data["symbol"]).upper() not in text.upper():
        return False
    if data["direction"].upper() not in text.upper():
        return False
    for v in [data["entry"], data["stop"]]:
        if str(round(float(v),2)) not in text:
            return False
    bad=["Матрица решения","Сверка контекста","ЗА","ПРОТИВ"]
    return not any(x in text for x in bad)
