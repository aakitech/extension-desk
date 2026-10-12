import asyncio
import json
import os
import sys
import threading
from datetime import date

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from extension_desk.data import DataStore
from extension_desk.mcp_server import tools
from extension_desk.mcp_server.server import build_server
from extension_desk.mcp_server.tools import CitationError, Recommendation
from extension_desk.review_queue import PENDING, ReviewQueue

TOOLS = {"get_rainfall", "get_prices", "get_plot_records", "get_pest_alerts",
         "draft_advisory", "flag_for_followup", "schedule_followup"}
TODAY = date(2026, 10, 12)


@pytest.fixture(scope="module")
def store() -> DataStore:
    return DataStore.load()


@pytest.fixture
def queue(tmp_path) -> ReviewQueue:
    return ReviewQueue(tmp_path / "queue.sqlite3")


def chiredzi_household(store):
    return store.households("chiredzi")[0]["record_id"]


def rec(text, *citations):
    return Recommendation(text=text, citations=list(citations))


# --- read tools -------------------------------------------------------------

def test_rainfall_defaults_to_latest_six_dekads_and_cites_each(store):
    out = tools.get_rainfall(store, "mangwe")
    latest = store.rainfall("mangwe")[-6:]
    assert out["citations"] == [r["record_id"] for r in latest]
    assert out["total_mm"] == pytest.approx(sum(float(r["rain_mm"]) for r in latest), abs=0.05)
    assert out["departure_mm"] == pytest.approx(out["total_mm"] - out["normal_mm"], abs=0.05)
    assert out["includes_preliminary"] is True  # Sep 2026 is preliminary CHIRPS


def test_rainfall_dry_season_has_no_percentage(store):
    out = tools.get_rainfall(store, "mangwe", start="2026-08", end="2026-08")
    assert out["normal_mm"] < 5 and out["anomaly_pct"] is None
    assert len(out["citations"]) == 3 and out["includes_preliminary"] is False


def test_prices_match_crop_by_name_and_cite_every_point(store):
    out = tools.get_prices(store, "chiredzi", crop="maize")
    assert {s["commodity"] for s in out["series"]} == {"Maize", "Maize meal", "Maize grain (white)"}
    points = [p["record_id"] for s in out["series"] for p in s["points"]]
    assert sorted(points) == out["citations"]
    millet = tools.get_prices(store, "mangwe", crop="millet", market="gwanda")
    assert [(s["market"], s["commodity"]) for s in millet["series"]] == [("Gwanda", "Pearl millet")]
    series = millet["series"][0]
    first, last = series["first"]["price_usd"], series["latest"]["price_usd"]
    assert series["change_pct"] == round((last - first) / first * 100)


@pytest.mark.parametrize("kwargs, message", [
    ({"crop": "cassava"}, "available: "),
    ({"market": "Harare"}, "available: "),
])
def test_prices_unknown_crop_or_market_raises_with_options(store, kwargs, message):
    with pytest.raises(ValueError, match=message):
        tools.get_prices(store, "chiredzi", **kwargs)


def test_plot_records_by_household_or_district(store):
    hh = chiredzi_household(store)
    one = tools.get_plot_records(store, household_id=hh)
    plots = store.plots(hh)
    assert one["citations"] == [hh, *(p["record_id"] for p in plots)]
    district = tools.get_plot_records(store, district="mangwe")
    assert len(district["households"]) == len(store.households("mangwe"))
    for kwargs in ({}, {"household_id": hh, "district": "mangwe"}):
        with pytest.raises(ValueError, match="exactly one"):
            tools.get_plot_records(store, **kwargs)


def test_pest_alerts_newest_first(store):
    out = tools.get_pest_alerts(store, "chiredzi")
    dates = [a["reported_on"] for a in out["alerts"]]
    assert dates == sorted(dates, reverse=True) and len(out["citations"]) == len(dates)


# --- acting tools: the invariant -------------------------------------------
# No tool sends anything or changes approval status. Acting tools only insert
# a pending item, and only when every citation exists and is about this
# household or its district. A rejected call writes nothing.

def test_draft_advisory_queues_a_pending_draft_with_all_citations(store, queue):
    hh = chiredzi_household(store)
    plot = store.plots(hh)[0]["record_id"]
    out = tools.draft_advisory(store, queue, hh, [
        rec("Wait for 20 mm before planting", "rain:chiredzi:2026-09-d3", "rain:chiredzi:2026-09-d3"),
        rec("Scout sorghum for fall armyworm", "pest:chiredzi:001", plot),
    ])
    assert out["status"] == PENDING and out["item_id"].startswith("advisory:")
    assert "Nothing has been sent" in out["note"]
    assert out["citations"] == ["rain:chiredzi:2026-09-d3", "pest:chiredzi:001", plot]
    assert out["payload"]["recommendations"][0]["citations"] == ["rain:chiredzi:2026-09-d3"]
    assert queue.items() == [{k: v for k, v in out.items() if k != "note"}]


