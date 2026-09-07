"""M1: ingestion's pure transform helpers. No network, no database."""

from ingest.run import _coerce, _dedupe_by_key, _dedupe_latest_non_null


def test_coerce_blank_and_na_to_none():
    assert _coerce("", "float") is None
    assert _coerce("   ", "int") is None
    assert _coerce("NA", "str") is None
    assert _coerce(None, "str") is None


def test_coerce_typed_values():
    assert _coerce("2016", "int") == 2016
    assert _coerce("69.3", "float") == 69.3
    assert _coerce(" Compliant ", "str") == "Compliant"
    assert _coerce("2.5", "int") == 2  # round-half-to-even, then int
    assert _coerce(False, "bool") == 0
    assert _coerce(True, "bool") == 1


def test_coerce_unparseable_number_to_none():
    assert _coerce("N/A", "float") is None
    assert _coerce("about 100", "int") is None


def test_dedupe_latest_non_null():
    rows = [
        {"ose_building_id": "1", "year_built": 1990, "neighborhood": None, "__year": 2015},
        {"ose_building_id": "1", "year_built": None, "neighborhood": "DOWNTOWN", "__year": 2020},
        {"ose_building_id": "2", "year_built": 1975, "neighborhood": "BALLARD", "__year": 2018},
    ]
    out = {r["ose_building_id"]: r for r in _dedupe_latest_non_null(rows, "ose_building_id")}
    assert set(out) == {"1", "2"}
    assert out["1"]["year_built"] == 1990  # newer row's value was null -> keep older
    assert out["1"]["neighborhood"] == "DOWNTOWN"  # newer non-null wins
    assert "__year" not in out["1"]


def test_dedupe_by_key_last_wins_drops_null_keys():
    rows = [
        {"ose_building_id": "1", "data_year": 2020, "site_eui": 50.0},
        {"ose_building_id": "1", "data_year": 2020, "site_eui": 55.0},
        {"ose_building_id": None, "data_year": 2020, "site_eui": 1.0},
    ]
    out = _dedupe_by_key(rows, ("ose_building_id", "data_year"))
    assert out == [{"ose_building_id": "1", "data_year": 2020, "site_eui": 55.0}]
