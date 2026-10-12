"""Fetch daily rainfall for each pilot district from the Open-Meteo archive
(ERA5 reanalysis), aggregate to dekads, and compare each dekad with its
1991-2020 normal. Writes data/rainfall.csv.

    uv run python scripts/fetch_rainfall.py
"""

import calendar
import csv
import json
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from extension_desk.districts import DISTRICTS

API = "https://archive-api.open-meteo.com/v1/archive"
CLIMATOLOGY = (1991, 2020)
KEEP_FROM = date(2023, 7, 1)  # two full rainy seasons plus the current one
# Below this, a percentage anomaly is noise (dry-season dekads), so it is left blank.
MIN_NORMAL_MM = 5.0
OUT = Path(__file__).resolve().parents[1] / "data" / "rainfall.csv"


def dekad_of(d: date) -> int:
    return 1 if d.day <= 10 else 2 if d.day <= 20 else 3


def dekad_bounds(year: int, month: int, dekad: int) -> tuple[date, date]:
    start = date(year, month, (dekad - 1) * 10 + 1)
    last = calendar.monthrange(year, month)[1] if dekad == 3 else dekad * 10
    return start, date(year, month, last)


def fetch_daily(lat: float, lon: float, end: date) -> dict[date, float]:
    query = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "start_date": f"{CLIMATOLOGY[0]}-01-01",
        "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "models": "era5",
        "timezone": "Africa/Harare",
    })
    with urllib.request.urlopen(f"{API}?{query}", timeout=120) as resp:
        daily = json.load(resp)["daily"]
    return {
        date.fromisoformat(t): p
        for t, p in zip(daily["time"], daily["precipitation_sum"])
        if p is not None
    }


def main() -> None:
    # ERA5 lags real time by about five days; stop well short of today.
    end = date.today() - timedelta(days=7)
    rows = []
    for key, district in DISTRICTS.items():
        daily = fetch_daily(district["lat"], district["lon"], end)
        totals: dict[tuple[int, int, int], float] = defaultdict(float)
        counts: dict[tuple[int, int, int], int] = defaultdict(int)
        for d, p in daily.items():
            k = (d.year, d.month, dekad_of(d))
            totals[k] += p
            counts[k] += 1

        def complete(y: int, m: int, dk: int) -> bool:
            start, stop = dekad_bounds(y, m, dk)
            return counts[(y, m, dk)] == (stop - start).days + 1

        # Only complete dekads enter the normal; a dekad missing a day would
        # drag its year's total down and skew the mean.
        normals: dict[tuple[int, int], list[float]] = defaultdict(list)
        for (y, m, dk), total in totals.items():
            if CLIMATOLOGY[0] <= y <= CLIMATOLOGY[1] and complete(y, m, dk):
                normals[(m, dk)].append(total)
        years = CLIMATOLOGY[1] - CLIMATOLOGY[0] + 1
        short = {k: len(v) for k, v in normals.items() if len(v) < years}
        if short:
            print(f"warning: {key} normals with fewer than {years} complete years: {short}")

        for (y, m, dk), total in sorted(totals.items()):
            start, stop = dekad_bounds(y, m, dk)
            if start < KEEP_FROM or not complete(y, m, dk):
                continue
            total = round(total, 1)
            normal = round(statistics.mean(normals[(m, dk)]), 1)
            anomaly = round((total - normal) / normal * 100) if normal >= MIN_NORMAL_MM else None
            rows.append({
                "record_id": f"rain:{key}:{y}-{m:02d}-d{dk}",
                "district": key,
                "dekad_start": start.isoformat(),
                "dekad_end": stop.isoformat(),
                "rain_mm": total,
                "normal_mm": normal,
                "departure_mm": round(total - normal, 1),
                "anomaly_pct": "" if anomaly is None else anomaly,
                "normal_years": len(normals[(m, dk)]),
                "source": "Open-Meteo archive (CC BY 4.0), ERA5 reanalysis; normal = 1991-2020 mean",
            })

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} dekads to {OUT}")


if __name__ == "__main__":
    main()
