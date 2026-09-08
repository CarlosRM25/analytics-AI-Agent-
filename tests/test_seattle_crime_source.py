"""M9: the seattle_crime SourceSpec is internally consistent, matches the DDL,
and exercises the generic-ingestion additions (flat table, datetime time column,
null_tokens, the "year" dtype). No network, no database.
"""

import re
from pathlib import Path

import pytest

from ingest.run import _coerce, _stage
from sources import base
from sources.seattle_crime import SPEC

_ALLOWED_DTYPES = {"str", "int", "float", "bool", "year"}
_SCHEMA_SQL = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


@pytest.fixture(autouse=True)
def _restore_registry():
    snapshot = dict(base.REGISTRY)
    try:
        yield
    finally:
        base.REGISTRY.clear()
        base.REGISTRY.update(snapshot)


def _schema_columns() -> dict[str, set[str]]:
    text = _SCHEMA_SQL.read_text(encoding="utf-8")
    tables: dict[str, set[str]] = {}
    for block in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);", text, re.DOTALL):
        name, body = block.group(1), block.group(2)
        cols: set[str] = set()
        for line in body.splitlines():
            m = re.match(r"([a-z_][a-z0-9_]*)\s+", line.strip())
            if m and m.group(1).upper() not in {"PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT"}:
                cols.add(m.group(1))
        tables[name] = cols
    return tables


def test_spec_identity():
    assert SPEC.key == "seattle_crime"
    assert SPEC.resource_id == "tazs-3rd5"
    assert SPEC.target_tables == ("crime_incidents",)
    assert SPEC.upsert_keys == {"crime_incidents": ("offense_id",)}
    assert SPEC.model_module is None  # no predict tool
    assert SPEC.static_table is None  # flat — no slowly-changing dimension
    assert SPEC.time_column == "report_date_time" and SPEC.time_is_datetime
    assert SPEC.year_column == "report_year"
    assert SPEC.fact_table == "crime_incidents"


def test_importing_does_not_register_but_load_does():
    assert "seattle_crime" not in base.REGISTRY
    assert base.load("seattle_crime") is SPEC
    assert base.REGISTRY["seattle_crime"] is SPEC


def test_field_map_targets_are_valid_and_in_schema():
    schema = _schema_columns()
    assert "crime_incidents" in schema
    for _src, fm in SPEC.field_targets():
        assert fm.table == "crime_incidents"
        assert fm.dtype in _ALLOWED_DTYPES
        assert fm.column in schema["crime_incidents"], f"{fm.column} not in db/schema.sql"


def test_upsert_key_is_mapped():
    mapped = {(fm.table, fm.column) for _src, fm in SPEC.field_targets()}
    assert ("crime_incidents", "offense_id") in mapped


def test_coerce_year_and_null_tokens():
    assert _coerce("2025-06-14T21:03:00.000", "year") == 2025
    assert _coerce("-", "str", SPEC.null_tokens) is None
    assert _coerce("REDACTED", "str", SPEC.null_tokens) is None
    assert _coerce("CAPITOL HILL", "str", SPEC.null_tokens) == "CAPITOL HILL"


def test_stage_maps_a_raw_record_flat():
    raw = [
        {
            "offense_id": "72861701717",
            "report_number": "2025-265997",
            "report_date_time": "2025-06-14T21:03:00.000",
            "nibrs_crime_against_category": "PROPERTY",
            "offense_category": "PROPERTY CRIME",
            "block_address": "REDACTED",
            "latitude": "-1.0",
            "longitude": "-122.33",
        }
    ]
    (rows,) = _stage(SPEC, raw).values()
    assert len(rows) == 1
    row = rows[0]
    assert row["offense_id"] == "72861701717"
    assert row["report_datetime"] == "2025-06-14T21:03:00.000"
    assert row["report_year"] == 2025
    assert row["crime_against"] == "PROPERTY"
    assert row["block_address"] is None  # REDACTED -> null_tokens
    assert row["latitude"] is None  # -1.0 sentinel -> transform
    assert row["longitude"] == -122.33
    assert "__sort" not in row  # no static_table for this source


def test_stage_dedupes_on_offense_id():
    raw = [
        {"offense_id": "1", "offense_category": "ALL OTHER", "report_date_time": "2025-01-01"},
        {"offense_id": "1", "offense_category": "PROPERTY CRIME", "report_date_time": "2025-01-01"},
        {"offense_id": "2", "offense_category": "VIOLENT CRIME", "report_date_time": "2025-01-01"},
    ]
    (rows,) = _stage(SPEC, raw).values()
    assert len(rows) == 2
    by_id = {r["offense_id"]: r["offense_category"] for r in rows}
    assert by_id == {"1": "PROPERTY CRIME", "2": "VIOLENT CRIME"}  # last write wins
