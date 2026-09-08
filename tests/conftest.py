"""Shared fixtures."""

import pytest
from sqlalchemy import text

import config
from db import engine
from sources import base as _sources_base


def _clear_demo_singletons() -> None:
    """Drop the M8 middleware singletons (cache / limiter / budget / redis handle)
    so they rebuild from the current settings + ``store.client``. Does NOT touch
    ``get_settings`` — callers that also want fresh settings clear that too."""
    from app import budget, cache, limits, store

    for fn in (store.client, cache.response_cache, limits.rate_limiter, budget.month_budget):
        clear = getattr(fn, "cache_clear", None)  # may be monkeypatched to a plain lambda
        if clear:
            clear()


def _clear_caches() -> None:
    config.get_settings.cache_clear()
    engine.rw_engine.cache_clear()
    engine.ro_engine.cache_clear()
    _clear_demo_singletons()


@pytest.fixture
def fake_redis(monkeypatch):
    """Back the response cache / rate limiter / budget with an in-memory FakeRedis.
    Leaves ``get_settings`` alone so an autouse settings patch isn't lost."""
    import fakeredis

    from app import store

    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(store, "client", lambda: fake)
    _clear_demo_singletons()
    yield fake
    _clear_demo_singletons()


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
def loaded_crime_db(sqlite_env):
    """Migrated temp DB with a few crime_incidents rows + a datasets row (M9)."""
    from db import migrate
    from db.engine import rw_engine

    incidents = [
        {
            "offense_id": "a1",
            "report_year": 2025,
            "offense_category": "PROPERTY CRIME",
            "precinct": "North",
        },
        {
            "offense_id": "a2",
            "report_year": 2025,
            "offense_category": "VIOLENT CRIME",
            "precinct": "West",
        },
        {
            "offense_id": "a3",
            "report_year": 2024,
            "offense_category": "PROPERTY CRIME",
            "precinct": "North",
        },
    ]
    migrate.main()
    with rw_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO datasets (dataset_key, title, row_count, first_year, last_year) "
                "VALUES ('seattle_crime', 'SPD Crime', 3, 2024, 2025)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO crime_incidents (offense_id, report_year, offense_category, precinct) "
                "VALUES (:offense_id, :report_year, :offense_category, :precinct)"
            ),
            incidents,
        )
    return sqlite_env


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
