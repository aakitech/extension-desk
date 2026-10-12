import re
from datetime import date, timedelta

import pytest

from extension_desk.data import DataStore, UnknownRecord
from extension_desk.districts import DISTRICTS

_NORMAL = "; normal = 1991-2020 CHIRPS v3.0 final mean"
RAIN_SOURCES = {
    "CHIRPS v3.0 final (Climate Hazards Center, UCSB), 0.05-degree pixel" + _NORMAL: "final",
    "CHIRPS v3.0 preliminary (Climate Hazards Center, UCSB), 0.05-degree pixel; "
    "final not yet published" + _NORMAL: "prelim",
}
FEWS_QUOTE = re.compile(
    r"; (?P<currency>[A-Z]{3}) (?P<value>[\d.]+) per (?P<pack>[\d.]+)[ _]kg"
    r"(?:, converted by FEWS NET at (?P<rate>[\d.]+) USD/(?P<rate_currency>[A-Z]{3}))?$"
)


@pytest.fixture(scope="module")
def store() -> DataStore:
    return DataStore.load()


def test_every_record_has_id_and_source(store):
    for name, rows in store.tables.items():
        assert rows, f"{name} is empty"
        for row in rows:
            assert row["record_id"], name
            assert row["source"], row["record_id"]


def test_record_ids_are_unique_across_tables(store):
    ids = [r["record_id"] for rows in store.tables.values() for r in rows]
    assert len(ids) == len(set(ids))


def test_duplicate_ids_are_rejected():
    row = {"record_id": "x", "source": "s"}
    with pytest.raises(ValueError, match="duplicate"):
        DataStore({"a": [row], "b": [dict(row)]})


def test_get_and_unknown(store):
    rid = store.tables["rainfall"][0]["record_id"]
    assert store.get(rid)["record_id"] == rid
    assert store.exists(rid)
    assert not store.exists("rain:nowhere:2026-01-d1")
    with pytest.raises(UnknownRecord):
        store.get("rain:nowhere:2026-01-d1")


def test_household_lookup_rejects_non_household_ids(store):
    plot_id = store.tables["plots"][0]["record_id"]
    with pytest.raises(UnknownRecord):
        store.household(plot_id)


def test_every_district_has_rain_prices_households_and_alerts(store):
    for key in DISTRICTS:
        assert store.rainfall(key), key
        assert store.prices(key), key
        assert store.households(key), key
        assert store.pest_alerts(key), key


def test_every_plot_belongs_to_a_known_household(store):
    for plot in store.tables["plots"]:
        assert store.household(plot["household_id"])


def test_people_and_farms_are_synthetic(store):
    for name in ("households", "plots", "pest_alerts"):
        for row in store.tables[name]:
            assert row["source"].startswith("synthetic"), row["record_id"]


def test_dry_season_anomaly_is_blank_not_a_huge_percentage(store):
    for row in store.tables["rainfall"]:
        if float(row["normal_mm"]) < 5:
            assert row["anomaly_pct"] == "", row["record_id"]
        else:
            assert row["anomaly_pct"] != "", row["record_id"]


def test_departure_is_rain_minus_normal(store):
    for row in store.tables["rainfall"]:
        expected = round(float(row["rain_mm"]) - float(row["normal_mm"]), 1)
        assert float(row["departure_mm"]) == pytest.approx(expected), row["record_id"]


def test_filters(store):
    chiredzi = store.rainfall("chiredzi", start="2026-01-01", end="2026-01-31")
    assert [r["dekad_start"] for r in chiredzi] == ["2026-01-01", "2026-01-11", "2026-01-21"]
    maize = store.prices("chiredzi", commodity="maize meal")
    assert maize and all(r["commodity"] == "Maize meal" for r in maize)


def test_date_filters_accept_month_or_day_in_every_table(store):
    latest = max(r["month"] for r in store.prices("chiredzi"))
    assert store.prices("chiredzi", since=latest + "-01") == store.prices("chiredzi", since=latest)
    by_month = store.rainfall("chiredzi", start="2026-01", end="2026-01")
    by_day = store.rainfall("chiredzi", start="2026-01-01", end="2026-01-31")
    assert by_month == by_day and len(by_month) == 3



