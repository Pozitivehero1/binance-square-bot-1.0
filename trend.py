"""Trending Binance USDT symbols ranked by attention, liquidity and movement."""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import Dict, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BINANCE_24H_ENDPOINTS = (
    "https://data-api.binance.vision/api/v3/ticker/24hr",
    "https://api.binance.com/api/v3/ticker/24hr",
)
MIN_QUOTE_VOLUME = float(os.getenv("MIN_QUOTE_VOLUME", "8000000"))

STABLES = {
    "USDT",
    "USDC",
    "BUSD",
    "TUSD",
    "FDUSD",
    "DAI",
    "USDP",
    "EUR",
    "AEUR",
    "TRY",
    "BRL",
    "GBP",
    "AUD",
}
BLACKLIST_PAIRS = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "EURUSDT"}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


@dataclass(frozen=True)
class TrendingMarket:
    symbol: str
    attention_score: float
    quote_volume: float
    trade_count: float
    change_pct: float
    last_price: float
    rank: int



def _session() -> requests.Session:
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


_SESSION = _session()


def get_trending_market(limit: int = 80) -> List[TrendingMarket]:
    """Return liquid markets with metadata used by selection and W2E ranking."""
    payload = None
    last_error = None
    for endpoint in BINANCE_24H_ENDPOINTS:
        try:
            response = _SESSION.get(endpoint, timeout=(5, 20))
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:
            last_error = exc
            logger.debug("Ticker endpoint %s failed: %s", endpoint, exc)
    if payload is None:
        logger.error("get_trending_market failed: %s", last_error)
        return []

    rows = []
    for item in payload if isinstance(payload, list) else []:
        symbol = str(item.get("symbol", "")).upper()
        if not symbol.endswith("USDT") or symbol in BLACKLIST_PAIRS:
            continue
        base = symbol[:-4]
        if not base or base in STABLES or base.endswith(LEVERAGED_SUFFIXES):
            continue
        try:
            quote_volume = float(item.get("quoteVolume", 0.0))
            raw_change = float(item.get("priceChangePercent", 0.0))
            change_pct = abs(raw_change)
            trade_count = max(float(item.get("count", 0.0)), 0.0)
            last_price = float(item.get("lastPrice", 0.0))
        except (TypeError, ValueError):
            continue
        if quote_volume < MIN_QUOTE_VOLUME or last_price <= 0:
            continue
        movement_component = min(change_pct, 35.0) ** 0.85
        liquidity_component = math.log10(max(quote_volume, 1.0))
        activity_component = math.log10(max(trade_count, 1.0))
        attention_score = movement_component * 0.52 + liquidity_component * 2.4 + activity_component * 1.1
        rows.append((symbol, attention_score, quote_volume, trade_count, raw_change, last_price))

    rows.sort(key=lambda row: (row[1], row[2]), reverse=True)
    out = []
    for rank, row in enumerate(rows[: max(1, int(limit))], start=1):
        symbol, attention_score, quote_volume, trade_count, raw_change, last_price = row
        out.append(TrendingMarket(symbol, attention_score, quote_volume, trade_count, raw_change, last_price, rank))
    return out


def get_trending_symbols(limit: int = 80) -> List[str]:
    """Backward-compatible symbol-only view."""
    return [item.symbol for item in get_trending_market(limit=limit)]


def get_base_asset(symbol: str) -> str:
    symbol = symbol.upper()
    for quote in ("USDT", "BUSD", "USDC", "FDUSD"):
        if symbol.endswith(quote):
            return symbol[: -len(quote)]
    return symbol
