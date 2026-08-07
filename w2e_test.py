"""Offline tests for W2E scoring. No network and no publication."""
from monetization import ConversionIntentEvaluator, score_market_monetization


def main() -> None:
    hot = score_market_monetization(
        quote_volume_24h=650_000_000,
        trade_count_24h=1_200_000,
        abs_change_24h=7.5,
        trend_rank=4,
        trend_universe_size=80,
        attention_score=78,
        change_15m=1.3,
        volume_spike=2.8,
        risk_reward=1.9,
        overextended=False,
    )
    cold = score_market_monetization(
        quote_volume_24h=9_000_000,
        trade_count_24h=8_000,
        abs_change_24h=0.4,
        trend_rank=70,
        trend_universe_size=80,
        attention_score=26,
        change_15m=0.08,
        volume_spike=0.8,
        risk_reward=1.05,
        overextended=False,
    )
    assert hot.score > cold.score + 30, (hot, cold)

    evaluator = ConversionIntentEvaluator()
    useful = evaluator.report(
        "$TEST уже двигается, но я бы не догонял цену.\n\n"
        "Для меня ключевой вопрос — удержит ли рынок уровень 1.25. "
        "Если закрепления нет, сценарий отменяю и пропускаю вход.",
        "TEST",
    )
    spam = evaluator.report(
        "СРОЧНО ПОКУПАЙ TEST! 100% гарантировано без риска! Точно даст иксы!!!",
        "TEST",
    )
    assert useful.score >= 72, useful
    assert useful.score > spam.score + 25, (useful, spam)
    print(f"W2E: OK | hot={hot.score:.1f} cold={cold.score:.1f} useful={useful.score:.1f} spam={spam.score:.1f}")


if __name__ == "__main__":
    main()
