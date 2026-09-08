"""M1: the seattle_energy SourceSpec is internally consistent and matches the
DDL. No network, no database.
"""

import re
from pathlib import Path

import pytest

from sources import base
from sources.seattle_energy import SPEC

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
    """{table_name: {column, ...}} parsed loosely from db/schema.sql."""
    text = _SCHEMA_SQL.read_text(encoding="utf-8")
    tables: dict[str, set[str]] = {}
    for block in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);", text, re.DOTALL):
        name, body = block.group(1), block.group(2)
        cols: set[str] = set()
        for line in body.splitlines():
            line = line.strip()
            m = re.match(r"([a-z_][a-z0-9_]*)\s+", line)
            if m and m.group(1).upper() not in {
                "PRIMARY",
                "UNIQUE",
                "KEY",
                "CONSTRAINT",
                "FOREIGN",
                "INDEX",
            }:
                cols.add(m.group(1))
        tables[name] = cols
    return tables


def test_importing_the_module_does_not_register():
    assert "seattle_energy" not in base.REGISTRY


def test_spec_identity():
    assert SPEC.key == "seattle_energy"
    assert SPEC.resource_id == "teqw-tu6e"
    assert SPEC.socrata_domain == "data.seattle.gov"
    assert SPEC.target_tables == ("buildings", "energy_records")  # FK-safe order
    assert SPEC.upsert_keys["energy_records"] == ("ose_building_id", "data_year")
    assert SPEC.model_module == "model.seattle_energy"


def test_load_registers_then_fixture_restores():
    spec = base.load("seattle_energy")
    assert spec is SPEC
    assert base.REGISTRY["seattle_energy"] is SPEC


def test_field_map_targets_are_known_tables():
    for _src, fm in SPEC.field_targets():
        assert fm.table in SPEC.target_tables
        assert fm.dtype in _ALLOWED_DTYPES


def test_field_map_columns_exist_in_schema():
    schema = _schema_columns()
    assert set(schema) >= {"buildings", "energy_records"}
    missing = [
        f"{fm.table}.{fm.column}"
        for _src, fm in SPEC.field_targets()
        if fm.column not in schema[fm.table]
    ]
    assert not missing, f"field_map targets not in db/schema.sql: {missing}"


def test_every_upsert_key_is_mapped():
    mapped = {(fm.table, fm.column) for _src, fm in SPEC.field_targets()}
    for table, keys in SPEC.upsert_keys.items():
        for col in keys:
            assert (table, col) in mapped, f"{table}.{col} is an upsert key but not in field_map"
