"""Editorial and factual quality gates for automated Binance Square posts."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Dict, List, Optional, Tuple

from content_strategy import question_forbidden, question_required
from engagement import FeedAppealEvaluator


@dataclass
class QualityReport:
    score: float
    valid: bool
    reasons: Tuple[str, ...]
    components: Dict[str, float]


FULL_PLAN_FORMATS = {
    "setup_plan", "risk_memo", "two_scenarios", "execution_protocol",
    "signal_vs_trade", "follow_up",
}


class PostQualityEvaluator:
    MIN_LENGTH = int(os.getenv("POST_MIN_CHARS", "180"))
    MAX_LENGTH = int(os.getenv("POST_MAX_CHARS", "760"))

    UNSUPPORTED_CLAIMS = (
        "90% точности", "100% точности", "гарантирован", "без риска",
        "точно вырастет", "точно упадет", "точно пойдёт", "точно пойдет",
        "киты покупают", "киты продают", "инсайд", "листинг скоро",
        "легкая прибыль", "лёгкая прибыль", "вероятность успеха",
        "памп неизбежен", "безусловный сигнал",
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
            "editorial_structure": self._structure(text, content_format),
            "human_voice": self._human_voice(text),
            "engagement": self._engagement(text, content_format),
            "credibility": self._credibility(text),
            "spam_control": self._spam_control(text),
            "feed_appeal": feed_appeal.score,
        }
        # Factual validity remains a hard gate, but ranking now favours posts
        # people may actually stop and read in a feed.
        weights = {
            "factual_contract": 0.15,
            "headline": 0.16,
            "readability": 0.10,
            "editorial_structure": 0.07,
            "human_voice": 0.12,
            "engagement": 0.08,
            "credibility": 0.07,
            "spam_control": 0.03,
            "feed_appeal": 0.22,
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

        if content_format in FULL_PLAN_FORMATS:
            for name, variants in (
                ("entry", ("вход:", "entry:")),
                ("targets", ("цели:", "tp1")),
                ("stop", ("стоп-лосс:", "stop-loss:")),
                ("risk_reward", ("r/r:", "risk/reward:")),
            ):
                if not any(item in lowered for item in variants):
                    reasons.append(f"missing {name}")
        else:
            if not any(item in lowered for item in ("ключевой уровень:", "уровень решения:", "что отслеживаю:")):
                reasons.append("missing key level")
            if not any(item in lowered for item in ("отмена:", "граница ошибки:", "стоп-сценарий:")):
                reasons.append("missing compact invalidation")

        invalidation_markers = (
            "отмена:", "отменяет идею", "точка ошибки", "без усреднения",
            "стоп не обсуждается", "сценарий закрывается", "позиция закрыта",
            "граница ошибки", "где признаю ошибку",
        )
        if not any(marker in lowered for marker in invalidation_markers):
            reasons.append("missing invalidation rule")

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
            required_level_keys = ("entry", "tp1", "tp2", "tp3", "stop") if content_format in FULL_PLAN_FORMATS else ("tp1", "stop")
            for key in required_level_keys:
                if key in levels and _fmt_price(levels[key]) not in text:
                    reasons.append(f"missing {key}")
            if content_format in FULL_PLAN_FORMATS and f"{levels.get('risk_reward', 0):.2f}" not in text:
                reasons.append("missing risk reward")

        q_count = text.count("?")
        if content_format and question_required(content_format) and q_count != 1:
            reasons.append("audience question required")
        if content_format and question_forbidden(content_format) and q_count:
            reasons.append("audience question forbidden")
        if q_count > 1:
            reasons.append("too many questions")

        if not first_line:
            reasons.append("missing headline")
        else:
            if headline and first_line != headline.strip():
                reasons.append("headline mismatch")
            if len(first_line) < 35:
                reasons.append("headline too short")
            if len(first_line) > 115:
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

        hashtags = re.findall(r"#[A-Za-zА-Яа-я0-9_]+", text)
        if len(hashtags) > 2:
            reasons.append("too many hashtags")
        if len(re.findall(r"[\U0001F300-\U0001FAFF]", text)) > 2:
            reasons.append("too many emojis")

        return not reasons, reasons

    @staticmethod
    def _contract_score(valid: bool, reasons: List[str]) -> float:
        if valid:
            return 100.0
        severe = sum(reason.startswith("missing") for reason in reasons)
        return max(20.0, 88.0 - severe * 11.0 - max(0, len(reasons) - severe) * 5.0)

    def _headline_score(self, text: str, explicit_headline: str) -> float:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if not first:
            return 0.0
        score = 100.0
        if len(first) < 35 or len(first) > 115:
            score -= 25.0
        if any(re.search(pattern, first, flags=re.IGNORECASE) for pattern in self.GENERIC_HEADLINES):
            score -= 55.0
        if "$" not in first:
            score -= 18.0
        if not any(word in first.lower() for word in (
            "почему", "уров", "риск", "сценар", "вход", "движ", "реш", "ошиб",
            "жду", "график", "цель", "не нравится", "осторож", "ловуш", "толпа",
            "не спеш", "одна вещь", "важнее",
        )):
            score -= 18.0
        if explicit_headline and first != explicit_headline.strip():
            score -= 25.0
        if re.search(r"[+-]?\d+(?:[.,]\d+)?%\s+за\s+15", first.lower()) or re.search(r"объ[её]м\s+x\d", first.lower()):
            score += 12.0
        return min(max(score, 0.0), 100.0)

    @staticmethod
    def _readability(text: str) -> float:
        paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        score = 100.0
        if len(paragraphs) < 5:
            score -= 18.0
        if len(paragraphs) > 11:
            score -= 8.0
        score -= min(25.0, sum(len(line) > 190 for line in lines) * 7.0)
        sentences = [item.strip().lower() for item in re.split(r"[.!?]+", text) if len(item.strip()) > 20]
        score -= min(20.0, (len(sentences) - len(set(sentences))) * 8.0)
        return max(score, 35.0)

    @staticmethod
    def _structure(text: str, content_format: str) -> float:
        lowered = text.lower().replace("ё", "е")
        score = 35.0
        score += 20.0 if len([part for part in text.split("\n\n") if part.strip()]) >= 4 else 0.0
        if content_format in FULL_PLAN_FORMATS:
            score += 20.0 if all(marker in lowered for marker in ("вход:", "цели:", "стоп-лосс:", "r/r:")) else 0.0
        else:
            score += 20.0 if any(marker in lowered for marker in ("ключевой уровень:", "уровень решения:", "что отслеживаю:")) else 0.0
        score += 15.0 if any(marker in lowered for marker in ("триггер", "сценарий a", "что я жду", "практический вывод", "ключевая граница", "сейчас:")) else 0.0
        score += 10.0 if content_format and content_format != "setup_plan" else 4.0
        return min(score, 100.0)

    @staticmethod
    def _human_voice(text: str) -> float:
        lowered = text.lower()
        score = 35.0
        score += 35.0 if re.search(r"\b(?:я|мне|для меня|моя|мой)\b", lowered) else 0.0
        score += 18.0 if any(marker in lowered for marker in ("не спеш", "не догон", "пропуска", "жду", "считаю", "вижу")) else 0.0
        score += 12.0 if any(marker in lowered for marker in ("ошибка", "неправ", "контраргумент", "отмена")) else 0.0
        return min(score, 100.0)

    @staticmethod
    def _engagement(text: str, content_format: str) -> float:
        q_count = text.count("?")
        if question_required(content_format):
            return 100.0 if q_count == 1 else 25.0
        if question_forbidden(content_format):
            return 92.0 if q_count == 0 else 35.0
        return 96.0 if q_count == 1 else 82.0 if q_count == 0 else 40.0

    def _credibility(self, text: str) -> float:
        lowered = text.lower().replace("ё", "е")
        score = 100.0
        score -= sum(35.0 for claim in self.UNSUPPORTED_CLAIMS if claim in lowered)
        if not any(marker in lowered for marker in (
            "отмена:", "отменяет идею", "без усреднения", "точка ошибки",
            "сценарий закрывается", "позиция закрыта", "граница ошибки",
        )):
            score -= 22.0
        if "гарант" in lowered or "точно" in lowered:
            score -= 25.0
        return max(score, 0.0)

    @staticmethod
    def _spam_control(text: str) -> float:
        hashtags = len(re.findall(r"#\w+", text))
        emojis = len(re.findall(r"[\U0001F300-\U0001FAFF]", text))
        words = re.findall(r"[A-Za-zА-Яа-я]+", text)
        uppercase = sum(word.isupper() and len(word) > 3 for word in words)
        score = 100.0
        score -= max(0, hashtags - 2) * 20.0
        score -= max(0, emojis - 2) * 15.0
        score -= max(0, uppercase - 8) * 3.0
        return max(score, 35.0)
