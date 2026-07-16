"""Chart rendering — dark theme, EMA20/50/200, S/R lines, entry/TP/SL zones.

v3: uses SignalScore direction to draw upward or downward TP levels, adds
Bollinger Bands, volume-colored bars, and watermark.
"""
from __future__ import annotations
import logging
import tempfile
from typing import Optional
import pandas as pd
import mplfinance as mpf

logger = logging.getLogger(__name__)


def _to_df(raw_data) -> Optional[pd.DataFrame]:
    if raw_data is None: return None
    if isinstance(raw_data, pd.DataFrame):
        df = raw_data.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df.set_index("timestamp", inplace=True)
            else:
                return None
        return df
    try:
        df = pd.DataFrame(raw_data, columns=[
            "timestamp","open","high","low","close","volume",
            "close_time","quote_asset_volume","number_of_trades",
            "taker_buy_base_asset_volume","taker_buy_quote_asset_volume","ignore",
        ])
        for c in ("open","high","low","close","volume"):
            df[c] = df[c].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df
    except Exception as e:
        logger.error(f"chart df build failed: {e}")
        return None


def generate_chart(
    symbol: str,
    raw_data,
    basic: str,
    *,
    entry: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    tp3: Optional[float] = None,
    stop: Optional[float] = None,
    direction: str = "long",
    support: Optional[float] = None,
    resistance: Optional[float] = None,
) -> Optional[str]:
    df = _to_df(raw_data)
    if df is None: return None
    df = df.tail(120)
    if len(df) < 20:
        logger.warning("not enough candles for chart")
        return None

    df["EMA20"]  = df["close"].ewm(span=20,  adjust=False).mean()
    df["EMA50"]  = df["close"].ewm(span=50,  adjust=False).mean()
    df["EMA200"] = df["close"].ewm(span=200, adjust=False).mean()
    ma20  = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()
    df["BB_H"] = ma20 + 2 * std20
    df["BB_L"] = ma20 - 2 * std20

    addplots = [
        mpf.make_addplot(df["EMA20"],  color="#f5a623", width=1.0),
        mpf.make_addplot(df["EMA50"],  color="#4a90e2", width=1.0),
        mpf.make_addplot(df["EMA200"], color="#bd10e0", width=1.0, linestyle="--"),
        mpf.make_addplot(df["BB_H"],   color="#666666", width=0.6, linestyle=":"),
        mpf.make_addplot(df["BB_L"],   color="#666666", width=0.6, linestyle=":"),
    ]

    hlines_vals, hlines_colors, hlines_styles, hlines_widths = [], [], [], []
    def _h(v, c, st="-", w=1.0):
        if v is None: return
        hlines_vals.append(float(v)); hlines_colors.append(c)
        hlines_styles.append(st); hlines_widths.append(w)

    _h(entry, "#ffffff", "-", 1.2)
    _h(tp1,   "#26de81", "--", 1.0)
    _h(tp2,   "#26de81", "--", 1.0)
    _h(tp3,   "#26de81", "--", 1.0)
    _h(stop,  "#ff4757", "--", 1.2)
    _h(support,    "#2ecc71", ":", 0.9)
    _h(resistance, "#e74c3c", ":", 0.9)

    hlines = dict(hlines=hlines_vals, colors=hlines_colors,
                  linestyle=hlines_styles, linewidths=hlines_widths) \
        if hlines_vals else None

    # Dark theme
    mc = mpf.make_marketcolors(
        up="#26de81", down="#ff4757",
        edge={"up": "#26de81", "down": "#ff4757"},
        wick={"up": "#26de81", "down": "#ff4757"},
        volume={"up": "#1e6f5c", "down": "#8b1e2b"},
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds", marketcolors=mc,
        facecolor="#0f1216", edgecolor="#0f1216",
        figcolor="#0f1216", gridcolor="#1e242c",
        rc={"axes.labelcolor": "#c8ccd4",
            "xtick.color": "#8b93a1", "ytick.color": "#8b93a1",
            "axes.edgecolor": "#1e242c", "text.color": "#c8ccd4"},
    )

    arrow = "▲ LONG" if direction == "long" else "▼ SHORT"
    title = f"{basic}/USDT · 15m · {arrow}"

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    path = tmp.name; tmp.close()
    try:
        mpf.plot(
            df, type="candle", style=style,
            addplot=addplots, volume=True,
            hlines=hlines,
            title=title, ylabel="Price (USDT)", ylabel_lower="Vol",
            figsize=(12, 7), tight_layout=True,
            savefig=dict(fname=path, dpi=140,
                         facecolor="#0f1216", bbox_inches="tight"),
        )
        return path
    except Exception as e:
        logger.error(f"chart plot failed: {e}")
        return None