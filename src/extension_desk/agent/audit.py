"""Append-only audit log (JSONL). One line per event: officer decisions at
the approval gate, and drafts the agent dropped or declined to make.

Each entry carries the inputs, the outputs, a UTC timestamp and, for
decisions, the approving officer. The file is only ever opened for append.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "state" / "audit.jsonl"


def default_path() -> Path:
    return Path(os.environ.get("EXTENSION_DESK_AUDIT", DEFAULT_PATH))


class AuditLog:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **fields) -> dict:
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "event": event, **fields}
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
