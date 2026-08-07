"""Editorial and factual quality gates for automated Binance Square posts."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Dict, List, Optional, Tuple

from content_strategy import question_forbidden
from engagement import FeedAppealEvaluator


@dataclass
class QualityReport:
    score: float
    valid: bool
    reasons: Tuple[str, ...]
    components: Dict[str, float]


# Compatibility constant. Human Feed v6 uses compact posts for every format.
FULL_PLAN_FORMATS: set[str] = set()


class PostQualityEvaluator:
    MIN_LENGTH = int(os.getenv("POST_MIN_CHARS", "160"))
    MAX_LENGTH = int(os.getenv("POST_MAX_CHARS", "620"))

    UNSUPPORTED_CLAIMS = (
        "90% точности", "100% точности", "гарантирован", "без риска",
        "точно вырастет", "точно упадет", "точно пойдёт", "точно пойдет",
        "киты покупают", "киты продают", "инсайд", "листинг скоро",
        "легкая прибыль", "лёгкая прибыль", "вероятность успеха",
        "памп неизбежен", "безусловный сигнал",
    )
    ROBOTIC_PHRASES = (
        "направление у идеи", "граница ошибки:", "диапазон контроля",
        "стоп является технической границей", "параметры сценария",
        "карта исполнения", "правило исполнения:", "факты для выбора:",
        "что вижу сейчас:",
    )
    GENERIC_HEADLINES = (
        r"^\$[A-Z0-9]+\s*[—-]\s*(?:LONG|SHORT)\s*:",
        r"^\$[A-Z0-9]+\s*[—-]\s*(?:ЛОНГ|ШОРТ)\s*:",
        r"^СИГНАЛ\s*[|:]",
    )

    def evaluate(self, text: str) -> float:
        return self.report(text).score

    def report(
        self,
        text: str,
        *,
        basic: Optional[str] = None,
        direction: Optional[str] = None,
        levels: Optional[Dict[str, float]] = None,
        content_format: str = "",
        headline: str = "",
    ) -> QualityReport:
        valid, reasons = self.validate(
            text,
            basic=basic,
            direction=direction,
            levels=levels,
            content_format=content_format,
            headline=headline,
        )
        feed_appeal = FeedAppealEvaluator().report(text)
        components = {
            "factual_contract": self._contract_score(valid, reasons),
            "headline": self._headline_score(text, headline),
            "readability": self._readability(text),
            "structure": self._structure(text),
            "human_voice": self._human_voice(text),
            "credibility": self._credibility(text),
            "spam_control": self._spam_control(text),
            "feed_appeal": feed_appeal.score,
        }
        weights = {
            "factual_contract": 0.15,
            "headline": 0.18,
            "readability": 0.10,
            "structure": 0.08,
            "human_voice": 0.14,
            "credibility": 0.08,
            "spam_control": 0.04,
            "feed_appeal": 0.23,
        }
        score = sum(components[name] * weights[name] for name in components)
        return QualityReport(
            score=min(max(score, 0.0), 100.0),
            valid=valid,
            reasons=tuple(reasons),
            components=components,
        )

    def validate(
        self,
        text: str,
        *,
        basic: Optional[str] = None,
        direction: Optional[str] = None,
        levels: Optional[Dict[str, float]] = None,
        content_format: str = "",
        headline: str = "",
    ) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        lowered = text.lower().replace("ё", "е")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        first_line = lines[0] if lines else ""

        if basic:
            ticker_pattern = rf"(?<![A-Za-z0-9_])\${re.escape(basic.upper())}(?![A-Za-z0-9_])"
            ticker_count = len(re.findall(ticker_pattern, text.upper()))
            if not 1 <= ticker_count <= 3:
                reasons.append(f"ticker mentions {ticker_count}")

        if direction:
            variants = ("LONG", "ЛОНГ") if direction.lower() == "long" else ("SHORT", "ШОРТ")
            if not any(item in text.upper() for item in variants):
                reasons.append("missing direction")

        if levels:
            from writer import _fmt_price
            for key in ("tp1", "stop"):
                if key in levels and _fmt_price(levels[key]) not in text:
                    reasons.append(f"missing {key}")

        if not any(marker in lowered for marker in (
            "сценарий отмен",
            "сценарий для меня отмен",
            "идею просто закрываю",
            "больше не актуален",
            "для меня закрыт",
            "ломает идею",
            "не открываю",
            "прохожу мимо",
        )):
            reasons.append("missing invalidation rule")

        q_count = text.count("?")
        if content_format and question_forbidden(content_format) and q_count:
            reasons.append("audience question forbidden")
        if q_count > 1:
            reasons.append("too many questions")

        if not first_line:
            reasons.append("missing headline")
        else:
            if headline and first_line != headline.strip():
                reasons.append("headline mismatch")
            if len(first_line) < 28:
                reasons.append("headline too short")
            if len(first_line) > 125:
                reasons.append("headline too long")
            if any(re.search(pattern, first_line, flags=re.IGNORECASE) for pattern in self.GENERIC_HEADLINES):
                reasons.append("generic signal headline")

        if len(text) < self.MIN_LENGTH:
            reasons.append(f"post too short {len(text)}")
        if len(text) > self.MAX_LENGTH:
            reasons.append(f"post too long {len(text)}")

        for claim in self.UNSUPPORTED_CLAIMS:
            if claim in lowered:
                reasons.append(f"unsupported claim: {claim}")
        if any(phrase in lowered for phrase in self.ROBOTIC_PHRASES):
            reasons.append("robotic wording")

        hashtags = re.findall(r"#[A-Za-zА-Яа-я0-9_]+", text)
        if len(hashtags) > 1:
            reasons.append("too many hashtags")
        if len(re.findall(r"[\U0001F300-\U0001FAFF]", text)) > 1:
            reasons.append("too many emojis")
        return not reasons, reasons

    @staticmethod
    def _contract_score(valid: bool, reasons: List[str]) -> float:
        if valid:
            return 100.0
        severe = sum(reason.startswith("missing") or "robotic" in reason for reason in reasons)
        return max(15.0, 88.0 - severe * 13.0 - max(0, len(reasons) - severe) * 5.0)

    def _headline_score(self, text: str, explicit_headline: str) -> float:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if not first:
            return 0.0
        lowered = first.lower().replace("ё", "е")
        score = 92.0
        if 38 <= len(first) <= 118:
            score += 8.0
        else:
            score -= 18.0
        if "$" not in first:
            score -= 22.0
        if any(re.search(pattern, first, flags=re.IGNORECASE) for pattern in self.GENERIC_HEADLINES):
            score -= 60.0
        if any(word in lowered for word in (
            "не ", "но", "после", "уже", "важнее", "спор", "ошиб", "ловуш",
            "жду", "ретест", "главный риск", "не догон", "одна причина",
        )):
            score += 8.0
        if explicit_headline and first != explicit_headline.strip():
            score -= 30.0
        return min(max(score, 0.0), 100.0)

    @staticmethod
    def _readability(text: str) -> float:
        paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
        score = 100.0
        if len(paragraphs) < 3:
            score -= 25.0
        if len(paragraphs) > 7:
            score -= min(35.0, (len(paragraphs) - 7) * 7.0)
        if len(text) > 560:
            score -= min(25.0, (len(text) - 560) / 7.0)
        if len(text) < 220:
            score -= min(15.0, (220 - len(text)) / 5.0)
        long_lines = sum(len(line) > 180 for line in text.splitlines())
        score -= min(20.0, long_lines * 7.0)
        return max(score, 25.0)

    @staticmethod
    def _structure(text: str) -> float:
        lowered = text.lower().replace("ё", "е")
        score = 40.0
        score += 22.0 if "если " in lowered else 0.0
        score += 18.0 if any(x in lowered for x in ("первая цель", "к ", "зона интереса")) else 0.0
        score += 20.0 if any(x in lowered for x in (
            "отменяется", "закрываю", "не актуален", "для меня закрыт", "ломает идею"
        )) else 0.0
        return min(score, 100.0)

    @staticmethod
    def _human_voice(text: str) -> float:
        lowered = text.lower().replace("ё", "е")
        score = 35.0
        score += 35.0 if re.search(r"\b(?:я|мне|мой|моя|для меня)\b", lowered) else 0.0
        score += 20.0 if any(marker in lowered for marker in (
            "не догон", "не хочу", "жду", "смотрю", "пропущ", "ретест", "прохожу",
        )) else 0.0
        score += 10.0 if any(marker in lowered for marker in ("ошибка", "ловушка", "поздн", "спеш")) else 0.0
        return min(score, 100.0)

    def _credibility(self, text: str) -> float:
        lowered = text.lower().replace("ё", "е")
        score = 100.0
        score -= sum(35.0 for claim in self.UNSUPPORTED_CLAIMS if claim in lowered)
        if not any(marker in lowered for marker in (
            "отменяется", "идею просто закрываю", "не актуален", "для меня закрыт", "ломает идею", "не открываю",
        )):
            score -= 25.0
        if "гарант" in lowered or "точно" in lowered:
            score -= 25.0
        return max(score, 0.0)

    @staticmethod
    def _spam_control(text: str) -> float:
        hashtags = len(re.findall(r"#\w+", text))
        emojis = len(re.findall(r"[\U0001F300-\U0001FAFF]", text))
        exclamations = text.count("!")
        score = 100.0
        score -= max(0, hashtags - 1) * 25.0
        score -= max(0, emojis - 1) * 20.0
        score -= max(0, exclamations - 1) * 12.0
        return max(score, 30.0)
