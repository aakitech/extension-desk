import pytest

from extension_desk.data import DataStore, UnknownRecord
from extension_desk.districts import DISTRICTS


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


def test_filters(store):
    chiredzi = store.rainfall("chiredzi", start="2026-01-01", end="2026-01-31")
    assert [r["dekad_start"] for r in chiredzi] == ["2026-01-01", "2026-01-11", "2026-01-21"]
    maize = store.prices("chiredzi", commodity="maize meal")
    assert maize and all(r["commodity"] == "Maize meal" for r in maize)
