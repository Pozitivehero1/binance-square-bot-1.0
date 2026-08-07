"""Feed-appeal scoring for Binance Square posts.

This is intentionally separate from factual quality. A post can be perfectly
correct and still look like an automated terminal dump. FeedAppealEvaluator
rewards hooks, human language, a single clear idea and a natural CTA while
penalising jargon/numeric density and template-like labels.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict


@dataclass(frozen=True)
class FeedAppealReport:
    score: float
    components: Dict[str, float]


class FeedAppealEvaluator:
    TECHNICAL_TERMS = (
        "rsi", "adx", "vwap", "ema20", "ema50", "r/r", "risk/reward",
        "мульти", "таймфрейм", "относительный объём", "стоп-сценарий",
        "параметры сценария", "карта исполнения", "протокол исполнения",
    )
    HUMAN_MARKERS = (
        "я ", "мне ", "для меня", "не спеш", "не догон", "жду", "вижу",
        "не нравится", "настораж", "пропущ", "покупать", "продавать",
        "толпа", "ловушка", "ошибка", "важнее", "сейчас",
    )
    HOOK_MARKERS = (
        "почему", "но", "не ", "одна вещь", "ошибка", "ловушка", "опасн",
        "не нравится", "настораж", "не спеш", "не догон", "если", "вот",
        "главный риск", "все смотрят", "толпа", "что делать",
    )

    def report(self, text: str) -> FeedAppealReport:
        first = next((x.strip() for x in text.splitlines() if x.strip()), "")
        lowered = text.lower().replace("ё", "е")
        words = re.findall(r"[a-zа-я0-9$%./+-]+", lowered)
        word_count = max(1, len(words))
        numbers = re.findall(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?%?", text)
        paragraphs = [x.strip() for x in text.split("\n\n") if x.strip()]

        hook = 45.0
        if "$" in first:
            hook += 8
        if any(m in first.lower().replace("ё", "е") for m in self.HOOK_MARKERS):
            hook += 28
        if 42 <= len(first) <= 105:
            hook += 15
        if first.startswith("$"):
            hook -= 10
        if re.match(r"^\$[A-Z0-9]+:\s*[+-]?\d", first):
            hook -= 25
        hook = max(0.0, min(100.0, hook))

        human = 35.0
        human += min(50.0, sum(1 for m in self.HUMAN_MARKERS if m in lowered) * 9.0)
        if re.search(r"\b(?:я|мне|мой|моя|для меня)\b", lowered):
            human += 15
        human = min(100.0, human)

        clarity = 100.0
        numeric_ratio = len(numbers) / word_count
        if numeric_ratio > 0.11:
            clarity -= min(45.0, (numeric_ratio - 0.11) * 320)
        tech_hits = sum(lowered.count(term) for term in self.TECHNICAL_TERMS)
        clarity -= min(35.0, max(0, tech_hits - 3) * 6.0)
        if len(paragraphs) > 9:
            clarity -= (len(paragraphs) - 9) * 4
        if len(text) > 690:
            clarity -= 10
        clarity = max(20.0, clarity)

        conversation = 55.0
        q = text.count("?")
        if q == 1:
            conversation += 25
        elif q > 1:
            conversation -= 15
        if any(x in lowered for x in ("а вы", "как думаете", "кто смотрит", "вы бы", "что выберете", "у кого")):
            conversation += 20
        conversation = max(20.0, min(100.0, conversation))

        anti_template = 100.0
        labels = sum(lowered.count(x) for x in (
            "направление:", "вход:", "цели:", "стоп-лосс:", "r/r:",
            "ключевой уровень:", "рабочие уровни", "параметры сценария",
            "что отслеживаю:", "отмена:",
        ))
        if labels > 5:
            anti_template -= min(50.0, (labels - 5) * 10.0)
        if lowered.count("usdt") > 5:
            anti_template -= 12
        if len(numbers) >= 12:
            anti_template -= 16
        anti_template = max(25.0, anti_template)

        components = {
            "hook": hook,
            "human_voice": human,
            "clarity": clarity,
            "conversation": conversation,
            "anti_template": anti_template,
        }
        score = (
            hook * 0.30 + human * 0.24 + clarity * 0.22 +
            conversation * 0.14 + anti_template * 0.10
        )
        return FeedAppealReport(round(max(0.0, min(100.0, score)), 2), components)
