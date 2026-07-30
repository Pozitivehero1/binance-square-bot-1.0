"""Persistent post memory with wording and structural anti-duplication checks."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, List, Sequence, Set, Tuple

logger = logging.getLogger(__name__)
MEMORY_FILE = Path(os.getenv("POST_MEMORY_FILE", "post_memory.json"))

# Mandatory trading vocabulary must not dominate the similarity score.
_BOILERPLATE_TOKENS = {
    "вход", "entry", "tp", "tp1", "tp2", "tp3", "цель", "цели", "стоп", "stop",
    "rr", "risk", "reward", "usdt", "long", "short", "лонг", "шорт", "сигнал",
    "сценарий", "план", "уровень", "уровни", "цена", "сделка", "позиция",
}


class PostMemory:
    def __init__(self, path: Path = MEMORY_FILE, keep_days: int = 30, max_items: int = 160):
        self.path = Path(path)
        self.keep_days = max(1, int(keep_days))
        self.max_items = max(30, int(max_items))
        self.items: List[dict] = self._load()
        self._feature_cache = [self._features(str(item.get("text", ""))) for item in self.items]

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def normalize_text(text: str) -> str:
        normalized = text.lower().replace("ё", "е")
        normalized = re.sub(r"\$[a-z0-9]+", "$ticker", normalized)
        normalized = re.sub(r"\b\d+(?:[.,]\d+)?\b", "#", normalized)
        normalized = re.sub(r"#[a-zа-я0-9_]+", "", normalized)
        normalized = re.sub(r"[^a-zа-я$#\s]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    @classmethod
    def semantic_tokens(cls, text: str) -> List[str]:
        normalized = cls.normalize_text(text)
        tokens = []
        for token in normalized.split():
            compact = token.replace("#", "").replace("$", "")
            if not compact or token in {"#", "$ticker"}:
                continue
            if compact in _BOILERPLATE_TOKENS:
                continue
            if len(compact) <= 1:
                continue
            tokens.append(compact)
        return tokens

    @staticmethod
    def _ngrams(tokens: Sequence[str], size: int = 3) -> Set[Tuple[str, ...]]:
        if len(tokens) < size:
            return {tuple(tokens)} if tokens else set()
        return {tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}

    @classmethod
    def paragraph_openers(cls, text: str) -> Set[Tuple[str, ...]]:
        result: Set[Tuple[str, ...]] = set()
        for paragraph in re.split(r"\n\s*\n", text):
            tokens = cls.semantic_tokens(paragraph)
            if tokens:
                result.add(tuple(tokens[: min(5, len(tokens))]))
        return result

    @staticmethod
    def structure_signature(text: str) -> str:
        signatures = []
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        for part in paragraphs:
            lower = part.lower()
            line_count = len([line for line in part.splitlines() if line.strip()])
            if re.search(r"\b(?:tp1|tp2|tp3|вход|entry|стоп|stop|r/r)\b", lower):
                kind = "L"  # levels
            elif "?" in part:
                kind = "Q"
            elif any(word in lower for word in ("отмена сценария", "риск", "красная линия", "граница ошибки")):
                kind = "R"
            elif any(word in lower for word in ("btc", "старш", "контекст", "фон рынка")):
                kind = "C"
            elif re.search(r"(?:^|\n)(?:□|✓|\+|−|\d+[.)]|шаг|ворота)", lower):
                kind = "S"  # structured list
            elif len(part) < 90:
                kind = "A"
            elif len(part) < 190:
                kind = "B"
            else:
                kind = "D"
            signatures.append(f"{kind}{min(line_count, 9)}")
        return "-".join(signatures)

    @staticmethod
    def _jaccard(left: Set, right: Set) -> float:
        union = left | right
        return len(left & right) / len(union) if union else 0.0

    @classmethod
    def _features(cls, text: str) -> dict:
        tokens = cls.semantic_tokens(text)
        return {
            "semantic": " ".join(tokens),
            "token_set": set(tokens),
            "trigrams": cls._ngrams(tokens),
            "openers": cls.paragraph_openers(text),
            "structure": cls.structure_signature(text),
        }

    @classmethod
    def _compare_features(cls, left: dict, right: dict) -> float:
        if not left["semantic"] or not right["semantic"]:
            return 0.0
        token_jaccard = cls._jaccard(left["token_set"], right["token_set"])
        trigram_jaccard = cls._jaccard(left["trigrams"], right["trigrams"])
        opener_jaccard = cls._jaccard(left["openers"], right["openers"])
        structure_ratio = SequenceMatcher(None, left["structure"], right["structure"]).ratio()
        score = (
            token_jaccard * 0.25
            + trigram_jaccard * 0.45
            + opener_jaccard * 0.15
            + structure_ratio * 0.15
        )
        return min(max(score, 0.0), 1.0)

    @classmethod
    def compare_texts(cls, left: str, right: str) -> float:
        """Return 0..1 similarity while discounting mandatory level boilerplate."""
        return cls._compare_features(cls._features(left), cls._features(right))

    def _load(self) -> List[dict]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, list):
                return []
            cutoff = self._now() - timedelta(days=self.keep_days)
            valid = []
            for item in data:
                if not isinstance(item, dict) or "ts" not in item:
                    continue
                try:
                    if self._parse_timestamp(item["ts"]) >= cutoff:
                        valid.append(item)
                except (TypeError, ValueError):
                    continue
            return valid[-self.max_items :]
        except Exception as exc:
            logger.warning("PostMemory load failed: %s", exc)
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(self.items[-self.max_items :], handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except Exception as exc:
            logger.error("PostMemory save failed: %s", exc)
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def add_post(
        self,
        symbol: str,
        text: str,
        *,
        post_style: str = "",
        signal_type: str = "",
    ) -> None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        title = lines[0] if lines else ""
        content_lines = [line for line in lines if not line.startswith("#")]
        cta = ""
        for line in reversed(content_lines[-7:]):
            if "?" in line:
                cta = line
                break

        self.items.append(
            {
                "ts": self._now().isoformat(),
                "symbol": symbol.upper(),
                "text": text,
                "title": title,
                "title_signature": self.normalize_text(title),
                "cta": cta,
                "text_signature": self.normalize_text(text),
                "semantic_signature": " ".join(self.semantic_tokens(text)),
                "structure_signature": self.structure_signature(text),
                "post_style": str(post_style or ""),
                "signal_type": str(signal_type or ""),
            }
        )
        self.items = self.items[-self.max_items :]
        self._feature_cache.append(self._features(text))
        self._feature_cache = self._feature_cache[-self.max_items :]
        self._save()

    def recent_texts(self, n: int = 10) -> List[str]:
        return [str(item.get("text", "")) for item in self.items[-n:]]

    def recent_symbols(self, n: int = 20) -> List[str]:
        return [str(item.get("symbol", "")) for item in self.items[-n:]]

    def get_last_titles(self, n: int = 10) -> List[str]:
        return [str(item.get("title", "")) for item in self.items[-n:]]

    def get_last_ctas(self, n: int = 10) -> List[str]:
        return [str(item.get("cta", "")) for item in self.items[-n:] if item.get("cta")]

    def get_last_post_styles(self, n: int = 10) -> List[str]:
        return [str(item.get("post_style", "")) for item in self.items[-n:] if item.get("post_style")]

    def get_last_signal_types(self, n: int = 10) -> List[str]:
        return [str(item.get("signal_type", "")) for item in self.items[-n:] if item.get("signal_type")]

    def signal_type_frequency(self, n: int = 20) -> dict:
        counts = {}
        for signal_type in self.get_last_signal_types(n):
            counts[signal_type] = counts.get(signal_type, 0) + 1
        return counts

    def get_last_styles(self, n: int = 10) -> List[str]:
        return [str(item.get("structure_signature", "")) for item in self.items[-n:]]

    def was_title_used(self, title: str, threshold: float = 0.82) -> bool:
        candidate = self.normalize_text(title)
        if not candidate:
            return False
        for item in self.items[-60:]:
            existing = item.get("title_signature") or self.normalize_text(item.get("title", ""))
            if SequenceMatcher(None, candidate, existing).ratio() >= threshold:
                return True
        return False

    def similarity_score(self, text: str, n: int = 60) -> float:
        candidate = self._features(text)
        best = 0.0
        for existing in self._feature_cache[-n:]:
            best = max(best, self._compare_features(candidate, existing))
        return best

    def most_similar(self, text: str, n: int = 60) -> Tuple[float, str]:
        candidate = self._features(text)
        best_score = 0.0
        best_index = -1
        start = max(0, len(self._feature_cache) - n)
        for index in range(start, len(self._feature_cache)):
            score = self._compare_features(candidate, self._feature_cache[index])
            if score > best_score:
                best_score, best_index = score, index
        best_text = str(self.items[best_index].get("text", "")) if best_index >= 0 else ""
        return best_score, best_text

    def is_similar(self, text: str, threshold: float = 0.56) -> bool:
        return self.similarity_score(text) >= threshold
