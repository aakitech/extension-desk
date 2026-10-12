"""Generate the synthetic households, plots and pest alerts. No real people or
farms: every value comes from a seeded random generator, so the output is
reproducible. Writes data/households.csv, data/plots.csv, data/pest_alerts.csv.

    uv run python scripts/generate_synthetic.py
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

from extension_desk.districts import DISTRICTS

SEED = 20261012
HOUSEHOLDS_PER_DISTRICT = 25
AS_OF = date(2026, 10, 12)
SOURCE = "synthetic (scripts/generate_synthetic.py)"
OUT = Path(__file__).resolve().parents[1] / "data"

CROPS = [("maize", 0.55), ("sorghum", 0.2), ("groundnuts", 0.15), ("cowpeas", 0.1)]
MATURITY = {"maize": ["short", "medium"], "sorghum": ["short", "medium"],
            "groundnuts": ["short"], "cowpeas": ["short"]}
PESTS = [
    ("fall armyworm", ["maize", "sorghum"]),
    ("red-billed quelea", ["sorghum"]),
    ("maize stalk borer", ["maize"]),
    ("aphids", ["groundnuts", "cowpeas"]),
]


def write(name: str, rows: list[dict]) -> None:
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to data/{name}")


def main() -> None:
    rng = random.Random(SEED)
    households, plots, alerts = [], [], []
    n = 0
    for key in DISTRICTS:
        for _ in range(HOUSEHOLDS_PER_DISTRICT):
            n += 1
            hh = f"hh-{n:03d}"
            households.append({
                "record_id": hh,
                "district": key,
                "ward": rng.randint(1, 20),
                "members": rng.randint(2, 9),
                "has_phone": rng.random() < 0.8,
                "last_officer_visit": (AS_OF - timedelta(days=rng.randint(14, 400))).isoformat(),
                "source": SOURCE,
            })
            for p in range(1, rng.randint(1, 3) + 1):
                crop = rng.choices([c for c, _ in CROPS], [w for _, w in CROPS])[0]
                irrigated = rng.random() < 0.12
                # The 2026/27 rainy season hasn't started; only irrigated
                # plots are already in the ground.
                planted = (AS_OF - timedelta(days=rng.randint(10, 45))).isoformat() if irrigated else ""
                plots.append({
                    "record_id": f"plot:{hh}-p{p}",
                    "household_id": hh,
                    "crop": crop,
                    "maturity": rng.choice(MATURITY[crop]),
                    "area_ha": round(rng.uniform(0.2, 2.5), 1),
                    "soil": rng.choice(["sandy", "sandy loam", "clay loam"]),
                    "irrigated": irrigated,
                    "planted_on": planted,
                    "source": SOURCE,
                })

        for i in range(1, 4):
            pest, crops = rng.choice(PESTS)
            alerts.append({
                "record_id": f"pest:{key}:{i:03d}",
                "district": key,
                "reported_on": (AS_OF - timedelta(days=rng.randint(1, 30))).isoformat(),
                "pest": pest,
                "crops_affected": ";".join(crops),
                "severity": rng.choice(["low", "medium", "high"]),
                "source": SOURCE,
            })

    write("households.csv", households)
    write("plots.csv", plots)
    write("pest_alerts.csv", alerts)


if __name__ == "__main__":
    main()
