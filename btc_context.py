"""BTC market context — direction bias + funding rate sanity check."""
from __future__ import annotations
import logging
import requests
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

BINANCE_24H_ONE = "https://data-api.binance.vision/api/v3/ticker/24hr"
BINANCE_KLINES = "https://data-api.binance.vision/api/v3/klines"
BINANCE_FUTURES_FUNDING = "https://fapi.binance.com/fapi/v1/premiumIndex"


@dataclass
class BTCContext:
    price: float
    change_1h: float
    change_4h: float
    change_24h: float
    bias: str  # 'bullish' | 'bearish' | 'neutral'


def _pct_change(klines) -> float:
    if not klines or len(klines) < 2:
        return 0.0
    try:
        opn = float(klines[0][1])
        cls = float(klines[-1][4])
        return (cls - opn) / opn * 100 if opn else 0.0
    except (TypeError, ValueError, IndexError):
        return 0.0


def get_btc_context() -> Optional[BTCContext]:
    try:
        r = requests.get(
            BINANCE_24H_ONE,
            params={"symbol": "BTCUSDT"},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        d = r.json()
        price = float(d["lastPrice"])
        change_24h = float(d["priceChangePercent"])

        r1 = requests.get(BINANCE_KLINES, params={"symbol": "BTCUSDT", "interval": "1h", "limit": 2}, timeout=10)
        r4 = requests.get(BINANCE_KLINES, params={"symbol": "BTCUSDT", "interval": "1h", "limit": 5}, timeout=10)
        r1.raise_for_status(); r4.raise_for_status()
        change_1h = _pct_change(r1.json())
        change_4h = _pct_change(r4.json())
    except Exception as e:
        logger.warning(f"get_btc_context: {e}")
        return None

    # Bias: require agreement across two horizons
    if change_1h > 0.4 and change_4h > 0.8:
        bias = "bullish"
    elif change_1h < -0.4 and change_4h < -0.8:
        bias = "bearish"
    else:
        bias = "neutral"

    return BTCContext(price=price, change_1h=change_1h,
                      change_4h=change_4h, change_24h=change_24h, bias=bias)


def is_direction_compatible(direction: str, btc: BTCContext) -> bool:
    """Reject counter-trend alt setups when BTC is strongly biased."""
    if btc.bias == "neutral":
        return True
    if btc.bias == "bullish" and direction == "short":
        return False
    if btc.bias == "bearish" and direction == "long":
        return False
    return True


def get_funding_rate(symbol: str) -> Optional[float]:
    """Perpetual funding rate for symbol (e.g. 0.0001 = 0.01%)."""
    try:
        r = requests.get(BINANCE_FUTURES_FUNDING, params={"symbol": symbol}, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        d = r.json()
        return float(d.get("lastFundingRate", 0)) if isinstance(d, dict) else None
    except Exception as e:
        logger.debug(f"funding {symbol}: {e}")
        return None
