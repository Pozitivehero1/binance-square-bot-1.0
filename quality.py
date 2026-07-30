"""Post quality scoring and hard validation for automated publishing."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Tuple


@dataclass
class QualityReport:
    score: float
    valid: bool
    reasons: Tuple[str, ...]
    components: Dict[str, float]


class PostQualityEvaluator:
    MIN_LENGTH = 320
    MAX_LENGTH = 1800

    UNSUPPORTED_CLAIMS = (
        "90% точности",
        "100% точности",
        "гарантирован",
        "без риска",
        "точно вырастет",
        "точно упадет",
        "киты покупают",
        "киты продают",
        "крупные игроки начали",
        "я заработал",
        "легкая прибыль",
        "лёгкая прибыль",
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
    ) -> QualityReport:
        valid, reasons = self.validate(
            text,
            basic=basic,
            direction=direction,
            levels=levels,
        )
        components = {
            "completeness": self._completeness(valid, reasons),
            "readability": self._readability(text),
            "structure": self._structure(text),
            "engagement": self._engagement(text),
            "credibility": self._credibility(text),
            "spam_control": self._spam_control(text),
            "length": self._length_score(text),
        }
        weights = {
            "completeness": 0.28,
            "readability": 0.15,
            "structure": 0.18,
            "engagement": 0.12,
            "credibility": 0.15,
            "spam_control": 0.07,
            "length": 0.05,
        }
        score = sum(components[key] * weights[key] for key in components)
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
    ) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        lowered = text.lower()

        required_groups = (
            ("entry", ("вход", "entry")),
            ("targets", ("tp", "цель", "targets")),
            ("stop", ("стоп", "stop", "sl")),
        )
        for name, variants in required_groups:
            if not any(item in lowered for item in variants):
                reasons.append(f"missing {name}")

        risk_markers = (
            "не финансовая рекомендация",
            "не является финансовой рекомендацией",
            "отмена сценария",
            "размер позиции",
            "допустимого риска",
            "стоп-уровня",
        )
        if not any(marker in lowered for marker in risk_markers):
            reasons.append("missing risk note")

        if "?" not in text:
            reasons.append("missing audience question")

        if basic:
            ticker = "$" + basic.upper()
            if ticker not in text.upper():
                reasons.append("missing ticker")

        if direction:
            variants = (
                ("LONG", "ЛОНГ", "ПОКУПКА")
                if direction.lower() == "long"
                else ("SHORT", "ШОРТ", "ПРОДАЖА")
            )
            if not any(item in text.upper() for item in variants):
                reasons.append("missing direction")

        # AI must not be forced to repeat exact numbers in the prose.
        # The trade block is generated from validated bot data.

        if len(text) < self.MIN_LENGTH:
            reasons.append(f"post too short {len(text)}")
        if len(text) > self.MAX_LENGTH:
            reasons.append("post too long")

        for claim in self.UNSUPPORTED_CLAIMS:
            if claim in lowered:
                reasons.append(f"unsupported claim: {claim}")

        hashtags = re.findall(r"#[A-Za-zА-Яа-я0-9_]+", text)
        if len(hashtags) > 5:
            reasons.append("too many hashtags")

        return not reasons, reasons

    @staticmethod
    def _completeness(valid: bool, reasons: List[str]) -> float:
        if valid:
            return 100.0
        severe = sum(
            reason.startswith("missing entry")
            or reason.startswith("missing tp")
            or reason.startswith("missing stop")
            or reason.startswith("missing risk reward")
            for reason in reasons
        )
        return max(20.0, 82.0 - severe * 12.0 - max(0, len(reasons) - severe) * 6.0)

    @staticmethod
    def _readability(text: str) -> float:
        paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        score = 100.0
        if len(paragraphs) < 4:
            score -= 15.0
        long_lines = sum(len(line) > 220 for line in lines)
        score -= min(25.0, long_lines * 7.0)
        sentences = [part.strip().lower() for part in re.split(r"[.!?]+", text) if len(part.strip()) > 20]
        duplicate_count = len(sentences) - len(set(sentences))
        score -= min(20.0, duplicate_count * 8.0)
        return max(35.0, score)

    @staticmethod
    def _structure(text: str) -> float:
        lowered = text.lower()
        score = 0.0
        score += 20.0 if len([part for part in text.split("\n\n") if part.strip()]) >= 5 else 8.0
        score += 25.0 if all(marker in lowered for marker in ("вход", "tp1", "стоп", "r/r")) else 0.0
        score += 20.0 if "отмена сценария" in lowered else 0.0
        score += 15.0 if any(marker in lowered for marker in ("подтверждение", "подтвердит", "чек-лист", "аргументы")) else 0.0
        score += 10.0 if "контекст" in lowered else 0.0
        score += 10.0 if "$" in text and ("long" in lowered or "short" in lowered) else 0.0
        return min(score, 100.0)

    @staticmethod
    def _engagement(text: str) -> float:
        score = 0.0
        questions = text.count("?")
        score += 45.0 if questions == 1 else 32.0 if questions > 1 else 0.0
        score += 20.0 if re.search(r"\b(?:RSI|ADX|VWAP|EMA20|ATR)\b", text, re.IGNORECASE) else 0.0
        score += 20.0 if re.search(r"\b(?:вход|TP1|стоп)\b", text, re.IGNORECASE) else 0.0
        score += 15.0 if any(word in text.lower() for word in ("почему", "суть", "тезис", "сценарий", "реакция")) else 0.0
        return min(score, 100.0)

    def _credibility(self, text: str) -> float:
        lowered = text.lower()
        score = 100.0
        for claim in self.UNSUPPORTED_CLAIMS:
            if claim in lowered:
                score -= 35.0
        if not any(marker in lowered for marker in ("отмена сценария", "размер позиции", "допустимого риска")):
            score -= 20.0
        if "гарант" in lowered or "точно" in lowered:
            score -= 20.0
        return max(score, 0.0)

    @staticmethod
    def _spam_control(text: str) -> float:
        hashtags = len(re.findall(r"#\w+", text))
        emojis = len(re.findall(r"[\U0001F300-\U0001FAFF]", text))
        words = re.findall(r"[A-Za-zА-Яа-я]+", text)
        uppercase = sum(word.isupper() and len(word) > 3 for word in words)
        score = 100.0
        score -= max(0, hashtags - 4) * 15.0
        score -= max(0, emojis - 3) * 10.0
        score -= max(0, uppercase - 7) * 3.0
        return max(score, 35.0)

    def _length_score(self, text: str) -> float:
        size = len(text)
        if 500 <= size <= 1300:
            return 100.0
        if self.MIN_LENGTH <= size < 500 or 1300 < size <= 1600:
            return 82.0
        if 250 <= size <= self.MAX_LENGTH:
            return 60.0
        return 25.0
