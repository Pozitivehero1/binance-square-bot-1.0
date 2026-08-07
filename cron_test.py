"""Offline tests for cron-safe state, pacing, reach gate and process lock."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from publication_guard import PublicationGuard
from runtime import ProcessLock, resolve_state_file


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_directory:
        state_dir = Path(temp_directory) / "persistent-state"
        with patch.dict(
            os.environ,
            {
                "STATE_DIR": str(state_dir),
                "ENABLE_PACING_LIMITS": "1",
                "ENABLE_REACH_GATE": "1",
                "MIN_GLOBAL_INTERVAL_MIN": "120",
                "MAX_POSTS_PER_DAY": "2",
                "MIN_REACH_SCORE": "76",
                "BOT_TIMEZONE": "UTC",
                "FORCE_PUBLISH": "0",
            },
            clear=False,
        ):
            memory_path = resolve_state_file("POST_MEMORY_FILE", "post_memory.json")
            assert memory_path.parent == state_dir

            guard_path = state_dir / "publication_state.json"
            guard = PublicationGuard(path=guard_path)
            now = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
            assert guard.preflight(now).allowed

            guard.state = {
                "publications": [
                    {
                        "ts": (now - timedelta(minutes=30)).isoformat(),
                        "symbol": "BTCUSDT",
                    }
                ]
            }
            guard_path.parent.mkdir(parents=True, exist_ok=True)
            guard_path.write_text(json.dumps(guard.state), encoding="utf-8")
            blocked = guard.preflight(now)
            assert not blocked.allowed and blocked.next_allowed_at is not None

            strong = guard.evaluate_candidate(
                market_score=86,
                quality_score=94,
                volume_relative=2.1,
                change_1h=2.2,
            )
            weak = guard.evaluate_candidate(
                market_score=55,
                quality_score=82,
                volume_relative=0.5,
                change_1h=0.1,
            )
            assert strong.allowed and not weak.allowed

            lock_path = state_dir / "test.lock"
            first = ProcessLock(lock_path, stale_minutes=10)
            second = ProcessLock(lock_path, stale_minutes=10)
            assert first.acquire()
            assert not second.acquire()
            first.release()
            assert second.acquire()
            second.release()

    print("CRON SAFETY: OK | stable state paths | pacing | reach gate | overlap lock")


if __name__ == "__main__":
    main()