@pytest.mark.parametrize("call", [
    lambda s: s.rainfall("chiredzi", start="2026-13-99"),
    lambda s: s.rainfall("chiredzi", end="2026-02-30"),
    lambda s: s.rainfall("chiredzi", start="2026-03", end="2026-01"),
    # No cassava rows exist: a bad date must still raise, not return [].
    lambda s: s.prices("chiredzi", commodity="cassava", since="May 2026"),
    lambda s: s.pest_alerts("chiredzi", since="2026-1-5"),
])
def test_bad_date_input_raises_even_when_no_rows_match(store, call):
    with pytest.raises(ValueError):
        call(store)


def test_rainfall_normals_use_all_thirty_years(store):
    assert {r["normal_years"] for r in store.tables["rainfall"]} == {"30"}


def test_unknown_district_raises_instead_of_returning_nothing(store):
    assert store.rainfall("Chiredzi") == store.rainfall("chiredzi")
    with pytest.raises(ValueError, match="known districts"):
        store.rainfall("harare")


def test_returned_rows_are_copies(store):
    row = store.households("chiredzi")[0]
    row["district"] = "tampered"
    assert store.get(row["record_id"])["district"] == "chiredzi"


def test_pest_alerts_only_for_crops_already_planted(store):
    for district in DISTRICTS:
        planted = {}
        for hh in store.households(district):
            for plot in store.plots(hh["record_id"]):
                if plot["planted_on"]:
                    planted.setdefault(plot["crop"], []).append(plot["planted_on"])
        for alert in store.pest_alerts(district):
            crops = alert["crops_affected"].split(";")
            assert all(c in planted for c in crops), alert["record_id"]
            first_planting = min(d for c in crops for d in planted[c])
            # data/README.md promises at least a week after the first planting.
            earliest = date.fromisoformat(first_planting) + timedelta(days=7)
            assert date.fromisoformat(alert["reported_on"]) >= earliest, alert["record_id"]


def test_every_district_has_recent_non_maize_prices(store):
    latest = max(r["month"] for r in store.tables["prices"])
    for district in DISTRICTS:
        recent = {r["commodity"] for r in store.prices(district, since=latest)
                  if not r["commodity"].lower().startswith("maize")}
        assert {"Sorghum", "Pearl millet", "Groundnuts (shelled)"} <= recent, district


def test_prices_are_usd_per_kg_numbers(store):
    for row in store.tables["prices"]:
        assert row["unit"] == "KG", row["record_id"]
        assert float(row["price_usd"]) > 0, row["record_id"]


def test_fews_prices_match_the_quoted_price_pack_and_rate(store):
    rows = [r for r in store.tables["prices"] if r["source"].startswith("FEWS NET")]
    assert rows
    for row in rows:
        m = FEWS_QUOTE.search(row["source"])
        assert m, row["record_id"]
        per_kg = float(m["value"]) / float(m["pack"])
        if m["currency"] != "USD":
            assert m["rate_currency"] == m["currency"], row["record_id"]
            per_kg *= float(m["rate"])
        else:
            assert m["rate"] is None, row["record_id"]
        # Source keeps the quote to 2 dp and the rate to 5 dp, so allow that much.
        assert float(row["price_usd"]) == pytest.approx(per_kg, rel=0.01, abs=0.002), row["record_id"]


def test_rainfall_is_chirps_with_prelim_only_after_final(store):
    for district in DISTRICTS:
        sources = [r["source"] for r in store.rainfall(district)]
        assert set(sources) <= set(RAIN_SOURCES), district
        kinds = [RAIN_SOURCES[s] for s in sources]
        # Prelim fills only the tail the final archive hasn't reached.
        first_prelim = kinds.index("prelim") if "prelim" in kinds else len(kinds)
        assert all(k == "final" for k in kinds[:first_prelim]), district
        assert all(k == "prelim" for k in kinds[first_prelim:]), district


def test_mangwe_late_september_rain_is_in_the_data(store):
    # The "false start" eval case depends on this dekad existing.
    assert store.exists("rain:mangwe:2026-09-d3")


def test_data_dir_can_be_overridden_for_evals(tmp_path, monkeypatch):
    import shutil

    from extension_desk.data import DATA_DIR
    for f in DATA_DIR.glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    rows = (tmp_path / "pest_alerts.csv").read_text(encoding="utf-8").splitlines()
    (tmp_path / "pest_alerts.csv").write_text("\n".join(rows[:2]) + "\n", encoding="utf-8")
    monkeypatch.setenv("EXTENSION_DESK_DATA", str(tmp_path))
    assert len(DataStore.load().tables["pest_alerts"]) == 1
    monkeypatch.delenv("EXTENSION_DESK_DATA")
    assert len(DataStore.load().tables["pest_alerts"]) == 6
