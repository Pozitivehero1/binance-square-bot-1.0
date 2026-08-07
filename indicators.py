"""Technical indicators and direction-aware trade-level calculations."""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    average_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    average_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + relative_strength))
    result = result.where(average_loss != 0, 100.0)
    result = result.where(average_gain != 0, 0.0)
    return result


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    previous_close = close.shift(1)
    return pd.concat(
        [(high - low), (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    return _true_range(high, low, close).ewm(
        alpha=1 / window, adjust=False, min_periods=window
    ).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )
    atr_series = _atr(high, low, close, window).replace(0, np.nan)
    plus_di = 100.0 * plus_dm.ewm(
        alpha=1 / window, adjust=False, min_periods=window
    ).mean() / atr_series
    minus_di = 100.0 * minus_dm.ewm(
        alpha=1 / window, adjust=False, min_periods=window
    ).mean() / atr_series
    denominator = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denominator
    return dx.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()



@dataclass
class IndicatorResult:
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
    volume_relative: float
    swing_high: float
    swing_low: float
    support: float
    resistance: float
    breakout_up: bool
    breakout_down: bool
    liquidity_sweep: bool
    liquidity_sweep_up: bool
    liquidity_sweep_down: bool
    pullback: bool
    pullback_long: bool
    pullback_short: bool
    trend_continuation: bool
    trend_continuation_long: bool
    trend_continuation_short: bool
    false_breakout: bool
    false_breakout_up: bool
    false_breakout_down: bool
    atr_stop: float
    risk_reward: float
    risk_reward_long: float
    risk_reward_short: float


@dataclass
class MultiTimeframeIndicators:
    symbol: str
    tf_15m: Optional[IndicatorResult] = None
    tf_1h: Optional[IndicatorResult] = None
    tf_4h: Optional[IndicatorResult] = None
    tf_1d: Optional[IndicatorResult] = None
    confidence_score: float = 0.0
    confidence_long: float = 0.0
    confidence_short: float = 0.0


def _interval_to_minutes(interval: str) -> Optional[int]:
    match = re.fullmatch(r"(\d+)([mhdw])", interval.strip())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    multiplier = {"m": 1, "h": 60, "d": 1440, "w": 10080}[unit]
    return value * multiplier


def _safe_float(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


class IndicatorsCalculator:
    """Calculates indicators for one candle interval using only closed candles."""

    MIN_CANDLES = 60

    def __init__(self, df: pd.DataFrame, interval: str = "15m"):
        if df is None or len(df) < self.MIN_CANDLES:
            raise ValueError(f"At least {self.MIN_CANDLES} candles are required")
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            raise ValueError(f"Missing columns: {sorted(required - set(df.columns))}")

        frame = df.copy().sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        for column in required:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=list(required))
        if len(frame) < self.MIN_CANDLES:
            raise ValueError("Not enough valid candles after cleanup")

        self.df = frame
        self.interval = interval
        self.interval_minutes = _interval_to_minutes(interval)

    def _calc_change_hours(self, hours: int) -> float:
        if not self.interval_minutes:
            return 0.0
        target_minutes = hours * 60
        if self.interval_minutes > target_minutes:
            return 0.0
        bars = max(1, int(round(target_minutes / self.interval_minutes)))
        if len(self.df) <= bars:
            return 0.0
        previous = float(self.df["close"].iloc[-bars - 1])
        current = float(self.df["close"].iloc[-1])
        return ((current - previous) / previous * 100.0) if previous else 0.0

    def calculate_all(self) -> IndicatorResult:
        frame = self.df
        close = frame["close"]
        high = frame["high"]
        low = frame["low"]
        volume = frame["volume"]
        price = float(close.iloc[-1])

        change_1h = self._calc_change_hours(1)
        change_4h = self._calc_change_hours(4)
        change_24h = self._calc_change_hours(24)

        rsi_series = _rsi(close, window=14)
        rsi = _safe_float(rsi_series.iloc[-1], 50.0)

        ema20_series = close.ewm(span=20, adjust=False).mean()
        ema50_series = close.ewm(span=50, adjust=False).mean()
        ema200_series = close.ewm(span=200, adjust=False).mean()
        ema20 = _safe_float(ema20_series.iloc[-1], price)
        ema50 = _safe_float(ema50_series.iloc[-1], price)
        ema200 = _safe_float(ema200_series.iloc[-1], price)

        atr_series = _atr(high, low, close, window=14)
        atr = max(_safe_float(atr_series.iloc[-1], price * 0.01), price * 0.0001)

        adx_series = _adx(high, low, close, window=14)
        adx = _safe_float(adx_series.iloc[-1], 20.0)

        macd_series = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        macd_signal_series = macd_series.ewm(span=9, adjust=False).mean()
        macd_hist_series = macd_series - macd_signal_series
        macd = _safe_float(macd_series.iloc[-1], 0.0)
        macd_signal = _safe_float(macd_signal_series.iloc[-1], 0.0)
        macd_hist = _safe_float(macd_hist_series.iloc[-1], 0.0)

        typical = (high + low + close) / 3.0
        rolling_volume = volume.rolling(50, min_periods=10).sum()
        rolling_value = (typical * volume).rolling(50, min_periods=10).sum()
        vwap_series = rolling_value / rolling_volume.replace(0, np.nan)
        vwap = _safe_float(vwap_series.iloc[-1], price)

        bb_mid_series = close.rolling(20).mean()
        bb_std_series = close.rolling(20).std(ddof=0)
        bb_high_series = bb_mid_series + 2.0 * bb_std_series
        bb_low_series = bb_mid_series - 2.0 * bb_std_series
        bb_high = _safe_float(bb_high_series.iloc[-1], price + atr * 2)
        bb_low = _safe_float(bb_low_series.iloc[-1], price - atr * 2)
        bb_mid = _safe_float(bb_mid_series.iloc[-1], price)

        direction_sign = np.sign(close.diff()).fillna(0.0)
        obv_series = (direction_sign * volume).cumsum()
        obv = _safe_float(obv_series.iloc[-1], 0.0)

        typical_price = (high + low + close) / 3.0
        typical_mean = typical_price.rolling(20).mean()
        mean_deviation = typical_price.rolling(20).apply(
            lambda values: float(np.mean(np.abs(values - np.mean(values)))), raw=True
        )
        cci_series = (typical_price - typical_mean) / (0.015 * mean_deviation.replace(0, np.nan))
        cci = _safe_float(cci_series.iloc[-1], 0.0)

        rsi_min = rsi_series.rolling(14).min()
        rsi_max = rsi_series.rolling(14).max()
        stoch_rsi = (rsi_series - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
        stoch_k_series = stoch_rsi.rolling(3).mean() * 100.0
        stoch_d_series = stoch_k_series.rolling(3).mean()
        stoch_rsi_k = _safe_float(stoch_k_series.iloc[-1], 50.0)
        stoch_rsi_d = _safe_float(stoch_d_series.iloc[-1], 50.0)

        previous_volume = volume.iloc[-21:-1]
        volume_average = float(previous_volume.mean()) if len(previous_volume) else 0.0
        volume_relative = float(volume.iloc[-1] / volume_average) if volume_average > 0 else 1.0
        volume_relative = min(max(volume_relative, 0.0), 20.0)

        prior_20 = frame.iloc[-21:-1]
        prior_50 = frame.iloc[-51:-1] if len(frame) >= 51 else frame.iloc[:-1]
        swing_high = float(prior_20["high"].max())
        swing_low = float(prior_20["low"].min())
        resistance = float(prior_50["high"].max())
        support = float(prior_50["low"].min())

        previous_close = float(close.iloc[-2])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        atr_pct = atr / price if price else 0.0
        breakout_buffer = max(0.001, min(0.004, atr_pct * 0.12))

        breakout_up = (
            price > resistance * (1.0 + breakout_buffer)
            and previous_close <= resistance * (1.0 + breakout_buffer)
        )
        breakout_down = (
            price < support * (1.0 - breakout_buffer)
            and previous_close >= support * (1.0 - breakout_buffer)
        )

        liquidity_sweep_up = current_high > swing_high * (1.0 + breakout_buffer / 2) and price < swing_high
        liquidity_sweep_down = current_low < swing_low * (1.0 - breakout_buffer / 2) and price > swing_low
        liquidity_sweep = liquidity_sweep_up or liquidity_sweep_down

        bullish_trend = ema20 > ema50 and ema50 >= ema200 * 0.995
        bearish_trend = ema20 < ema50 and ema50 <= ema200 * 1.005
        near_ema20 = abs(price - ema20) <= atr * 0.45

        pullback_long = bullish_trend and near_ema20 and price >= ema20 and current_low <= ema20 + atr * 0.15
        pullback_short = bearish_trend and near_ema20 and price <= ema20 and current_high >= ema20 - atr * 0.15
        pullback = pullback_long or pullback_short

        trend_continuation_long = bullish_trend and price > ema20 and macd_hist > 0
        trend_continuation_short = bearish_trend and price < ema20 and macd_hist < 0
        trend_continuation = trend_continuation_long or trend_continuation_short

        false_breakout_up = current_high > resistance * (1.0 + breakout_buffer / 2) and price < resistance
        false_breakout_down = current_low < support * (1.0 - breakout_buffer / 2) and price > support
        false_breakout = false_breakout_up or false_breakout_down

        result = IndicatorResult(
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
            liquidity_sweep_up=liquidity_sweep_up,
            liquidity_sweep_down=liquidity_sweep_down,
            pullback=pullback,
            pullback_long=pullback_long,
            pullback_short=pullback_short,
            trend_continuation=trend_continuation,
            trend_continuation_long=trend_continuation_long,
            trend_continuation_short=trend_continuation_short,
            false_breakout=false_breakout,
            false_breakout_up=false_breakout_up,
            false_breakout_down=false_breakout_down,
            atr_stop=atr * 1.5,
            risk_reward=0.0,
            risk_reward_long=0.0,
            risk_reward_short=0.0,
        )

        long_levels = build_trade_levels(result, "long")
        short_levels = build_trade_levels(result, "short")
        result.risk_reward_long = long_levels["risk_reward"]
        result.risk_reward_short = short_levels["risk_reward"]
        result.risk_reward = max(result.risk_reward_long, result.risk_reward_short)
        return result


def build_trade_levels(ind: IndicatorResult, direction: str) -> Dict[str, float]:
    """Build a technically bounded plan and return the real R/R to TP3."""
    if direction not in {"long", "short"}:
        raise ValueError("direction must be 'long' or 'short'")

    entry = float(ind.price)
    atr = max(float(ind.atr), entry * 0.0001)
    base_risk = atr * 1.45

    if direction == "long":
        if ind.breakout_up and ind.resistance < entry:
            structural_stop = float(ind.resistance) - atr * 0.22
            structural_distance = entry - structural_stop
            risk_distance = min(max(structural_distance, atr * 1.0), atr * 1.8)
        else:
            structural_stop = float(ind.support) - atr * 0.12
            structural_distance = entry - structural_stop if structural_stop < entry else base_risk
            risk_distance = min(max(structural_distance, atr * 1.0), atr * 1.8)
        stop = entry - risk_distance

        favorable = [
            value
            for value in (ind.resistance, ind.swing_high, ind.bb_high)
            if value is not None and float(value) > entry + atr * 0.25
        ]
        raw_target = max((float(value) for value in favorable), default=entry)
        if ind.breakout_up:
            measured_move = max((ind.resistance - ind.support) * 0.65, atr * 3.0)
            raw_target = max(raw_target, entry + measured_move)
        elif raw_target <= entry:
            measured_move = max((ind.resistance - ind.support) * 0.45, atr * 1.6)
            raw_target = entry + measured_move
        reward_distance = max(0.0, raw_target - entry)
        reward_distance = min(reward_distance, risk_distance * 5.0, atr * 7.0)
        tp3 = entry + reward_distance

        tp1_distance = min(risk_distance, reward_distance * 0.50)
        tp2_distance = min(risk_distance * 1.6, reward_distance * 0.80)
        tp1 = entry + tp1_distance
        tp2 = entry + max(tp2_distance, tp1_distance + atr * 0.05)
        tp3 = max(tp3, tp2 + atr * 0.05)
    else:
        if ind.breakout_down and ind.support > entry:
            structural_stop = float(ind.support) + atr * 0.22
            structural_distance = structural_stop - entry
            risk_distance = min(max(structural_distance, atr * 1.0), atr * 1.8)
        else:
            structural_stop = float(ind.resistance) + atr * 0.12
            structural_distance = structural_stop - entry if structural_stop > entry else base_risk
            risk_distance = min(max(structural_distance, atr * 1.0), atr * 1.8)
        stop = entry + risk_distance

        favorable = [
            value
            for value in (ind.support, ind.swing_low, ind.bb_low)
            if value is not None and float(value) < entry - atr * 0.25
        ]
        raw_target = min((float(value) for value in favorable), default=entry)
        if ind.breakout_down:
            measured_move = max((ind.resistance - ind.support) * 0.65, atr * 3.0)
            raw_target = min(raw_target, entry - measured_move)
        elif raw_target >= entry:
            measured_move = max((ind.resistance - ind.support) * 0.45, atr * 1.6)
            raw_target = entry - measured_move
        reward_distance = max(0.0, entry - raw_target)
        reward_distance = min(reward_distance, risk_distance * 5.0, atr * 7.0)
        tp3 = entry - reward_distance

        tp1_distance = min(risk_distance, reward_distance * 0.50)
        tp2_distance = min(risk_distance * 1.6, reward_distance * 0.80)
        tp1 = entry - tp1_distance
        tp2 = entry - max(tp2_distance, tp1_distance + atr * 0.05)
        tp3 = min(tp3, tp2 - atr * 0.05)

    actual_reward = abs(tp3 - entry)
    risk_reward = actual_reward / risk_distance if risk_distance > 0 else 0.0
    return {
        "entry": entry,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stop": stop,
        "risk": risk_distance,
        "risk_reward": risk_reward,
    }


def calculate_multi_timeframe(
    symbol: str,
    data_by_tf: Dict[str, pd.DataFrame],
) -> MultiTimeframeIndicators:
    result = MultiTimeframeIndicators(symbol=symbol)
    mapping = {"15m": "tf_15m", "1h": "tf_1h", "4h": "tf_4h", "1d": "tf_1d"}

    for interval, attribute in mapping.items():
        frame = data_by_tf.get(interval)
        if frame is None:
            continue
        try:
            indicator = IndicatorsCalculator(frame, interval=interval).calculate_all()
            setattr(result, attribute, indicator)
        except Exception as exc:
            logger.warning("Indicator calculation failed for %s %s: %s", symbol, interval, exc)

    result.confidence_long = compute_confidence_score(result, "long")
    result.confidence_short = compute_confidence_score(result, "short")
    result.confidence_score = max(result.confidence_long, result.confidence_short)
    return result


def compute_confidence_score(mtf: MultiTimeframeIndicators, direction: str) -> float:
    """Direction-aware confluence score. It is not a calibrated win probability."""
    if direction not in {"long", "short"}:
        raise ValueError("direction must be 'long' or 'short'")

    weighted_score = 0.0
    used_weight = 0.0
    weights = {"15m": 0.20, "1h": 0.30, "4h": 0.30, "1d": 0.20}

    for interval, ind in (
        ("15m", mtf.tf_15m),
        ("1h", mtf.tf_1h),
        ("4h", mtf.tf_4h),
        ("1d", mtf.tf_1d),
    ):
        if ind is None:
            continue
        weight = weights[interval]
        used_weight += weight

        if direction == "long":
            checks = [
                ind.ema20 > ind.ema50,
                ind.price > ind.ema20,
                ind.macd_hist > 0,
                42 <= ind.rsi <= 70,
                ind.price >= ind.vwap,
                ind.breakout_up or ind.pullback_long or ind.trend_continuation_long,
            ]
            opposing_false_breakout = ind.false_breakout_up
        else:
            checks = [
                ind.ema20 < ind.ema50,
                ind.price < ind.ema20,
                ind.macd_hist < 0,
                30 <= ind.rsi <= 58,
                ind.price <= ind.vwap,
                ind.breakout_down or ind.pullback_short or ind.trend_continuation_short,
            ]
            opposing_false_breakout = ind.false_breakout_down

        checks.extend([ind.adx >= 20, ind.volume_relative >= 1.0])
        sub_score = sum(bool(check) for check in checks) / len(checks) * 100.0
        if opposing_false_breakout:
            sub_score -= 18.0
        weighted_score += max(0.0, sub_score) * weight

    if used_weight <= 0:
        return 0.0
    normalized = weighted_score / used_weight
    return min(max(normalized, 0.0), 100.0)
