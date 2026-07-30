"""Offline 150-post anti-repetition stress test.

The test uses the exact candidate-selection pipeline with persistent memory,
repeated market structures, alternating LONG/SHORT directions and ten tickers.
It never calls market APIs, Mistral or the publisher.
"""
from __future__ import annotations

import logging
import os
import statistics
import tempfile
from pathlib import Path
from unittest.mock import patch

from filters import SignalFilter
from indicators import build_trade_levels, calculate_multi_timeframe
from main import MAX_POST_SIMILARITY, _best_post_variant
from memory import PostMemory
from self_test import _build_setup


def main() -> None:
    logging.getLogger().setLevel(logging.ERROR)
    symbols = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "AVAX", "SUI")
    similarities = []

    with tempfile.TemporaryDirectory() as temp_directory:
        memory = PostMemory(Path(temp_directory) / "post_memory.json")
        for index in range(150):
            direction = "long" if index % 2 == 0 else "short"
            basic = symbols[index % len(symbols)]
            frames = _build_setup(direction)
            mtf = calculate_multi_timeframe(f"{basic}USDT", frames)
            score = SignalFilter(min_score=0).evaluate(mtf)
            assert score is not None
            levels = build_trade_levels(mtf.tf_15m, direction)

            with patch.dict(os.environ, {"CONTENT_MODE": "deterministic"}, clear=False):
                selected = _best_post_variant(
                    symbol=f"{basic}USDT",
                    basic=basic,
                    mtf=mtf,
                    score=score,
                    levels=levels,
                    memory=memory,
                    btc=None,
                )
            assert selected is not None, f"No acceptable post at sequence #{index + 1}"
            draft, _ = selected
            similarity = memory.similarity_score(draft.text)
            assert similarity < MAX_POST_SIMILARITY, (
                f"Post #{index + 1} similarity {similarity:.3f} reached "
                f"the gate {MAX_POST_SIMILARITY:.3f}"
            )
            if index:
                similarities.append(similarity)
            memory.add_post(
                f"{basic}USDT",
                draft.text,
                post_style=draft.style_id,
                signal_type=draft.signal_type,
            )

    p95 = statistics.quantiles(similarities, n=20)[18]
    print(
        "REPETITION STRESS: OK | posts=150 | "
        f"max_similarity={max(similarities):.3f} | "
        f"avg_similarity={statistics.mean(similarities):.3f} | "
        f"p95={p95:.3f} | gate={MAX_POST_SIMILARITY:.2f}"
    )
    print("No market, Mistral or publishing network call was attempted.")


if __name__ == "__main__":
    main()
