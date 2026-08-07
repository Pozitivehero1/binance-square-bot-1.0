"""Offline copy-regression matrix for aligned and counter-trend hot moves."""
from __future__ import annotations

from content_strategy import headline_candidates


def _headlines(direction: str, move: float) -> list[str]:
    return headline_candidates(
        ticker="$TEST",
        direction=direction,
        format_id="hot_reaction",
        key_level="0.0464",
        risk_pct="2.0%",
        reward_pct="4.0%",
        rsi=55.0,
        adx=24.0,
        price_vs_vwap="выше",
        angle_title="тест",
        change_15m=move,
        volume_spike=8.0,
    )


def main() -> None:
    aligned_long = _headlines("long", +4.2)
    aligned_short = _headlines("short", -4.2)
    counter_short = _headlines("short", +4.2)
    counter_long = _headlines("long", -4.2)

    assert any("не догонял LONG" in x for x in aligned_long)
    assert any("не догонял SHORT" in x for x in aligned_short)
    assert not any("не догонял SHORT" in x for x in counter_short)
    assert not any("не догонял LONG" in x for x in counter_long)
    assert any("импульс" in x.lower() for x in counter_short)
    assert any("продавц" in x.lower() or "дно" in x.lower() for x in counter_long)
    for group in (aligned_long, aligned_short, counter_short, counter_long):
        assert not any("Направление у идеи" in x for x in group)

    print("COPY MATRIX: OK | aligned/counter-trend headlines are direction-aware")


if __name__ == "__main__":
    main()
