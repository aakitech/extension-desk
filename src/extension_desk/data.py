"""Read-only access to the committed datasets. Every record carries a
`record_id` that tools return and advisories cite, and a `source`.

Record ID prefixes: `rain:`, `price:`, `hh:`, `plot:`, `pest:`.
Date filters accept either `YYYY-MM` or `YYYY-MM-DD` everywhere."""

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from extension_desk.districts import DISTRICTS

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

FILES = {
    "rainfall": "rainfall.csv",
    "prices": "prices.csv",
    "households": "households.csv",
    "plots": "plots.csv",
    "pest_alerts": "pest_alerts.csv",
}

_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


class UnknownRecord(KeyError):
    pass


class UnknownDistrict(ValueError):
    pass


def _read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _check_date(value: str) -> str:
    """Validate a filter bound up front, so a bad input raises instead of
    looking like missing data."""
    if not value:
        return value
    try:
        if not _DATE.match(value):
            raise ValueError
        date.fromisoformat(value if len(value) == 10 else value + "-01")
    except ValueError:
        raise ValueError(f"expected a real YYYY-MM or YYYY-MM-DD date, got {value!r}") from None
    return value


def _on_or_after(value: str, since: str) -> bool:
    """`value` (a month or a date) is on or after `since` (a month or a date)."""
    return value[:len(since)] >= since[:len(value)]


def _on_or_before(value: str, end: str) -> bool:
    return value[:len(end)] <= end[:len(value)]


@dataclass
class DataStore:
    tables: dict[str, list[dict]]
    _by_id: dict[str, dict] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {}
        for name, rows in self.tables.items():
            for row in rows:
                rid = row["record_id"]
                if rid in self._by_id:
                    raise ValueError(f"duplicate record_id {rid!r} in {name}")
                self._by_id[rid] = row

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "DataStore":
        return cls({name: _read(data_dir / file) for name, file in FILES.items()})

    def get(self, record_id: str) -> dict:
        try:
            return dict(self._by_id[record_id])
        except KeyError:
            raise UnknownRecord(record_id) from None

    def exists(self, record_id: str) -> bool:
        return record_id in self._by_id

    @staticmethod
    def _district(district: str) -> str:
        key = district.strip().lower()
        if key not in DISTRICTS:
            raise UnknownDistrict(f"{district!r}; known districts: {', '.join(DISTRICTS)}")
        return key

    def _select(self, table: str, keep) -> list[dict]:
        return [dict(r) for r in self.tables[table] if keep(r)]

    def rainfall(self, district: str, start: str = "", end: str = "") -> list[dict]:
        """Dekads starting within [start, end]; either bound may be a month."""
        d = self._district(district)
        start, end = _check_date(start), _check_date(end)
        if start and end and start[:len(end)] > end[:len(start)]:
            raise ValueError(f"start {start!r} is after end {end!r}")
        return self._select("rainfall", lambda r: r["district"] == d
                            and _on_or_after(r["dekad_start"], start)
                            and (not end or _on_or_before(r["dekad_start"], end)))

    def prices(self, district: str, commodity: str | None = None, since: str = "") -> list[dict]:
        d = self._district(district)
        since = _check_date(since)
        return self._select("prices", lambda r: r["district"] == d
                            and (commodity is None or r["commodity"].lower() == commodity.lower())
                            and _on_or_after(r["month"], since))

    def households(self, district: str) -> list[dict]:
        d = self._district(district)
        return self._select("households", lambda r: r["district"] == d)

    def household(self, household_id: str) -> dict:
        if not household_id.startswith("hh:"):
            raise UnknownRecord(household_id)
        return self.get(household_id)

    def plots(self, household_id: str) -> list[dict]:
        self.household(household_id)
        return self._select("plots", lambda r: r["household_id"] == household_id)

    def pest_alerts(self, district: str, since: str = "") -> list[dict]:
        d = self._district(district)
        since = _check_date(since)
        return self._select("pest_alerts", lambda r: r["district"] == d
                            and _on_or_after(r["reported_on"], since))
