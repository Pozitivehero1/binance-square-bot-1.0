"""Post memory — de-duplicates recent post text/topics."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

MEMORY_FILE = Path("post_memory.json")


class PostMemory:
    """Keeps last N posts to avoid repeating the same phrasing."""

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
        self.items.append({
            "ts": datetime.now().isoformat(),
            "symbol": symbol,
            "text": text,
        })
        self._save()

    def recent_texts(self, n: int = 10) -> List[str]:
        return [x["text"] for x in self.items[-n:]]

    def recent_symbols(self, n: int = 20) -> List[str]:
        return [x["symbol"] for x in self.items[-n:]]
