"""M0 smoke tests: the scaffold imports and the two real pieces behave.

No API key, no database — these must stay runnable on a bare checkout.
"""

import dataclasses

import pytest
from pydantic import ValidationError

import config
from sources.base import REGISTRY, FieldMap, SourceSpec, get, register

_SECRETS = ("ANTHROPIC_API_KEY", "REDIS_URL", "CORS_ALLOWED_ORIGIN", "SOCRATA_APP_TOKEN")


def test_config_module_exposes_factory():
    assert callable(config.get_settings)


def test_local_mode_needs_no_secrets(monkeypatch):
    for var in _SECRETS:
        monkeypatch.delenv(var, raising=False)
    settings = config.Settings(DEPLOY_MODE="local", _env_file=None)
    assert settings.SQLITE_PATH.endswith("analytics.db")
    assert settings.ANTHROPIC_API_KEY is None


def test_deployed_mode_requires_api_key_redis_cors(monkeypatch):
    for var in _SECRETS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError):
        config.Settings(DEPLOY_MODE="deployed", _env_file=None)


def test_deployed_mode_ok_with_minimum(monkeypatch):
    for var in _SECRETS:
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
