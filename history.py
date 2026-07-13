import os
import json
from datetime import datetime, timedelta
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

HISTORY_FILE = Path("published_history.json")

def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return {}
    return {}

def save_history(history):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving history: {e}")

def get_recently_published(minutes=180):
    history = load_history()
    cutoff = datetime.now() - timedelta(minutes=minutes)
    return [s for s, ts in history.items() if datetime.fromisoformat(ts) > cutoff]

def add_published(symbol):
    history = load_history()
    history[symbol] = datetime.now().isoformat()
    save_history(history)

def cleanup_history(days=7):
    history = load_history()
    cutoff = datetime.now() - timedelta(days=days)
    to_delete = [s for s, ts in history.items() if datetime.fromisoformat(ts) < cutoff]
    for s in to_delete:
        del history[s]
    if to_delete:
        save_history(history)