"""Persistent cooldown history for successfully published symbols."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from runtime import migrate_legacy_state, resolve_state_file

logger = logging.getLogger(__name__)
HISTORY_FILE = resolve_state_file("PUBLISHED_HISTORY_FILE", "published_history.json")
_migrated = migrate_legacy_state(HISTORY_FILE, "published_history.json")
if _migrated:
    logger.info("Migrated publication history from %s to %s", _migrated, HISTORY_FILE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_history() -> Dict[str, str]:
    if not HISTORY_FILE.exists():
        return {}
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.error("History load failed: %s", exc)
        return {}


def save_history(history: Dict[str, str]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(HISTORY_FILE.parent),
            prefix=f".{HISTORY_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(history, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, HISTORY_FILE)
    except Exception as exc:
        logger.error("History save failed: %s", exc)
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def get_recently_published(minutes: int = 180) -> List[str]:
    cutoff = _now() - timedelta(minutes=max(0, int(minutes)))
    recent = []
    for symbol, timestamp in load_history().items():
        try:
            if _parse(timestamp) > cutoff:
                recent.append(symbol)
        except (TypeError, ValueError):
            continue
    return recent


def add_published(symbol: str) -> None:
    history = load_history()
    history[symbol.upper()] = _now().isoformat()
    save_history(history)


def cleanup_history(days: int = 14) -> None:
    history = load_history()
    cutoff = _now() - timedelta(days=max(1, int(days)))
    cleaned = {}
    for symbol, timestamp in history.items():
        try:
            if _parse(timestamp) >= cutoff:
                cleaned[symbol] = timestamp
        except (TypeError, ValueError):
            continue
    if cleaned != history:
        save_history(cleaned)
