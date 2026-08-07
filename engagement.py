"""Feed-appeal scoring for Binance Square posts.

The score intentionally rewards a human first line, compactness and one clear
trade idea. It penalises label-heavy terminal dumps and forced engagement bait.
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
        "граница ошибки", "диапазон контроля",
    )
    HUMAN_MARKERS = (
        "я ", "мне ", "для меня", "я бы", "не спеш", "не догон", "жду",
        "смотрю", "не хочу", "пропущ", "ретест", "ловушка", "ошибка",
        "важнее", "сейчас", "прохожу мимо",
    )
    HOOK_MARKERS = (
        "но", "не ", "одна причина", "одна вещь", "ошибка", "ловушка",
        "не хочу", "не догон", "если", "главный риск", "после", "уже",
        "важнее", "спорный", "слишком",
    )
    ROBOTIC_LABELS = (
        "направление:", "направление у идеи", "вход:", "цели:",
        "стоп-лосс:", "r/r:", "ключевой уровень:", "рабочие уровни",
        "параметры сценария", "что отслеживаю:", "граница ошибки:",
        "диапазон контроля", "правило исполнения:", "факты для выбора:",
    )

    def report(self, text: str) -> FeedAppealReport:
        first = next((x.strip() for x in text.splitlines() if x.strip()), "")
        lowered = text.lower().replace("ё", "е")
        words = re.findall(r"[a-zа-я0-9$%./+-]+", lowered)
        word_count = max(1, len(words))
        numbers = re.findall(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?%?", text)
        paragraphs = [x.strip() for x in text.split("\n\n") if x.strip()]

        hook = 42.0
        if "$" in first:
            hook += 12.0
        if any(marker in first.lower().replace("ё", "е") for marker in self.HOOK_MARKERS):
            hook += 28.0
        if 38 <= len(first) <= 118:
            hook += 14.0
        if re.match(r"^\$[A-Z0-9]+:\s*[+-]?\d+(?:[.,]\d+)?%", first):
            hook -= 20.0
        if first.lower().startswith("направление"):
            hook -= 30.0
        hook = max(0.0, min(100.0, hook))

        human = 35.0
        human += min(52.0, sum(1 for marker in self.HUMAN_MARKERS if marker in lowered) * 8.0)
        if re.search(r"\b(?:я|мне|мой|моя|для меня)\b", lowered):
            human += 14.0
        if any(marker in lowered for marker in ("направление у идеи", "граница ошибки", "диапазон контроля")):
            human -= 30.0
        human = max(0.0, min(100.0, human))

        clarity = 100.0
        numeric_ratio = len(numbers) / word_count
        if numeric_ratio > 0.085:
            clarity -= min(50.0, (numeric_ratio - 0.085) * 390.0)
        tech_hits = sum(lowered.count(term) for term in self.TECHNICAL_TERMS)
        clarity -= min(40.0, max(0, tech_hits - 2) * 8.0)
        if len(paragraphs) > 7:
            clarity -= (len(paragraphs) - 7) * 6.0
        if len(text) > 560:
            clarity -= min(25.0, (len(text) - 560) / 8.0)
        clarity = max(15.0, clarity)

        # A question is fine, but no question is also perfectly natural. We do
        # not reward every post for ending with engagement bait.
        conversation = 88.0
        q_count = text.count("?")
        if q_count == 1:
            conversation = 94.0
            if any(x in lowered for x in ("вы бы", "кто тоже", "для вас", "ждёте", "уже в позиции")):
                conversation = 100.0
        elif q_count > 1:
            conversation = 45.0

        anti_template = 100.0
        labels = sum(lowered.count(x) for x in self.ROBOTIC_LABELS)
        anti_template -= min(70.0, labels * 14.0)
        if lowered.count("usdt") > 2:
            anti_template -= min(25.0, (lowered.count("usdt") - 2) * 8.0)
        if len(numbers) >= 10:
            anti_template -= 22.0
        if text.count(":") >= 7:
            anti_template -= 18.0
        anti_template = max(10.0, anti_template)

        length_fit = 100.0
        if len(text) < 220:
            length_fit -= min(35.0, (220 - len(text)) / 3.0)
        elif len(text) > 540:
            length_fit -= min(45.0, (len(text) - 540) / 4.0)
        if not 3 <= len(paragraphs) <= 7:
            length_fit -= 10.0
        length_fit = max(20.0, length_fit)

        components = {
            "hook": hook,
            "human_voice": human,
            "clarity": clarity,
            "conversation": conversation,
            "anti_template": anti_template,
            "length_fit": length_fit,
        }
        score = (
            hook * 0.28
            + human * 0.24
            + clarity * 0.20
            + conversation * 0.08
            + anti_template * 0.12
            + length_fit * 0.08
        )
        return FeedAppealReport(round(max(0.0, min(100.0, score)), 2), components)