@pytest.mark.parametrize("citations, message", [
    ([], "at least one citation"),
    (["rain:chiredzi:2099-01-d1"], "no such record"),
    (["rain:mangwe:2026-09-d3"], "not about"),       # another district's rain
    (["price:sorghum:gwanda:2026-09"], "not about"),  # another district's market
    (["hh:026"], "not about"),                        # another household
    (["plot:002-1"], "not about"),                    # another household's plot
    (["advisory:1"], "no such record"),               # a queue item is not a source
])
def test_one_bad_recommendation_rejects_the_whole_draft(store, queue, citations, message):
    good = rec("Scout for fall armyworm", "pest:chiredzi:001")
    with pytest.raises(CitationError, match=message):
        tools.draft_advisory(store, queue, "hh:001", [good, rec("Plant now", *citations)])
    assert queue.items() == []


def test_draft_advisory_rejects_empty_input_and_unknown_household(store, queue):
    with pytest.raises(ValueError, match="at least one recommendation"):
        tools.draft_advisory(store, queue, "hh:001", [])
    with pytest.raises(ValueError, match="must not be empty"):
        tools.draft_advisory(store, queue, "hh:001", [rec("  ", "hh:001")])
    with pytest.raises(ValueError, match="unknown household"):
        tools.draft_advisory(store, queue, "hh:999", [rec("x", "hh:001")])
    with pytest.raises(ValueError, match="unknown household"):
        tools.draft_advisory(store, queue, "plot:001-1", [rec("x", "hh:001")])
    assert queue.items() == []


def test_flag_for_followup_needs_valid_citations(store, queue):
    with pytest.raises(CitationError):
        tools.flag_for_followup(store, queue, "hh:001", "No phone, missed last visit", [])
    with pytest.raises(ValueError, match="must not be empty"):
        tools.flag_for_followup(store, queue, "hh:001", "", ["hh:001"])
    assert queue.items() == []
    out = tools.flag_for_followup(store, queue, "hh:001", "Missed last visit", ["hh:001"])
    assert out["status"] == PENDING and out["item_id"].startswith("flag:")


@pytest.mark.parametrize("visit_date, message", [
    ("2026-10-11", "in the past"),
    ("next week", "YYYY-MM-DD"),
    ("2026-02-30", "YYYY-MM-DD"),
])
def test_schedule_followup_rejects_bad_dates(store, queue, visit_date, message):
    with pytest.raises(ValueError, match=message):
        tools.schedule_followup(store, queue, "hh:001", visit_date, "Check seedlings",
                                ["hh:001"], today=TODAY)
    assert queue.items() == []


def test_schedule_followup_proposes_a_visit(store, queue):
    out = tools.schedule_followup(store, queue, "hh:001", "2026-10-12", "Check seedlings",
                                  ["hh:001"], today=TODAY)
    assert out["status"] == PENDING and out["payload"]["visit_date"] == "2026-10-12"
    assert "Nothing has been booked or sent" in out["note"]


def test_queue_survives_a_new_connection_and_ids_are_unique_under_concurrency(store, tmp_path):
    path = tmp_path / "queue.sqlite3"
    first = ReviewQueue(path)
    results, errors = [], []

    def worker(i):
        try:
            results.append(tools.flag_for_followup(store, ReviewQueue(path), "hh:001", f"r{i}", ["hh:001"]))
        except Exception as e:  # pragma: no cover - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len({r["item_id"] for r in results}) == 20
    assert len(first.items()) == 20 and {i["status"] for i in first.items()} == {PENDING}


def test_queue_refuses_an_item_without_citations(queue):
    with pytest.raises(ValueError, match="citation"):
        queue.add("advisory", "hh:001", {}, [], "test")
    with pytest.raises(ValueError, match="kind"):
        queue.add("sms", "hh:001", {}, ["hh:001"], "test")


# --- over MCP ---------------------------------------------------------------

def test_server_exposes_exactly_the_issue_tools_with_hints(store, queue):
    listed = asyncio.run(build_server(store, queue).list_tools())
    assert {t.name for t in listed} == TOOLS  # no approve, send or order tool
    for t in listed:
        assert t.annotations.readOnlyHint is t.name.startswith("get_"), t.name
        assert t.annotations.openWorldHint is False, t.name


def test_rejected_call_over_mcp_is_an_error_and_writes_nothing(store, queue):
    server = build_server(store, queue)
    args = {"household_id": "hh:001",
            "recommendations": [{"text": "Plant now", "citations": []}]}
    with pytest.raises(ToolError, match="at least one citation"):
        asyncio.run(server.call_tool("draft_advisory", args))
    assert queue.items() == []
    args["recommendations"][0]["citations"] = ["rain:chiredzi:2026-09-d3"]
    content = asyncio.run(server.call_tool("draft_advisory", args))
    assert json.loads(content[0].text)["status"] == PENDING
    assert len(queue.items()) == 1


def test_server_runs_standalone_over_stdio(tmp_path):
    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "extension_desk.mcp_server"],
            env={**os.environ, "EXTENSION_DESK_QUEUE": str(tmp_path / "queue.sqlite3")},
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            result = await session.call_tool("get_rainfall", {"district": "chiredzi"})
            return {t.name for t in listed.tools}, result

    names, result = asyncio.run(asyncio.wait_for(run(), timeout=60))
    assert names == TOOLS
    assert not result.isError
    assert json.loads(result.content[0].text)["citations"][0].startswith("rain:chiredzi:")
