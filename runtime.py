"""Runtime helpers for cron-safe paths, logging, locks and status files."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import socket
import tempfile
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent


def load_project_env(*, override: bool = False) -> Path:
    """Load .env from the project directory, independent of cron working directory."""
    env_path = PROJECT_DIR / ".env"
    load_dotenv(dotenv_path=env_path, override=override)
    return env_path


def _project_relative(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_DIR / path


def state_dir() -> Path:
    raw = (os.getenv("STATE_DIR") or "state").strip()
    return _project_relative(raw)


def resolve_state_file(env_name: str, default_name: str) -> Path:
    """Resolve state paths consistently.

    A bare filename is stored under STATE_DIR. Relative paths containing a folder
    are resolved from the project root. Absolute paths are kept unchanged.
    """
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return state_dir() / default_name
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    if path.parent == Path("."):
        return state_dir() / path.name
    return PROJECT_DIR / path


def resolve_project_file(env_name: str, default_value: str) -> Path:
    raw = (os.getenv(env_name) or default_value).strip()
    return _project_relative(raw)


def migrate_legacy_state(target: Path, legacy_name: str) -> Optional[Path]:
    """Copy an old relative state file into the new stable state directory once."""
    if target.exists():
        return None
    candidates = [PROJECT_DIR / legacy_name, Path.cwd() / legacy_name]
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen or resolved == target.resolve():
            continue
        seen.add(resolved)
        if not resolved.is_file():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, target)
            return resolved
        except OSError:
            return None
    return None


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def setup_logging() -> logging.Logger:
    """Configure stdout + rotating persistent log exactly once."""
    root = logging.getLogger()
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")

    if not any(getattr(handler, "_binance_console", False) for handler in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console._binance_console = True  # type: ignore[attr-defined]
        root.addHandler(console)

    log_path = resolve_project_file("LOG_FILE", "logs/bot.log")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not any(getattr(handler, "_binance_file", None) == str(log_path) for handler in root.handlers):
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=max(100_000, int(os.getenv("LOG_MAX_BYTES", "3000000"))),
                backupCount=max(1, int(os.getenv("LOG_BACKUPS", "5"))),
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler._binance_file = str(log_path)  # type: ignore[attr-defined]
            root.addHandler(file_handler)
    except OSError:
        root.exception("Cannot initialize persistent log file: %s", log_path)
    return logging.getLogger("bot")


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ProcessLock:
    """Cross-platform atomic lock for overlapping third-party cron launches."""

    def __init__(self, path: Optional[Path] = None, stale_minutes: Optional[int] = None):
        self.path = path or resolve_state_file("RUN_LOCK_FILE", "bot.lock")
        self.stale_minutes = max(5, stale_minutes or int(os.getenv("LOCK_STALE_MIN", "45")))
        self.token = f"{os.getpid()}-{datetime.now(timezone.utc).timestamp()}"
        self.acquired = False

    def _is_stale(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            timestamp = datetime.fromisoformat(str(data.get("started_at", "")))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
            pid = int(data.get("pid", 0))
            same_host = str(data.get("host", "")) == socket.gethostname()
            if same_host and _pid_is_running(pid):
                return False
            return age_seconds > self.stale_minutes * 60
        except Exception:
            try:
                age_seconds = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
                return age_seconds > self.stale_minutes * 60
            except OSError:
                return True

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "token": self.token,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "project_dir": str(PROJECT_DIR),
            "cwd": str(Path.cwd()),
        }
        for _ in range(2):
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.acquired = True
                return True
            except FileExistsError:
                if not self._is_stale():
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except Exception:
            pass
        self.acquired = False

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def write_status(status: str, detail: str = "", **extra: object) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "detail": detail,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        **extra,
    }
    path = resolve_state_file("BOT_STATUS_FILE", "status.json")
    try:
        atomic_write_json(path, payload)
    except OSError:
        logging.getLogger(__name__).exception("Cannot write status file: %s", path)
