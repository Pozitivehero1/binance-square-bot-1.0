import pandas as pd
import numpy as np
import ta
from typing import Dict, Optional, List, Tuple
import logging
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger(__name__)

@dataclass
class IndicatorResult:
    """Результат расчёта индикаторов для одного таймфрейма."""
    price: float
    change_1h: float
    change_4h: float
    change_24h: float
    rsi: float
    ema20: float
    ema50: float
    ema200: float
    atr: float
    adx: float
    macd: float
    macd_signal: float
    macd_hist: float
    vwap: float
    bb_high: float
    bb_low: float
    bb_mid: float
    obv: float
    cci: float
    stoch_rsi_k: float
    stoch_rsi_d: float
    volume_relative: float  # текущий объём / средний за 20
    swing_high: float       # последний локальный максимум (за 20 свечей)
    swing_low: float        # последний локальный минимум
    support: float          # ближайший уровень поддержки (простой метод)
    resistance: float       # ближайший уровень сопротивления
    breakout_up: bool       # пробой вверх (цена выше resistance)
    breakout_down: bool     # пробой вниз (цена ниже support)
    liquidity_sweep: bool   # свип ликвидности (цена пробила swing high/low и вернулась)
    pullback: bool          # откат к EMA20 после тренда
    trend_continuation: bool # продолжение тренда (EMA20 > EMA50 и цена выше EMA20)
    false_breakout: bool    # ложный пробой (цена пробила уровень, но закрылась обратно)
    atr_stop: float         # стоп на основе ATR (ATR * 2)
    risk_reward: float      # соотношение риск/прибыль (рассчитывается отдельно)

@dataclass
class MultiTimeframeIndicators:
    """Индикаторы для нескольких таймфреймов."""
    symbol: str
    tf_15m: Optional[IndicatorResult] = None
    tf_1h: Optional[IndicatorResult] = None
    tf_4h: Optional[IndicatorResult] = None
    tf_1d: Optional[IndicatorResult] = None
    confidence_score: float = 0.0

