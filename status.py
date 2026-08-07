"""Print the current cron/runtime status without exposing API keys."""
from __future__ import annotations

import json
from pathlib import Path

from runtime import load_project_env, resolve_project_file, resolve_state_file

load_project_env()


def _show(label: str, path: Path) -> None:
    print(f"{label}: {path}")
    if not path.exists():
        print("  not created yet")
        return
    try:
        if path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-25:]:
                print(line)
    except Exception as exc:
        print(f"  read failed: {exc}")


def main() -> None:
    _show("STATUS", resolve_state_file("BOT_STATUS_FILE", "status.json"))
    _show("PUBLICATION STATE", resolve_state_file("PUBLICATION_STATE_FILE", "publication_state.json"))
    _show("LOG TAIL", resolve_project_file("LOG_FILE", "logs/bot.log"))


if __name__ == "__main__":
    main()
