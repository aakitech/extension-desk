"""Extension Desk tools, as plain functions over a DataStore and a
ReviewQueue. `server.py` exposes them over MCP.

Read tools return the records they used and a `citations` list of their
record IDs. Acting tools never send anything: each one validates its
citations, then writes a `pending_approval` item to the review queue for an
officer. A call that fails validation writes nothing.
"""

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
    total = round(sum(float(r["rain_mm"]) for r in rows), 1)
    normal = round(sum(float(r["normal_mm"]) for r in rows), 1)
    return {
        "district": district.strip().lower(),
        "period": {"start": rows[0]["dekad_start"], "end": rows[-1]["dekad_end"]} if rows else None,
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
    available = store.prices(district)
    if crop and not any(crop.lower() in r["commodity"].lower() for r in available):
        raise ValueError(f"no prices for {crop!r} in {district}; available: "
                         + ", ".join(sorted({r['commodity'] for r in available})))
    if market and not any(market.lower() == r["market"].lower() for r in available):
        raise ValueError(f"no market {market!r} in {district}; available: "
                         + ", ".join(sorted({r['market'] for r in available})))
    rows = [r for r in store.prices(district, since=since)
            if (not crop or crop.lower() in r["commodity"].lower())
            and (not market or market.lower() == r["market"].lower())]
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
    households = [store.household(household_id)] if household_id else store.households(district)
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
    household: the household itself, one of its plots, or a rainfall, price
    or pest record for its district."""
    if not citations:
        raise CitationError("at least one citation is required")
    district = store.household(household_id)["district"]
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
        else:
            ok = False
        if not ok:
            bad.append(f"{rid}: not about {household_id} or {district}")
    if bad:
        raise CitationError("invalid citations: " + "; ".join(bad))


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
