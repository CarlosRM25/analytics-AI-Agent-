"""Shared fixtures."""

import pytest
from sqlalchemy import text

import config
from db import engine
from sources import base as _sources_base


def _clear_caches() -> None:
    config.get_settings.cache_clear()
    engine.rw_engine.cache_clear()
    engine.ro_engine.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_source_registry():
    """Every test starts with an empty ``sources.REGISTRY``; code that needs a
    source registered calls ``sources.base.load()`` itself (as the agent does)."""
    saved = dict(_sources_base.REGISTRY)
    _sources_base.REGISTRY.clear()
    try:
        yield
    finally:
        _sources_base.REGISTRY.clear()
        _sources_base.REGISTRY.update(saved)


@pytest.fixture
def sqlite_env(tmp_path, monkeypatch):
    """Point ``SQLITE_PATH`` at a fresh temp file; clear cached settings/engines
    around the test so ``get_settings()`` / ``rw_engine()`` pick it up."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("SQLITE_PATH", str(db_file))
    monkeypatch.setenv("DEPLOY_MODE", "local")
    _clear_caches()
    yield db_file
    _clear_caches()


@pytest.fixture
def loaded_db(sqlite_env):
    """Migrated temp DB with a handful of rows the agent tools can query."""
    from db import migrate
    from db.engine import rw_engine

    migrate.main()
    with rw_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO datasets (dataset_key, title, description, row_count) "
                "VALUES ('seattle_energy', 'Seattle Energy', 'test rows', 5)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO buildings (ose_building_id, primary_property_type) "
                "VALUES ('b1', 'Office')"
            )
        )
        for year, eui in enumerate([10, 20, 30, 40, 50], start=2019):
            conn.execute(
                text(
                    "INSERT INTO energy_records (ose_building_id, data_year, site_eui) "
                    "VALUES ('b1', :y, :e)"
                ),
                {"y": year, "e": eui},
            )
    return sqlite_env