class IndicatorsCalculator:
    """Класс для расчёта всех технических индикаторов."""
    
    def __init__(self, df: pd.DataFrame):
        """
        :param df: DataFrame с колонками 'open', 'high', 'low', 'close', 'volume'
        """
        self.df = df
        self.close = df['close'].values
        self.high = df['high'].values
        self.low = df['low'].values
        self.open = df['open'].values
        self.volume = df['volume'].values
        self.n = len(df)
        self._ensure_min_length()
    
    def _ensure_min_length(self):
        """Если данных мало, дополняем последним значением для расчётов."""
        if self.n < 200:
            # Для индикаторов, требующих длинной истории, повторяем последнюю свечу
            last = self.df.iloc[-1:].copy()
            repeats = 200 - self.n
            if repeats > 0:
                repeated = pd.concat([last]*repeats, ignore_index=True)
                self.df = pd.concat([self.df, repeated], ignore_index=True)
                self.n = len(self.df)
                # Обновляем массивы
                self.close = self.df['close'].values
                self.high = self.df['high'].values
                self.low = self.df['low'].values
                self.open = self.df['open'].values
                self.volume = self.df['volume'].values
    
    def calculate_all(self) -> IndicatorResult:
        """Рассчитывает все индикаторы и возвращает результат."""
        # Базовые цены
        price = float(self.close[-1])
        
        # Изменения за разные периоды (если данных достаточно)
        change_1h = self._calc_change(6)   # 15m * 6 = 1.5h, возьмём 4 для 1h
        change_4h = self._calc_change(16)  # 15m * 16 = 4h
        change_24h = self._calc_change(96) # 15m * 96 = 24h
        # Если данных мало, берём доступный период
        if change_1h is None:
            change_1h = 0.0
        if change_4h is None:
            change_4h = 0.0
        if change_24h is None:
            change_24h = 0.0
        
        # RSI (14)
        rsi = ta.momentum.RSIIndicator(self.df['close'], window=14).rsi().iloc[-1]
        rsi = float(rsi) if not np.isnan(rsi) else 50.0
        
        # EMA
        ema20 = self.df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = self.df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        ema200 = self.df['close'].ewm(span=200, adjust=False).mean().iloc[-1]
        ema20 = float(ema20) if not np.isnan(ema20) else price
        ema50 = float(ema50) if not np.isnan(ema50) else price
        ema200 = float(ema200) if not np.isnan(ema200) else price
        
        # ATR (14)
        atr_ind = ta.volatility.AverageTrueRange(self.df['high'], self.df['low'], self.df['close'], window=14)
        atr = float(atr_ind.average_true_range().iloc[-1]) if not np.isnan(atr_ind.average_true_range().iloc[-1]) else price * 0.01
        
        # ADX (14)
        adx_ind = ta.trend.ADXIndicator(self.df['high'], self.df['low'], self.df['close'], window=14)
        adx = float(adx_ind.adx().iloc[-1]) if not np.isnan(adx_ind.adx().iloc[-1]) else 20.0
        
        # MACD
        macd_ind = ta.trend.MACD(self.df['close'])
        macd = float(macd_ind.macd().iloc[-1]) if not np.isnan(macd_ind.macd().iloc[-1]) else 0.0
        macd_signal = float(macd_ind.macd_signal().iloc[-1]) if not np.isnan(macd_ind.macd_signal().iloc[-1]) else 0.0
        macd_hist = float(macd_ind.macd_diff().iloc[-1]) if not np.isnan(macd_ind.macd_diff().iloc[-1]) else 0.0
        
        # VWAP (используем типовую цену)
        typical = (self.df['high'] + self.df['low'] + self.df['close']) / 3
        vwap = (typical * self.df['volume']).cumsum() / self.df['volume'].cumsum()
        vwap = float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else price
        
        # Bollinger Bands (20, 2)
        bb = ta.volatility.BollingerBands(self.df['close'], window=20, window_dev=2)
        bb_high = float(bb.bollinger_hband().iloc[-1]) if not np.isnan(bb.bollinger_hband().iloc[-1]) else price * 1.05
        bb_low = float(bb.bollinger_lband().iloc[-1]) if not np.isnan(bb.bollinger_lband().iloc[-1]) else price * 0.95
        bb_mid = float(bb.bollinger_mavg().iloc[-1]) if not np.isnan(bb.bollinger_mavg().iloc[-1]) else price
        
        # OBV (On-Balance Volume)
        obv = ta.volume.OnBalanceVolumeIndicator(self.df['close'], self.df['volume']).on_balance_volume().iloc[-1]
        obv = float(obv) if not np.isnan(obv) else 0.0
        
        # CCI (20)
        cci = ta.trend.CCIIndicator(self.df['high'], self.df['low'], self.df['close'], window=20).cci().iloc[-1]
        cci = float(cci) if not np.isnan(cci) else 0.0
        
        # Stochastic RSI (14, 14, 3, 3)
        stoch = ta.momentum.StochRSIIndicator(self.df['close'], window=14, smooth1=3, smooth2=3)
        stoch_rsi_k = float(stoch.stochrsi_k().iloc[-1]) if not np.isnan(stoch.stochrsi_k().iloc[-1]) else 50.0
        stoch_rsi_d = float(stoch.stochrsi_d().iloc[-1]) if not np.isnan(stoch.stochrsi_d().iloc[-1]) else 50.0
        
        # Относительный объём (текущий / средний за 20)
        vol_avg = self.df['volume'].rolling(20).mean().iloc[-1]
        volume_relative = float(self.volume[-1] / vol_avg) if vol_avg > 0 else 1.0
        
        # Swing High / Low (за последние 20 свечей)
        # Локальные максимумы и минимумы
        high_vals = self.high[-20:]
        low_vals = self.low[-20:]
        swing_high = float(np.max(high_vals))
        swing_low = float(np.min(low_vals))
        
        # Простые уровни поддержки/сопротивления (на основе максимумов/минимумов за 50 свечей)
        support = float(self.low[-50:].min()) if self.n >= 50 else swing_low
        resistance = float(self.high[-50:].max()) if self.n >= 50 else swing_high
        
        # Пробои
        breakout_up = price > resistance * 1.005  # небольшой допуск
        breakout_down = price < support * 0.995
        
        # Свип ликвидности: цена пересекла swing high или swing low и вернулась обратно
        # Проверяем последние 10 свечей
        recent_high = self.high[-10:]
        recent_low = self.low[-10:]
        crossed_high = any(h > swing_high for h in recent_high)
        crossed_low = any(l < swing_low for l in recent_low)
        # Свип, если цена пробила и закрылась ниже/выше соответственно
        liquidity_sweep = (crossed_high and self.close[-1] < swing_high) or (crossed_low and self.close[-1] > swing_low)
        
        # Откат (pullback) – цена близка к EMA20 после тренда
        # Просто проверяем, что цена ниже EMA20, но выше EMA50 (бычий откат)
        pullback = (price < ema20) and (price > ema50) and (ema20 > ema50)
        
        # Продолжение тренда – EMA20 > EMA50 и цена выше EMA20
        trend_continuation = (ema20 > ema50) and (price > ema20)
        
        # Ложный пробой – цена пробила сопротивление, но закрылась ниже
        false_breakout = breakout_up and (self.close[-1] < resistance)
        
        # ATR Stop
        atr_stop = price - atr * 2
        
        # Risk/Reward (приблизительно)
        # Цель = сопротивление или swing_high, риск = ATR*2
        target = resistance if resistance > price else swing_high
        if target > price:
            reward = target - price
            risk = atr * 1.5
            risk_reward = reward / risk if risk > 0 else 1.0
        else:
            risk_reward = 1.0
        
        return IndicatorResult(
            price=price,
            change_1h=change_1h,
            change_4h=change_4h,
            change_24h=change_24h,
            rsi=rsi,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            atr=atr,
            adx=adx,
            macd=macd,
            macd_signal=macd_signal,
            macd_hist=macd_hist,
            vwap=vwap,
            bb_high=bb_high,
            bb_low=bb_low,
            bb_mid=bb_mid,
            obv=obv,
            cci=cci,
            stoch_rsi_k=stoch_rsi_k,
            stoch_rsi_d=stoch_rsi_d,
            volume_relative=volume_relative,
            swing_high=swing_high,
            swing_low=swing_low,
            support=support,
            resistance=resistance,
            breakout_up=breakout_up,
            breakout_down=breakout_down,
            liquidity_sweep=liquidity_sweep,
            pullback=pullback,
            trend_continuation=trend_continuation,
            false_breakout=false_breakout,
            atr_stop=atr_stop,
            risk_reward=risk_reward
        )
    
    def _calc_change(self, periods: int) -> Optional[float]:
        """Рассчитывает процентное изменение за указанное количество свечей."""
        if self.n <= periods:
            return None
        prev = self.close[-periods-1]
        if prev == 0:
            return 0.0
        return (self.close[-1] - prev) / prev * 100.0

