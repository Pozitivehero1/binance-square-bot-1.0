import pandas as pd
import ta

def build_indicators(raw):
    # Проверка на наличие данных и минимальную длину (нужно хотя бы 10 свечей для расчёта change)
    if not raw or len(raw) < 10:
        return None

    df = pd.DataFrame(raw)

    close = df[4].astype(float)
    high = df[2].astype(float)
    low = df[3].astype(float)

    # Убедимся, что хватает данных для EMA50 (нужно как минимум 50 свечей, иначе EMA50 = close)
    if len(close) < 50:
        # Если данных меньше 50, используем последнее значение close для EMA50
        ema50 = close.iloc[-1]
    else:
        ema50 = close.ewm(span=50).mean().iloc[-1]

    rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]
    ema20 = close.ewm(span=20).mean().iloc[-1]
    # change рассчитываем только если есть 10 свечей (уже проверено выше)
    change = (close.iloc[-1] - close.iloc[-10]) / close.iloc[-10] * 100

    return {
        "price": float(close.iloc[-1]),
        "rsi": float(rsi),
        "ema20": float(ema20),
        "ema50": float(ema50),
        "change": float(change)
    }
