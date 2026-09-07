"""Shared fixtures."""

import pytest

import config
from db import engine


def _clear_caches() -> None:
    config.get_settings.cache_clear()
    engine.rw_engine.cache_clear()
    engine.ro_engine.cache_clear()


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
