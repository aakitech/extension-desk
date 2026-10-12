"""The officer review queue: where acting tools leave drafts, flags and
proposed visits. Nothing in it has been sent or done.

Items enter as `pending_approval`. Only `decide()` changes that, once, and
only the officer approval gate (`extension_desk.agent.gate`) calls it, with
an audit entry written in the same transaction. The MCP server never exposes
it, so no tool can approve its own output.

Backed by SQLite so the queue survives between tool calls and processes.
"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

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
    created_at TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    final_payload TEXT
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

    def decide(self, item_id: str, status: str, officer: str, final_payload: dict,
               log: Callable[[dict], None]) -> dict:
        """Move a pending item to `approved` or `rejected`, once.

        `log` is called with the decided item inside the transaction, before
        commit. If it raises, nothing changes: there is never a decision
        without its audit entry."""
        if status not in ("approved", "rejected"):
            raise ValueError(f"status must be approved or rejected, got {status!r}")
        if not officer or not officer.strip():
            raise ValueError("a decision needs the officer's name")
        kind, n = _split(item_id)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as conn:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")  # no second decision can interleave
            try:
                cur = conn.execute(
                    "UPDATE items SET status = ?, decided_by = ?, decided_at = ?, final_payload = ?"
                    " WHERE id = ? AND kind = ? AND status = ?",
                    (status, officer.strip(), now, json.dumps(final_payload), n, kind, PENDING))
                if cur.rowcount != 1:
                    exists = conn.execute("SELECT status FROM items WHERE id = ? AND kind = ?",
                                          (n, kind)).fetchone()
                    raise ValueError(f"{item_id} is not pending" if exists else f"no queue item {item_id}")
                item = _item(conn.execute("SELECT * FROM items WHERE id = ?", (n,)).fetchone())
                log(item)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return item

    def get(self, item_id: str) -> dict:
        kind, n = _split(item_id)
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ? AND kind = ?", (n, kind)).fetchone()
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


def _split(item_id: str) -> tuple[str, int]:
    kind, _, n = item_id.partition(":")
    return kind, int(n) if n.isdigit() else -1


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
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "final_payload": json.loads(row["final_payload"]) if row["final_payload"] else None,
    }
