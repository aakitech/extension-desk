"""Fetch WFP retail food prices for Zimbabwe (HDX, CC BY-IGO) and keep the
staple-crop series for the pilot districts' markets. Writes data/prices.csv.

    uv run python scripts/fetch_prices.py
"""

import csv
import io
import re
import urllib.request
from pathlib import Path

from extension_desk.districts import DISTRICTS

URL = (
    "https://data.humdata.org/dataset/d878cfac-a45f-48bd-8460-a2e16ab0ea94/"
    "resource/7c48c192-36d4-422e-996d-19cc62e2e4fd/download/wfp_food_prices_zwe.csv"
)
COMMODITIES = {"Maize", "Maize meal", "Beans (sugar)", "Sorghum", "Groundnuts (shelled)"}
KEEP_FROM = "2020-01"
OUT = Path(__file__).resolve().parents[1] / "data" / "prices.csv"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main() -> None:
    with urllib.request.urlopen(URL, timeout=120) as resp:
        reader = csv.DictReader(io.TextIOWrapper(resp, encoding="utf-8"))
        source_rows = list(reader)

    market_district = {
        market: key for key, d in DISTRICTS.items() for market in d["markets"]
    }
    rows = []
    for r in source_rows:
        month = r["date"][:7]
        if (
            r["market"] in market_district
            and r["commodity"] in COMMODITIES
            and r["currency"] == "USD"
            and r["pricetype"] == "Retail"
            and month >= KEEP_FROM
        ):
            rows.append({
                "record_id": f"price:{slug(r['commodity'])}:{slug(r['market'])}:{month}",
                "district": market_district[r["market"]],
                "market": r["market"],
                "commodity": r["commodity"],
                "unit": r["unit"],
                "month": month,
                "price_usd": r["price"],
                "price_flag": r["priceflag"],
                "source": "WFP via HDX (wfp_food_prices_zwe.csv), CC BY-IGO",
            })

    rows.sort(key=lambda r: r["record_id"])
    ids = [r["record_id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate price record IDs"

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} prices to {OUT}")


if __name__ == "__main__":
    main()
