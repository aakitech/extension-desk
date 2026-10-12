"""Eval fixtures: a copy of data/ with one documented change, so a task can
pose a situation the committed data doesn't (a drought, missing records, a
hostile data field). The change is applied at run time; nothing extra is
committed, and every changed row keeps a source that says it was changed.
"""

import csv
import shutil
from pathlib import Path
from typing import Callable

from extension_desk.data import DATA_DIR

EVAL_SOURCE = "eval fixture (see src/extension_desk/evals/fixtures.py)"


def _rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def make(dest: Path, change: Callable[[Path], None] | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for f in DATA_DIR.glob("*.csv"):
        shutil.copy(f, dest / f.name)
    if change:
        change(dest)
    return dest


def drought(district: str, months: tuple[str, ...]) -> Callable[[Path], None]:
    """Zero rain in the given months for a district, departures recomputed."""
    def change(d: Path) -> None:
        fields, rows = _rows(d / "rainfall.csv")
        for r in rows:
            if r["district"] == district and r["dekad_start"][:7] in months:
                normal = float(r["normal_mm"])
                r.update(rain_mm="0.0", departure_mm=f"{-normal:.1f}",
                         anomaly_pct="-100" if normal >= 5 else "", source=EVAL_SOURCE + ": drought")
        _write(d / "rainfall.csv", fields, rows)
    return change


def remove(district: str, tables: tuple[str, ...]) -> Callable[[Path], None]:
    """Drop every row for a district from the given tables (rainfall, prices, pest_alerts)."""
    def change(d: Path) -> None:
        for table in tables:
            fields, rows = _rows(d / f"{table}.csv")
            _write(d / f"{table}.csv", fields, [r for r in rows if r["district"] != district])
    return change


def inject(record_id: str, field: str, text: str) -> Callable[[Path], None]:
    """Put hostile text into one data field."""
    def change(d: Path) -> None:
        table = {"pest": "pest_alerts", "rain": "rainfall", "price": "prices"}[record_id.split(":")[0]]
        fields, rows = _rows(d / f"{table}.csv")
        hit = [r for r in rows if r["record_id"] == record_id]
        assert hit, record_id
        hit[0][field] = text
        hit[0]["source"] = EVAL_SOURCE + ": injected text"
        _write(d / f"{table}.csv", fields, rows)
    return change
