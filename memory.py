"""Post memory — de-duplicates recent post text/topics with style/CTA tracking."""
from __future__ import annotations
import json
import logging
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

MEMORY_FILE = Path("post_memory.json")


class PostMemory:
    """Keeps last N posts, extracts title, CTA, style for diversity checks."""

    def __init__(self, path: Path = MEMORY_FILE, keep_days: int = 14, max_items: int = 40):
        self.path = path
        self.keep_days = keep_days
        self.max_items = max_items
        self.items: List[dict] = self._load()

    def _load(self) -> List[dict]:
        if not self.path.exists():
            return []
        try:
            with open(self.path) as f:
                data = json.load(f)
            cutoff = datetime.now() - timedelta(days=self.keep_days)
            return [x for x in data if datetime.fromisoformat(x["ts"]) > cutoff]
        except Exception as e:
            logger.warning(f"PostMemory load: {e}")
            return []

    def _save(self) -> None:
        try:
            with open(self.path, "w") as f:
                json.dump(self.items[-self.max_items:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"PostMemory save: {e}")

    def add_post(self, symbol: str, text: str) -> None:
        """Store post with extracted title, CTA, style."""
        lines = text.strip().split('\n')
        title = lines[0] if lines else ""
        # CTA: look for line containing ? or ! among last 3 lines
        ctas = [l for l in lines[-3:] if '?' in l or '!' in l]
        cta = ctas[0] if ctas else ""
        # Style: hash of first 100 chars
        style = hashlib.md5(text[:100].encode()).hexdigest()[:8]

        self.items.append({
            "ts": datetime.now().isoformat(),
            "symbol": symbol,
            "text": text,
            "title": title,
            "cta": cta,
            "style": style,
        })
        self._save()

    def recent_texts(self, n: int = 10) -> List[str]:
        return [x["text"] for x in self.items[-n:]]

    def recent_symbols(self, n: int = 20) -> List[str]:
        return [x["symbol"] for x in self.items[-n:]]

    def get_last_titles(self, n: int = 10) -> List[str]:
        return [x.get("title", "") for x in self.items[-n:]]

    def get_last_ctas(self, n: int = 10) -> List[str]:
        return [x.get("cta", "") for x in self.items[-n:]]

    def get_last_styles(self, n: int = 10) -> List[str]:
        return [x.get("style", "") for x in self.items[-n:]]

    def is_similar(self, text: str, threshold: float = 0.6) -> bool:
        """Check if text is similar to any recent post (first 50 chars)."""
        sample = text[:50].lower()
        for item in self.items[-10:]:
            if item["text"][:50].lower() == sample:
                return True
        return False