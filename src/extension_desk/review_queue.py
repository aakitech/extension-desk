"""The officer review queue: where acting tools leave drafts, flags and
proposed visits. Nothing in it has been sent or done.

Items enter as `pending_approval`. This module offers no way to change that
status; the officer approval gate (#3) owns that, with its audit log, so no
MCP tool can approve its own output.

Backed by SQLite so the queue survives between tool calls and processes.
"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "state" / "review_queue.sqlite3"
PENDING = "pending_approval"
# Also the queue item ID prefix (`advisory:7`), so an ID can be typed by it.
KINDS = ("advisory", "flag", "visit")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ({", ".join(repr(k) for k in KINDS)})),
    household_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    citations TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '{PENDING}'
        CHECK (status IN ('{PENDING}', 'approved', 'rejected')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


def default_path() -> Path:
    return Path(os.environ.get("EXTENSION_DESK_QUEUE", DEFAULT_PATH))


class ReviewQueue:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, kind: str, household_id: str, payload: dict,
            citations: list[str], created_by: str) -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown queue item kind {kind!r}")
        if not citations:
            raise ValueError("a queue item needs at least one citation")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO items (kind, household_id, payload, citations, created_by, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (kind, household_id, json.dumps(payload), json.dumps(citations), created_by, now),
            )
            row = conn.execute("SELECT * FROM items WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _item(row)

    def get(self, item_id: str) -> dict:
        kind, _, n = item_id.partition(":")
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ? AND kind = ?",
                               (n if n.isdigit() else -1, kind)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return _item(row)

    def items(self, household_id: str | None = None, status: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM items WHERE 1 = 1", []
        if household_id is not None:
            query, args = query + " AND household_id = ?", [*args, household_id]
        if status is not None:
            query, args = query + " AND status = ?", [*args, status]
        with closing(self._connect()) as conn:
            return [_item(r) for r in conn.execute(query + " ORDER BY id", args)]


def _item(row: sqlite3.Row) -> dict:
    return {
        "item_id": f"{row['kind']}:{row['id']}",
        "kind": row["kind"],
        "household_id": row["household_id"],
        "status": row["status"],
        "payload": json.loads(row["payload"]),
        "citations": json.loads(row["citations"]),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }
