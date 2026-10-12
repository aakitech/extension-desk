"""Extension Desk tools, as plain functions over a DataStore and a
ReviewQueue. `server.py` exposes them over MCP.

Read tools return the records they used and a `citations` list of their
record IDs. Acting tools never send anything: each one validates its
citations, then writes a `pending_approval` item to the review queue for an
officer. A call that fails validation writes nothing.
"""

import re
from datetime import date

from pydantic import BaseModel, Field

from extension_desk.data import DataStore, UnknownRecord
from extension_desk.review_queue import ReviewQueue

DEFAULT_DEKADS = 6  # about two months, when no period is given
MIN_NORMAL_MM = 5.0  # matches scripts/fetch_rainfall.py: below this, % is noise
NOT_SENT = "Queued for officer review. Nothing has been sent to the household."


class Recommendation(BaseModel):
    text: str = Field(description="One piece of advice for the household.")
    citations: list[str] = Field(
        description="Record IDs this advice relies on (rain:, price:, plot:, pest:, hh:). At least one.")


class CitationError(ValueError):
    pass


# --- read tools -------------------------------------------------------------

def get_rainfall(store: DataStore, district: str, start: str = "", end: str = "") -> dict:
    rows = store.rainfall(district, start=start, end=end)
    if not start and not end:
        rows = rows[-DEFAULT_DEKADS:]
    if not rows:
        # No records is not the same as zero rain: say so, don't sum to 0.
        every = store.rainfall(district)
        note = ("No rainfall records for this period; the latest dekad is " + every[-1]["dekad_start"]
                if every else f"No rainfall records at all for {district.strip().lower()}")
        return {"district": district.strip().lower(), "period": None, "total_mm": None,
                "normal_mm": None, "departure_mm": None, "anomaly_pct": None,
                "includes_preliminary": False, "dekads": [], "citations": [], "note": note}
    total = round(sum(float(r["rain_mm"]) for r in rows), 1)
    normal = round(sum(float(r["normal_mm"]) for r in rows), 1)
    return {
        "district": district.strip().lower(),
        "period": {"start": rows[0]["dekad_start"], "end": rows[-1]["dekad_end"]},
        "total_mm": total,
        "normal_mm": normal,
        "departure_mm": round(total - normal, 1),
        "anomaly_pct": round((total - normal) / normal * 100) if normal >= MIN_NORMAL_MM else None,
        "includes_preliminary": any("preliminary" in r["source"] for r in rows),
        "dekads": [
            {k: r[k] for k in ("record_id", "dekad_start", "dekad_end", "rain_mm", "normal_mm",
                               "departure_mm", "anomaly_pct", "source")}
            for r in rows
        ],
        "citations": [r["record_id"] for r in rows],
    }


def get_prices(store: DataStore, district: str, crop: str = "", market: str = "",
               since: str = "") -> dict:
    crop, market = crop.strip().lower(), market.strip().lower()

    def matches(r: dict) -> bool:
        return ((not crop or crop in r["commodity"].lower())
                and (not market or market == r["market"].lower()))

    available = store.prices(district)
    # Checked on the combined filter, so a real crop at a real market that
    # never quotes it raises instead of looking like missing data.
    if (crop or market) and not any(matches(r) for r in available):
        pairs = sorted({f"{r['commodity']} at {r['market']}" for r in available})
        raise ValueError(f"no prices for crop={crop!r} market={market!r} in {district}; "
                         "available: " + ", ".join(pairs))
    rows = [r for r in store.prices(district, since=since) if matches(r)]
    series: dict[tuple[str, str], list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["month"]):
        series.setdefault((r["market"], r["commodity"]), []).append(r)
    out = []
    for (mkt, commodity), points in sorted(series.items()):
        first, last = float(points[0]["price_usd"]), float(points[-1]["price_usd"])
        out.append({
            "market": mkt,
            "commodity": commodity,
            "unit": "USD/kg",
            "first": {"month": points[0]["month"], "price_usd": first, "record_id": points[0]["record_id"]},
            "latest": {"month": points[-1]["month"], "price_usd": last, "record_id": points[-1]["record_id"]},
            "change_pct": round((last - first) / first * 100) if len(points) > 1 else None,
            "points": [{"month": p["month"], "price_usd": float(p["price_usd"]),
                        "record_id": p["record_id"], "source": p["source"]} for p in points],
        })
    return {
        "district": district.strip().lower(),
        "series": out,
        "citations": [r["record_id"] for r in sorted(rows, key=lambda r: r["record_id"])],
    }


def get_plot_records(store: DataStore, household_id: str = "", district: str = "") -> dict:
    if bool(household_id) == bool(district):
        raise ValueError("give exactly one of household_id or district")
    households = [_household(store, household_id)] if household_id else store.households(district)
    out, citations = [], []
    for hh in households:
        plots = store.plots(hh["record_id"])
        out.append({**hh, "plots": plots})
        citations += [hh["record_id"], *(p["record_id"] for p in plots)]
    return {"households": out, "citations": citations}


def get_pest_alerts(store: DataStore, district: str, since: str = "") -> dict:
    alerts = store.pest_alerts(district, since=since)
    return {
        "district": district.strip().lower(),
        "alerts": sorted(alerts, key=lambda a: a["reported_on"], reverse=True),
        "citations": [a["record_id"] for a in alerts],
    }


# --- acting tools -----------------------------------------------------------

def check_citations(store: DataStore, household_id: str, citations: list[str]) -> None:
    """Every citation must be a record that exists and is about this
    household: the household itself, one of its plots, a rainfall or price
    record for its district, or a pest alert for its district on a crop the
    household grows. This proves each citation is relevant to the household;
    it can't prove the record supports the claim. That is the officer's call
    at the approval gate.

    Prices stay district-wide on purpose: another crop's price can matter
    (a sorghum grower buys maize meal)."""
    if not citations:
        raise CitationError("at least one citation is required")
    district = store.household(household_id)["district"]
    crops = {p["crop"] for p in store.plots(household_id)}
    bad = []
    for rid in citations:
        try:
            record = store.get(rid)
        except UnknownRecord:
            bad.append(f"{rid}: no such record")
            continue
        prefix = rid.split(":", 1)[0]
        if prefix == "hh":
            ok = rid == household_id
        elif prefix == "plot":
            ok = record["household_id"] == household_id
        elif prefix in ("rain", "price", "pest"):
            ok = record["district"] == district
            if ok and prefix == "pest" and not crops & set(record["crops_affected"].split(";")):
                bad.append(f"{rid}: affects {record['crops_affected'].replace(';', ', ')}, which "
                           f"{household_id} doesn't grow ({', '.join(sorted(crops)) or 'no plots'})")
                continue
        else:
            ok = False
        if not ok:
            bad.append(f"{rid}: not about {household_id} or {district}")
    if bad:
        raise CitationError("invalid citations: " + "; ".join(bad))


EVIDENCE = ("rain", "price", "pest", "plot")


def _household(store: DataStore, household_id: str) -> dict:
    try:
        return store.household(household_id)
    except UnknownRecord:
        raise ValueError(f"unknown household {household_id!r}") from None


def _text(value: str, what: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{what} must not be empty")
    return value.strip()


def draft_advisory(store: DataStore, queue: ReviewQueue, household_id: str,
                   recommendations: list[Recommendation]) -> dict:
    _household(store, household_id)
    if not recommendations:
        raise ValueError("at least one recommendation is required")
    recs = []
    # Validate everything before writing, so one uncited line rejects the draft.
    for i, rec in enumerate(recommendations, 1):
        text = _text(rec.text, f"recommendation {i} text")
        try:
            check_citations(store, household_id, rec.citations)
            # The household record alone says who, not why: each line of
            # advice must also cite rainfall, price, pest or plot evidence.
            if not any(c.split(":", 1)[0] in EVIDENCE for c in rec.citations):
                raise CitationError("needs at least one rain:, price:, pest: or plot: citation")
        except CitationError as e:
            raise CitationError(f"recommendation {i} ({text[:40]!r}): {e}") from None
        recs.append({"text": text, "citations": list(dict.fromkeys(rec.citations))})
    citations = list(dict.fromkeys(c for r in recs for c in r["citations"]))
    item = queue.add("advisory", household_id, {"recommendations": recs}, citations, "draft_advisory")
    return {**item, "note": NOT_SENT}


def flag_for_followup(store: DataStore, queue: ReviewQueue, household_id: str, reason: str,
                      citations: list[str]) -> dict:
    _household(store, household_id)
    reason = _text(reason, "reason")
    check_citations(store, household_id, citations)
    citations = list(dict.fromkeys(citations))
    item = queue.add("flag", household_id, {"reason": reason}, citations, "flag_for_followup")
    return {**item, "note": NOT_SENT}


def schedule_followup(store: DataStore, queue: ReviewQueue, household_id: str, visit_date: str,
                      reason: str, citations: list[str], today: date | None = None) -> dict:
    _household(store, household_id)
    reason = _text(reason, "reason")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", visit_date):
            raise ValueError
        when = date.fromisoformat(visit_date)
    except ValueError:
        raise ValueError(f"visit_date must be YYYY-MM-DD, got {visit_date!r}") from None
    if when < (today or date.today()):
        raise ValueError(f"visit_date {visit_date} is in the past")
    check_citations(store, household_id, citations)
    citations = list(dict.fromkeys(citations))
    item = queue.add("visit", household_id, {"visit_date": when.isoformat(), "reason": reason},
                     citations, "schedule_followup")
    return {**item, "note": "Proposed visit queued for officer approval. Nothing has been booked or sent."}
