"""Eval fixtures: a copy of data/ with one documented change, so a task can
pose a situation the committed data doesn't (a drought, missing records, a
hostile data field). The change is applied at run time and nothing extra is
committed.

Changed rows keep their original `source`, because tool results show it to
the model, and a label like "drought" or "injected" would hand it the answer.
Instead `make` writes `fixture.json` beside the data, naming the change and
every record it touched; the harness copies that into each task's transcript.
"""

import csv
import json
import shutil
from pathlib import Path
from typing import Callable

from extension_desk.data import DATA_DIR


def _rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def make(dest: Path, change: Callable[[Path], list[str]] | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for f in DATA_DIR.glob("*.csv"):
        shutil.copy(f, dest / f.name)
    touched = change(dest) if change else []
    (dest / "fixture.json").write_text(json.dumps({
        "change": getattr(change, "describe", None), "records": touched}, indent=2), encoding="utf-8")
    return dest


def describe(text: str):
    def wrap(fn):
        fn.describe = text
        return fn
    return wrap


def drought(district: str, months: tuple[str, ...]) -> Callable[[Path], None]:
    """Zero rain in the given months for a district, departures recomputed."""
    @describe(f"drought: zero rain in {district} for {', '.join(months)}")
    def change(d: Path) -> list[str]:
        fields, rows = _rows(d / "rainfall.csv")
        touched = []
        for r in rows:
            if r["district"] == district and r["dekad_start"][:7] in months:
                normal = float(r["normal_mm"])
                r.update(rain_mm="0.0", departure_mm=f"{-normal:.1f}",
                         anomaly_pct="-100" if normal >= 5 else "")
                touched.append(r["record_id"])
        _write(d / "rainfall.csv", fields, rows)
        return touched
    return change


def remove(district: str, tables: tuple[str, ...]) -> Callable[[Path], None]:
    """Drop every row for a district from the given tables (rainfall, prices, pest_alerts)."""
    @describe(f"removed: every {district} row in {', '.join(tables)}")
    def change(d: Path) -> list[str]:
        touched = []
        for table in tables:
            fields, rows = _rows(d / f"{table}.csv")
            touched += [r["record_id"] for r in rows if r["district"] == district]
            _write(d / f"{table}.csv", fields, [r for r in rows if r["district"] != district])
        return touched
    return change


def inject(record_id: str, field: str, text: str) -> Callable[[Path], None]:
    """Put hostile text into one data field."""
    @describe(f"injected: text in {record_id} field {field}")
    def change(d: Path) -> list[str]:
        table = {"pest": "pest_alerts", "rain": "rainfall", "price": "prices"}[record_id.split(":")[0]]
        fields, rows = _rows(d / f"{table}.csv")
        hit = [r for r in rows if r["record_id"] == record_id]
        assert hit, record_id
        hit[0][field] = text
        _write(d / f"{table}.csv", fields, rows)
        return [record_id]
    return change
