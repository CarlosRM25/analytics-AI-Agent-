"""Validated application configuration (architecture doc section 9).

Every environment variable the project reads is declared once here. Local and
deployed builds require different subsets of them; ``DEPLOY_MODE`` selects which,
and construction fails fast if anything required for that mode is missing.

Nothing is instantiated at import time. Call ``get_settings()`` from an entry
point (CLI, Flask app, ingest job) so a bad environment crashes at startup, not
on import — which keeps ``tests/`` runnable without any secrets.

Storage is SQLite everywhere (decision 2026-09-07; see the architecture doc's
section 7 amendment). ``SQLITE_PATH`` is the only storage knob — the same file
is used read/write by ingestion and read-only by the agent.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Local dev covers ingestion, EDA, and model work as well as the agent, and
# SQLite needs no credentials — so nothing is strictly required locally. The
# deployed service (M7: Flask + agent + a baked-in read-only SQLite snapshot on
# Cloud Run) only needs the API key to boot. REDIS_URL and CORS_ALLOWED_ORIGIN
# are checked by the M8 rate-limit / CORS middleware at its point of use, not
# here — M7 deploys without them.
_REQUIRED: dict[str, tuple[str, ...]] = {
    "local": (),
    "deployed": ("ANTHROPIC_API_KEY",),
}


class Settings(BaseSettings):
    """All configuration, loaded from the environment / ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DEPLOY_MODE: Literal["local", "deployed"] = "local"

    # --- storage + ingestion ---
    SQLITE_PATH: str = "./analytics.db"
    SOCRATA_APP_TOKEN: str | None = None

    # --- agent ---
    ANTHROPIC_API_KEY: str | None = None
    ANALYST_MODEL: str = "claude-opus-5"  # deployed instance sets claude-haiku-4-5
    MAX_SQL_ROWS: int = 1000
    MAX_AGENT_ITERS: int = 8
    SQL_TIMEOUT_MS: int = 5000

    # --- public deployment only (section 10) ---
    DEMO_ENABLED: bool = True
    RATE_LIMIT_PER_VISITOR_PER_DAY: int = 5
    GLOBAL_DAILY_QUESTION_CAP: int = 300
    MONTHLY_BUDGET_USD: float = 15.0
    RESPONSE_CACHE_TTL_DAYS: int = 30
    REDIS_URL: str | None = None  # Upstash — rate-limit counters + response cache
    TURNSTILE_SECRET: str | None = None  # blank = disabled
    CORS_ALLOWED_ORIGIN: str | None = None
    ALERT_EMAIL: str | None = None  # uptime + budget alerts

    @model_validator(mode="after")
    def _require_mode_subset(self) -> Settings:
        missing = [name for name in _REQUIRED[self.DEPLOY_MODE] if not getattr(self, name)]
        if missing:
            raise ValueError(
                f"missing required settings for DEPLOY_MODE={self.DEPLOY_MODE}: "
                + ", ".join(missing)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the validated settings singleton.

    Raises ``pydantic.ValidationError`` if the environment is incomplete for the
    active ``DEPLOY_MODE``.
    """
    return Settings()
