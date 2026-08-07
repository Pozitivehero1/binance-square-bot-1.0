"""Offline acceptance test for the current-attention ranking model."""
from __future__ import annotations

import pandas as pd

from attention import compute_attention
from indicators import calculate_multi_timeframe
from self_test import _build_setup


def _quiet_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.loc[result.index[-1], "close"] = float(result["close"].iloc[-2]) * 1.0002
    result.loc[result.index[-1], "open"] = float(result["close"].iloc[-2])
    result.loc[result.index[-1], "high"] = max(float(result["open"].iloc[-1]), float(result["close"].iloc[-1])) * 1.0005
    result.loc[result.index[-1], "low"] = min(float(result["open"].iloc[-1]), float(result["close"].iloc[-1])) * 0.9995
    result.loc[result.index[-1], "volume"] = float(result["volume"].iloc[-21:-1].median()) * 0.7
    return result


def main() -> None:
    hot_frames = _build_setup("long")
    hot_mtf = calculate_multi_timeframe("HOTUSDT", hot_frames)
    assert hot_mtf.tf_15m is not None
    hot = compute_attention(hot_frames["15m"], hot_mtf.tf_15m, "long")

    quiet_frames = dict(hot_frames)
    quiet_frames["15m"] = _quiet_frame(hot_frames["15m"])
    quiet_mtf = calculate_multi_timeframe("QUIETUSDT", quiet_frames)
    assert quiet_mtf.tf_15m is not None
    quiet = compute_attention(quiet_frames["15m"], quiet_mtf.tf_15m, "long")

    assert hot.volume_spike > quiet.volume_spike
    assert hot.score > quiet.score, (hot, quiet)
    assert 0 <= hot.score <= 100 and 0 <= quiet.score <= 100
    print(
        f"ATTENTION: OK | hot={hot.score:.1f} vol=x{hot.volume_spike:.2f} "
        f"| quiet={quiet.score:.1f} vol=x{quiet.volume_spike:.2f}"
    )


if __name__ == "__main__":
    main()
