"""Fetch staple-crop retail prices for the pilot districts' markets and write
data/prices.csv:

- WFP retail food prices for Zimbabwe (HDX, CC BY-IGO): maize and maize meal.
- FEWS NET Data Warehouse (public): sorghum, pearl millet, cowpeas, shelled
  groundnuts and white maize grain, which WFP no longer reports in USD.

    uv run python scripts/fetch_prices.py
"""

import csv
import io
import re
import urllib.request
from decimal import Decimal
from pathlib import Path

from extension_desk.districts import DISTRICTS

WFP_URL = (
    "https://data.humdata.org/dataset/d878cfac-a45f-48bd-8460-a2e16ab0ea94/"
    "resource/7c48c192-36d4-422e-996d-19cc62e2e4fd/download/wfp_food_prices_zwe.csv"
)
WFP_COMMODITIES = {"Maize", "Maize meal", "Beans (sugar)", "Sorghum", "Groundnuts (shelled)"}
FEWS_URL = "https://fdw.fews.net/api/marketpricefacts/?country=ZW&format=csv&start_date={start}-01"
# FEWS NET product -> (commodity name in our records, the one pack size kept).
# Each product is quoted in more than one pack size; keeping one gives one
# price per commodity, market and month.
FEWS_PRODUCTS = {
    "Sorghum": ("Sorghum", "17.5 kg"),
    "Millet (Pearl)": ("Pearl millet", "17.5 kg"),
    "Cowpeas (Mixed)": ("Cowpeas", "4.5_kg"),
    "Groundnuts (Shelled)": ("Groundnuts (shelled)", "3.75 kg"),
    "Maize Grain (White)": ("Maize grain (white)", "17.5 kg"),
}
KEEP_FROM = "2023-12"  # when the WFP USD series for these markets starts
OUT = Path(__file__).resolve().parents[1] / "data" / "prices.csv"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fetch_csv(url: str, encoding: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=300) as resp:
        return list(csv.DictReader(io.TextIOWrapper(resp, encoding=encoding)))


def wfp_rows() -> list[dict]:
    market_district = {
        market: key for key, d in DISTRICTS.items() for market in d["markets"]
    }
    rows = []
    for r in fetch_csv(WFP_URL, "utf-8"):
        month = r["date"][:7]
        if (
            r["market"] in market_district
            and r["commodity"] in WFP_COMMODITIES
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
    return rows


def fews_rows() -> list[dict]:
    markets = {
        name: (key, short)
        for key, d in DISTRICTS.items() for name, short in d["fews_markets"].items()
    }
    rows = []
    for r in fetch_csv(FEWS_URL.format(start=KEEP_FROM), "utf-8-sig"):
        product = FEWS_PRODUCTS.get(r["product"])
        if (
            r["market"] not in markets
            or product is None
            or r["unit"] != product[1]
            or r["price_type"] != "Retail"
            or not r["value"]
            or not r["common_currency_price"]
        ):
            continue
        if r["common_unit"] != "kg" or r["common_currency"] != "USD":
            raise ValueError(f"unexpected FEWS NET common unit/currency in {r['dataseries']}")
        key, market = markets[r["market"]]
        commodity, unit = product
        month = r["start_date"][:7]
        value = Decimal(r["value"]).quantize(Decimal("0.01"))
        if r["currency"] == "USD":
            quoted = f"USD {value} per {unit}"
        else:
            rate = Decimal(r["exchange_rate"]).quantize(Decimal("0.00001"))
            quoted = f"{r['currency']} {value} per {unit}, converted by FEWS NET at {rate} USD/{r['currency']}"
        rows.append({
            "record_id": f"price:{slug(commodity)}:{slug(market)}:{month}",
            "district": key,
            "market": market,
            "commodity": commodity,
            "unit": "KG",
            "month": month,
            "price_usd": str(Decimal(r["common_currency_price"]).quantize(Decimal("0.001"))),
            "price_flag": "aggregate",
            "source": (
                f"FEWS NET Data Warehouse (dataseries {r['dataseries']}, {r['market']}), "
                f"monthly aggregate of weekly retail prices; {quoted}"
            ),
        })
    return rows


def main() -> None:
    rows = wfp_rows() + fews_rows()
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
