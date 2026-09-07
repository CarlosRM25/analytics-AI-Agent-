"""M0 smoke tests: the scaffold imports and the two real pieces behave.

No API key, no database — these must stay runnable on a bare checkout.
"""

import dataclasses

import pytest
from pydantic import ValidationError

import config
from sources.base import REGISTRY, FieldMap, SourceSpec, get, register

_LOCAL_REQUIRED = (
    "DB_ETL_USER",
    "DB_ETL_PASSWORD",
    "DB_AGENT_USER",
    "DB_AGENT_PASSWORD",
)


def test_config_module_exposes_factory():
    assert callable(config.get_settings)


def test_local_mode_requires_db_creds(monkeypatch):
    for var in _LOCAL_REQUIRED:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError):
        config.Settings(DEPLOY_MODE="local", _env_file=None)


def test_local_mode_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = config.Settings(
        DEPLOY_MODE="local",
        DB_ETL_USER="e",
        DB_ETL_PASSWORD="e",
        DB_AGENT_USER="a",
        DB_AGENT_PASSWORD="a",
        _env_file=None,
    )
    assert settings.ANTHROPIC_API_KEY is None


def test_deployed_mode_ok_with_minimum(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "REDIS_URL", "CORS_ALLOWED_ORIGIN"):
        monkeypatch.delenv(var, raising=False)
    settings = config.Settings(
        DEPLOY_MODE="deployed",
        ANTHROPIC_API_KEY="test",
        REDIS_URL="redis://localhost:6379",
        CORS_ALLOWED_ORIGIN="https://example.com",
        _env_file=None,
    )
    assert settings.DEPLOY_MODE == "deployed"
    assert settings.ANALYST_MODEL == "claude-opus-5"


def test_registry_starts_empty():
    assert REGISTRY == {}


def test_sourcespec_is_frozen_and_registers():
    spec = SourceSpec(
        key="dummy",
        socrata_domain="data.seattle.gov",
        resource_id="xxxx-xxxx",
        app_token_env="SOCRATA_APP_TOKEN",
        page_size=5000,
        field_map={"src": FieldMap(table="t", column="c", dtype="str")},
        target_tables=("t",),
        upsert_keys={"t": ("c",)},
        catalog_path="catalog/dummy.yaml",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.key = "changed"

    try:
        register(spec)
        assert get("dummy") is spec
        with pytest.raises(ValueError):
            register(spec)
    finally:
        REGISTRY.clear()
