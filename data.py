"""Reliable OHLCV loader with caching, retries and closed-candle filtering."""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from functools import wraps
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BINANCE_APIS = ("https://data-api.binance.vision", "https://api.binance.com")
BYBIT_API = "https://api.bybit.com"
ENABLE_BYBIT_FALLBACK = os.getenv("ENABLE_BYBIT_FALLBACK", "0").lower() in {
    "1", "true", "yes"
}

_BYBIT_INTERVALS = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "2h": "120",
    "4h": "240",
    "6h": "360",
    "12h": "720",
    "1d": "D",
    "1w": "W",
    "1M": "M",
}

_cache: Dict[str, pd.DataFrame] = {}
_cache_expiry: Dict[str, float] = {}
_cache_lock = threading.Lock()


def _build_session() -> requests.Session:
    retry_policy = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_policy, pool_connections=20, pool_maxsize=20)
    session = requests.Session()
    session.headers.update({"User-Agent": "BinanceSquareSignalBot/2.0"})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION = _build_session()


def retry(max_retries: int = 3, delay: float = 0.8, backoff: float = 2.0):
    """Retry transient non-HTTP exceptions with jitter."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            wait = delay
            last_error: Optional[Exception] = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # network and malformed upstream responses
                    last_error = exc
                    if attempt >= max_retries:
                        break
                    sleep_for = wait + random.uniform(0.0, min(0.35, wait / 2))
                    logger.warning(
                        "%s attempt %s/%s failed: %s; retrying",
                        func.__name__,
                        attempt,
                        max_retries,
                        exc,
                    )
                    time.sleep(sleep_for)
                    wait *= backoff
            if last_error is not None:
                raise last_error
            return None

        return wrapper

    return decorator


def _normalize_ohlcv(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    required = ("open", "high", "low", "close", "volume")
    if df is None or df.empty or any(column not in df.columns for column in required):
        return None

    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        if "timestamp" not in out.columns:
            return None
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
        out.set_index("timestamp", inplace=True)

    for column in required:
        out[column] = pd.to_numeric(out[column], errors="coerce")

    out = out[list(required)]
    out = out.replace([float("inf"), float("-inf")], pd.NA).dropna()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out[(out["high"] >= out[["open", "close"]].max(axis=1))]
    out = out[(out["low"] <= out[["open", "close"]].min(axis=1))]
    out = out[(out["volume"] >= 0)]
    return out if len(out) >= 30 else None


class DataFetcher:
    """Fetches market data from Binance, with Bybit as a compatible fallback."""

    def __init__(self, cache_ttl: int = 60):
        self.cache_ttl = max(5, int(cache_ttl))

    @retry(max_retries=3, delay=0.7)
    def fetch_binance_klines(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 250,
    ) -> Optional[pd.DataFrame]:
        # Request one extra row because the newest Binance kline may still be open.
        request_limit = min(max(int(limit) + 1, 31), 1000)
        response = None
        last_error = None
        for base_url in BINANCE_APIS:
            try:
                candidate = _SESSION.get(
                    f"{base_url}/api/v3/klines",
                    params={"symbol": symbol.upper(), "interval": interval, "limit": request_limit},
                    timeout=(5, 18),
                )
                candidate.raise_for_status()
                response = candidate
                break
            except requests.RequestException as exc:
                last_error = exc
                logger.debug("Binance endpoint %s failed: %s", base_url, exc)
        if response is None:
            raise RuntimeError(f"All Binance endpoints failed: {last_error}")
        rows = response.json()
        if not isinstance(rows, list) or len(rows) < 30:
            logger.info("Binance returned insufficient history for %s %s", symbol, interval)
            return None

        frame = pd.DataFrame(
            rows,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_asset_volume",
                "number_of_trades",
                "taker_buy_base_asset_volume",
                "taker_buy_quote_asset_volume",
                "ignore",
            ],
        )
        now_ms = int(time.time() * 1000)
        frame["close_time"] = pd.to_numeric(frame["close_time"], errors="coerce")
        frame = frame[frame["close_time"] < now_ms]
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        frame.set_index("timestamp", inplace=True)
        normalized = _normalize_ohlcv(frame)
        if normalized is None:
            return None
        return normalized.tail(limit).copy()

    @retry(max_retries=2, delay=0.8)
    def fetch_bybit_klines(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 250,
    ) -> Optional[pd.DataFrame]:
        bybit_interval = _BYBIT_INTERVALS.get(interval)
        if bybit_interval is None:
            logger.debug("No Bybit interval mapping for %s", interval)
            return None

        response = _SESSION.get(
            f"{BYBIT_API}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol.upper(),
                "interval": bybit_interval,
                "limit": min(max(int(limit), 30), 1000),
            },
            timeout=(5, 18),
        )
        response.raise_for_status()
        payload: Dict[str, Any] = response.json()
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(f"Bybit error: {payload.get('retMsg', 'unknown error')}")

        rows = payload.get("result", {}).get("list", [])
        if not isinstance(rows, list) or len(rows) < 30:
            return None

        frame = pd.DataFrame(
            rows,
            columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
        )
        frame["timestamp"] = pd.to_datetime(
            pd.to_numeric(frame["timestamp"], errors="coerce"), unit="ms", utc=True
        )
        frame.set_index("timestamp", inplace=True)
        interval_minutes = {
            "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720,
            "1d": 1440, "1w": 10080, "1M": 43200,
        }.get(interval)
        if interval_minutes:
            candle_close = frame.index + pd.Timedelta(minutes=interval_minutes)
            frame = frame[candle_close <= pd.Timestamp.now(tz="UTC")]
        normalized = _normalize_ohlcv(frame)
        return normalized.tail(limit).copy() if normalized is not None else None

    def get_data(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 250,
    ) -> Optional[pd.DataFrame]:
        symbol = symbol.upper().strip()
        cache_key = f"{symbol}:{interval}:{int(limit)}"
        now = time.time()

        with _cache_lock:
            cached = _cache.get(cache_key)
            expires = _cache_expiry.get(cache_key, 0.0)
            if cached is not None and expires > now:
                return cached.copy()

        frame: Optional[pd.DataFrame] = None
        try:
            frame = self.fetch_binance_klines(symbol, interval, limit)
        except Exception as exc:
            logger.warning("Binance data failed for %s %s: %s", symbol, interval, exc)

        if frame is None and ENABLE_BYBIT_FALLBACK:
            try:
                frame = self.fetch_bybit_klines(symbol, interval, limit)
            except Exception as exc:
                logger.warning("Bybit fallback failed for %s %s: %s", symbol, interval, exc)
        elif frame is None:
            logger.debug(
                "Bybit fallback disabled for %s %s; Binance data unavailable",
                symbol,
                interval,
            )

        if frame is None:
            logger.info("No usable market history for %s %s", symbol, interval)
            return None

        with _cache_lock:
            _cache[cache_key] = frame.copy()
            _cache_expiry[cache_key] = now + self.cache_ttl
        return frame.copy()

    def get_multi_timeframe_data(
        self,
        symbol: str,
        intervals: Optional[List[str]] = None,
        limit: int = 250,
    ) -> Dict[str, pd.DataFrame]:
        result: Dict[str, pd.DataFrame] = {}
        for interval in intervals or ["15m", "1h"]:
            frame = self.get_data(symbol, interval, limit)
            if frame is not None:
                result[interval] = frame
        return result


_fetcher = DataFetcher(cache_ttl=60)


def get_data(symbol: str, interval: str = "15m", limit: int = 250) -> Optional[pd.DataFrame]:
    return _fetcher.get_data(symbol, interval, limit)


def get_raw_data(symbol: str, interval: str = "15m", limit: int = 250) -> Optional[List[List[Any]]]:
    frame = get_data(symbol, interval, limit)
    if frame is None:
        return None

    reset = frame.reset_index()
    timestamp_column = reset.columns[0]
    reset["timestamp_ms"] = reset[timestamp_column].astype("int64") // 10**6
    rows = reset[["timestamp_ms", "open", "high", "low", "close", "volume"]].values.tolist()
    for row in rows:
        row.extend([0, 0, 0, 0, 0, 0])
    return rows
