"""Shared fixtures."""

import pytest

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
