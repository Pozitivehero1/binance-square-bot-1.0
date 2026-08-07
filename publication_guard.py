"""Global publication pacing and reach-quality gate for cron-driven runs."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from runtime import atomic_write_json, migrate_legacy_state, resolve_state_file


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str
    next_allowed_at: Optional[datetime] = None
    score: Optional[float] = None


class PublicationGuard:
    def __init__(self, memory_items: Optional[Iterable[dict]] = None, path: Optional[Path] = None):
        self.path = path or resolve_state_file("PUBLICATION_STATE_FILE", "publication_state.json")
        migrate_legacy_state(self.path, "publication_state.json")
        self.memory_items = list(memory_items or [])
        self.enable_pacing_limits = os.getenv("ENABLE_PACING_LIMITS", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.enable_reach_gate = os.getenv("ENABLE_REACH_GATE", "1").strip().lower() in {"1", "true", "yes", "on"}
        self.min_interval_min = max(20, int(os.getenv("MIN_GLOBAL_INTERVAL_MIN", "20")))
        self.max_posts_day = max(1, int(os.getenv("MAX_POSTS_PER_DAY", "72")))
        self.min_reach_score = max(0.0, min(100.0, float(os.getenv("MIN_REACH_SCORE", "64"))))
        self.force_publish = os.getenv("FORCE_PUBLISH", "0").strip().lower() in {"1", "true", "yes", "on"}
        timezone_name = (os.getenv("BOT_TIMEZONE") or "UTC").strip()
        try:
            self.local_tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            self.local_tz = timezone.utc
        self.windows = self._parse_windows(os.getenv("PUBLISH_WINDOWS", ""))
        self.state = self._load()

    @staticmethod
    def _parse_timestamp(value: object) -> Optional[datetime]:
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_windows(raw: str) -> list[tuple[int, int]]:
        windows: list[tuple[int, int]] = []
        for part in str(raw).split(","):
            item = part.strip()
            if not item:
                continue
            try:
                start_raw, end_raw = item.split("-", 1)
                sh, sm = (int(value) for value in start_raw.split(":"))
                eh, em = (int(value) for value in end_raw.split(":"))
                start = sh * 60 + sm
                end = eh * 60 + em
                if 0 <= start < 1440 and 0 <= end < 1440 and start != end:
                    windows.append((start, end))
            except (ValueError, TypeError):
                continue
        return windows

    def _load(self) -> dict:
        if not self.path.exists():
            return {"publications": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {"publications": []}
            publications = payload.get("publications", [])
            payload["publications"] = publications if isinstance(publications, list) else []
            return payload
        except Exception:
            return {"publications": []}

    def _all_publication_times(self) -> list[datetime]:
        result: list[datetime] = []
        for item in self.state.get("publications", []):
            if isinstance(item, dict):
                timestamp = self._parse_timestamp(item.get("ts"))
                if timestamp:
                    result.append(timestamp)
        for item in self.memory_items:
            if isinstance(item, dict):
                timestamp = self._parse_timestamp(item.get("ts"))
                if timestamp:
                    result.append(timestamp)
        return sorted(set(result))

    def _inside_window(self, local_now: datetime) -> bool:
        if not self.windows:
            return True
        minute = local_now.hour * 60 + local_now.minute
        for start, end in self.windows:
            if start < end and start <= minute < end:
                return True
            if start > end and (minute >= start or minute < end):
                return True
        return False

    def preflight(self, now: Optional[datetime] = None) -> GuardDecision:
        if self.force_publish:
            return GuardDecision(True, "FORCE_PUBLISH enabled")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        local_now = current.astimezone(self.local_tz)
        if not self._inside_window(local_now):
            return GuardDecision(False, "outside configured publish windows")
        if not self.enable_pacing_limits:
            return GuardDecision(True, "pacing limits disabled; cron cadence controls frequency")

        times = self._all_publication_times()
        if times:
            next_allowed = times[-1] + timedelta(minutes=self.min_interval_min)
            if current < next_allowed:
                return GuardDecision(False, "global publication interval not elapsed", next_allowed)

        today = local_now.date()
        count_today = sum(timestamp.astimezone(self.local_tz).date() == today for timestamp in times)
        if count_today >= self.max_posts_day:
            tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=self.local_tz)
            return GuardDecision(False, f"daily publication budget reached ({count_today}/{self.max_posts_day})", tomorrow.astimezone(timezone.utc))
        return GuardDecision(True, f"publication budget available ({count_today}/{self.max_posts_day})")

    @staticmethod
    def calculate_reach_score(*, market_score: float, quality_score: float, volume_relative: float, change_1h: float) -> float:
        volume = max(0.05, float(volume_relative))
        attention = 48.0
        attention += min(32.0, max(0.0, math.log2(volume)) * 20.0)
        attention += min(20.0, abs(float(change_1h)) * 6.0)
        attention = max(0.0, min(100.0, attention))
        score = float(market_score) * 0.50 + float(quality_score) * 0.35 + attention * 0.15
        return max(0.0, min(100.0, score))

    def evaluate_candidate(self, *, market_score: float, quality_score: float, volume_relative: float, change_1h: float) -> GuardDecision:
        score = self.calculate_reach_score(
            market_score=market_score,
            quality_score=quality_score,
            volume_relative=volume_relative,
            change_1h=change_1h,
        )
        if self.force_publish or not self.enable_reach_gate:
            return GuardDecision(True, f"reach score {score:.1f} recorded for ranking; hard gate disabled", score=score)
        if score >= self.min_reach_score:
            return GuardDecision(True, f"reach score {score:.1f} passed threshold {self.min_reach_score:.1f}", score=score)
        return GuardDecision(False, f"reach score {score:.1f} below threshold {self.min_reach_score:.1f}", score=score)

    def record_success(self, *, symbol: str, direction: str, content_format: str, visual_style: str, market_score: float, quality_score: float, reach_score: float, post_id: str = "") -> None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=45)
        publications = []
        for item in self.state.get("publications", []):
            if not isinstance(item, dict):
                continue
            timestamp = self._parse_timestamp(item.get("ts"))
            if timestamp and timestamp >= cutoff:
                publications.append(item)
        publications.append(
            {
                "ts": now.isoformat(),
                "symbol": symbol.upper(),
                "direction": direction,
                "content_format": content_format,
                "visual_style": visual_style,
                "market_score": round(float(market_score), 2),
                "quality_score": round(float(quality_score), 2),
                "reach_score": round(float(reach_score), 2),
                "post_id": str(post_id or ""),
            }
        )
        self.state = {"publications": publications[-300:]}
        atomic_write_json(self.path, self.state)
