"""Trending symbols from Binance (top USDT pairs by volume × movement)."""
from __future__ import annotations
import logging
import requests
from typing import List

logger = logging.getLogger(__name__)

BINANCE_24H = "https://data-api.binance.vision/api/v3/ticker/24hr"

STABLES = {"USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP"}
BLACKLIST_QUOTE = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT"}


def get_trending_symbols(limit: int = 80) -> List[str]:
    """Return top USDT pairs ranked by |priceChangePercent| * quoteVolume."""
    try:
        r = requests.get(BINANCE_24H, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.error(f"get_trending_symbols: {e}")
        return []

    rows = []
    for item in data:
        sym = item.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        if sym in BLACKLIST_QUOTE:
            continue
        base = sym[:-4]
        if base in STABLES or not base:
            continue
        # Skip leveraged tokens (UP/DOWN/BULL/BEAR)
        if any(base.endswith(suf) for suf in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        try:
            change_pct = abs(float(item.get("priceChangePercent", 0)))
            qv = float(item.get("quoteVolume", 0))
        except (TypeError, ValueError):
            continue
        if qv < 5_000_000:  # too illiquid
            continue
        score = change_pct * (qv ** 0.5)
        rows.append((sym, score))

    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows[:limit]]


def get_base_asset(symbol: str) -> str:
    """BTCUSDT -> BTC."""
    for quote in ("USDT", "BUSD", "USDC", "FDUSD"):
        if symbol.endswith(quote):
            return symbol[: -len(quote)]
    return symbol