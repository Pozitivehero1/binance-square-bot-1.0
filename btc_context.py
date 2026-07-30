"""BTC market context and perpetual funding sanity checks."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BINANCE_SPOT_BASES = ("https://data-api.binance.vision", "https://api.binance.com")
BINANCE_FUTURES_FUNDING = "https://fapi.binance.com/fapi/v1/premiumIndex"


@dataclass
class BTCContext:
    price: float
    change_1h: float
    change_4h: float
    change_24h: float
    bias: str


def _build_session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update({"User-Agent": "BinanceSquareSignalBot/2.0"})
    session.mount("https://", adapter)
    return session


_SESSION = _build_session()


def _spot_get(path: str, params):
    last_error = None
    for base_url in BINANCE_SPOT_BASES:
        try:
            response = _SESSION.get(f"{base_url}{path}", params=params, timeout=(5, 15))
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            logger.debug("BTC endpoint %s failed: %s", base_url, exc)
    raise RuntimeError(f"All Binance spot endpoints failed: {last_error}")


def _closed_hourly_closes(limit: int = 8):
    response = _spot_get(
        "/api/v3/klines",
        {"symbol": "BTCUSDT", "interval": "1h", "limit": limit + 1},
    )
    now_ms = int(time.time() * 1000)
    return [float(row[4]) for row in response.json() if int(row[6]) < now_ms]


def _change(closes, bars: int) -> float:
    if len(closes) <= bars:
        return 0.0
    previous = float(closes[-bars - 1])
    current = float(closes[-1])
    return (current - previous) / previous * 100.0 if previous else 0.0


def get_btc_context() -> Optional[BTCContext]:
    try:
        ticker_response = _spot_get(
            "/api/v3/ticker/24hr",
            {"symbol": "BTCUSDT"},
        )
        ticker = ticker_response.json()
        price = float(ticker["lastPrice"])
        change_24h = float(ticker["priceChangePercent"])
        closes = _closed_hourly_closes(8)
        change_1h = _change(closes, 1)
        change_4h = _change(closes, 4)
    except Exception as exc:
        logger.warning("get_btc_context failed: %s", exc)
        return None

    if change_1h >= 0.35 and change_4h >= 0.75:
        bias = "bullish"
    elif change_1h <= -0.35 and change_4h <= -0.75:
        bias = "bearish"
    else:
        bias = "neutral"

    return BTCContext(
        price=price,
        change_1h=change_1h,
        change_4h=change_4h,
        change_24h=change_24h,
        bias=bias,
    )


def is_direction_compatible(direction: str, btc: BTCContext) -> bool:
    if btc.bias == "neutral":
        return True
    return (btc.bias == "bullish" and direction == "long") or (
        btc.bias == "bearish" and direction == "short"
    )


def get_funding_rate(symbol: str) -> Optional[float]:
    try:
        response = _SESSION.get(
            BINANCE_FUTURES_FUNDING,
            params={"symbol": symbol.upper()},
            timeout=(5, 12),
        )
        if response.status_code != 200:
            logger.debug("Funding unavailable for %s: HTTP %s", symbol, response.status_code)
            return None
        payload = response.json()
        return float(payload.get("lastFundingRate", 0.0)) if isinstance(payload, dict) else None
    except Exception as exc:
        logger.debug("Funding request failed for %s: %s", symbol, exc)
        return None
