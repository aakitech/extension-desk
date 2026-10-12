"""Read-only access to the committed datasets. Every record carries a
`record_id` that tools return and advisories cite, and a `source`."""

import csv
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

FILES = {
    "rainfall": "rainfall.csv",
    "prices": "prices.csv",
    "households": "households.csv",
    "plots": "plots.csv",
    "pest_alerts": "pest_alerts.csv",
}


class UnknownRecord(KeyError):
    pass


def _read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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
            return self._by_id[record_id]
        except KeyError:
            raise UnknownRecord(record_id) from None

    def exists(self, record_id: str) -> bool:
        return record_id in self._by_id

    def rainfall(self, district: str, start: str = "", end: str = "9999") -> list[dict]:
        """Dekads whose start date falls in [start, end] (ISO dates)."""
        return [r for r in self.tables["rainfall"]
                if r["district"] == district and start <= r["dekad_start"] <= end]

    def prices(self, district: str, commodity: str | None = None, since: str = "") -> list[dict]:
        return [r for r in self.tables["prices"]
                if r["district"] == district
                and (commodity is None or r["commodity"].lower() == commodity.lower())
                and r["month"] >= since]

    def households(self, district: str) -> list[dict]:
        return [r for r in self.tables["households"] if r["district"] == district]

    def household(self, household_id: str) -> dict:
        row = self.get(household_id)
        if row not in self.tables["households"]:
            raise UnknownRecord(household_id)
        return row

    def plots(self, household_id: str) -> list[dict]:
        return [r for r in self.tables["plots"] if r["household_id"] == household_id]

    def pest_alerts(self, district: str, since: str = "") -> list[dict]:
        return [r for r in self.tables["pest_alerts"]
                if r["district"] == district and r["reported_on"] >= since]