def calculate_indicators(df: pd.DataFrame) -> IndicatorResult:
    """Упрощённая функция для расчёта индикаторов по одному DataFrame."""
    calc = IndicatorsCalculator(df)
    return calc.calculate_all()

def calculate_multi_timeframe(symbol: str, dataframes: Dict[str, pd.DataFrame]) -> MultiTimeframeIndicators:
    """
    Принимает словарь {таймфрейм: DataFrame} и возвращает агрегированные индикаторы.
    """
    result = MultiTimeframeIndicators(symbol=symbol)
    for tf, df in dataframes.items():
        if df is not None and len(df) > 10:
            ind = calculate_indicators(df)
            if tf == '15m':
                result.tf_15m = ind
            elif tf == '1h':
                result.tf_1h = ind
            elif tf == '4h':
                result.tf_4h = ind
            elif tf == '1d':
                result.tf_1d = ind
    # Вычисляем confidence_score на основе комбинации индикаторов
    result.confidence_score = compute_confidence_score(result)
    return result

def compute_confidence_score(mtf: MultiTimeframeIndicators) -> float:
    """Вычисляет общий скор уверенности (0-100) на основе всех таймфреймов."""
    score = 0.0
    weights = {'15m': 0.2, '1h': 0.3, '4h': 0.3, '1d': 0.2}
    for tf, ind in [('15m', mtf.tf_15m), ('1h', mtf.tf_1h), ('4h', mtf.tf_4h), ('1d', mtf.tf_1d)]:
        if ind is None:
            continue
        w = weights.get(tf, 0.25)
        # Критерии
        trend = 1 if ind.ema20 > ind.ema50 else 0
        rsi_ok = 1 if 40 < ind.rsi < 75 else 0
        adx_strong = 1 if ind.adx > 25 else 0
        macd_bull = 1 if ind.macd > ind.macd_signal else 0
        bb_ok = 1 if ind.price < ind.bb_high and ind.price > ind.bb_low else 0
        vol_ok = 1 if ind.volume_relative > 1.2 else 0
        pullback_ok = 1 if ind.pullback else 0
        breakout_ok = 1 if ind.breakout_up or ind.breakout_down else 0
        # Суммируем
        sub_score = (trend + rsi_ok + adx_strong + macd_bull + bb_ok + vol_ok + pullback_ok + breakout_ok) / 8 * 100
        score += sub_score * w
    # Дополнительный бонус за согласованность таймфреймов
    if mtf.tf_15m and mtf.tf_1h and mtf.tf_4h:
        if mtf.tf_15m.ema20 > mtf.tf_15m.ema50 and mtf.tf_1h.ema20 > mtf.tf_1h.ema50 and mtf.tf_4h.ema20 > mtf.tf_4h.ema50:
            score += 10
    return min(score, 100.0)