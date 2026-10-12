"""Run transcripts: everything needed to show what a run actually did.

    state/runs/<UTC timestamp>/run.json      model, exact build, command, date, commit
    state/runs/<UTC timestamp>/<hh>.json     messages and tool calls, gate pauses and
                                             answers, outcome, timing

A recorded run (for EVALS.md or the open-weights requirement) is one of these
directories copied under evals/runs/.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import messages_to_dict

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "state" / "runs"


def default_dir() -> Path:
    return Path(os.environ.get("EXTENSION_DESK_RUNS", DEFAULT_DIR))


def _commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10,
                             cwd=Path(__file__).resolve().parent)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                               text=True, timeout=10, cwd=Path(__file__).resolve().parent)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def command() -> list[str]:
    """argv with the program reduced to its name: no local paths in committed records."""
    return [Path(sys.argv[0]).name, *sys.argv[1:]]


class Transcript:
    def __init__(self, root: Path | str | None = None, name: str | None = None, **meta) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.dir = Path(root or default_dir()) / (name or stamp)
        self.dir.mkdir(parents=True, exist_ok=False)
        self.meta = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "command": command(), "commit": _commit(), **meta, "households": {}}
        self._write("run.json", self.meta)

    def _write(self, name: str, data) -> None:
        (self.dir / name).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
                                     encoding="utf-8")

    def household(self, household_id: str, *, state: dict, gate: list[dict], seconds: float) -> None:
        outcome = state.get("outcome", {})
        self._write(f"{household_id.replace(':', '_')}.json", {
            "household_id": household_id,
            "today": state.get("today"),
            "outcome": outcome,
            "seconds": round(seconds, 1),
            "turns": state.get("turns", 0),
            "failed_drafts": state.get("attempts", 0),
            "seen": state.get("seen", []),
            "gate": gate,
            "messages": messages_to_dict(state.get("messages", [])),
        })
        self.meta["households"][household_id] = {"status": outcome.get("status"), "seconds": round(seconds, 1)}
        self.meta["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._write("run.json", self.meta)
