"""App-level monthly spend cutoff (M8, architecture doc §10, cost control #5).

A running total of every answered question's ``usage.cost_usd`` for the current
UTC month, in Redis (``budget:<YYYY-MM>``, ~40-day TTL). When it reaches
``MONTHLY_BUDGET_USD`` the API behaves as if ``DEMO_ENABLED=false`` until the
month rolls over — no redeploy. This is the app's own backstop; the Anthropic
Console hard cap and the GCP billing budget sit behind it.

No Redis configured -> ``spent()`` is 0 and ``over_budget()`` is always False
(local dev never trips).
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

from app import store
from config import get_settings

_TTL = 40 * 86_400


def _month() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m")


class MonthBudget:
    def __init__(self, client=None, limit_usd: float = 15.0) -> None:
        self._client = client
        self.limit_usd = limit_usd

    def _key(self) -> str:
        return f"budget:{_month()}"

    def spent(self) -> float:
        if self._client is None:
            return 0.0
        try:
            return float(self._client.get(self._key()) or 0.0)
        except Exception:  # noqa: BLE001 - fail open (don't wall the demo on a cache blip)
            return 0.0

    def add(self, cost_usd: float) -> None:
        if self._client is None or not cost_usd:
            return
        try:
            pipe = self._client.pipeline()
            pipe.incrbyfloat(self._key(), float(cost_usd))
            pipe.expire(self._key(), _TTL)
            pipe.execute()
        except Exception:  # noqa: BLE001 - best effort
            pass

    def over_budget(self) -> bool:
        return self.spent() >= self.limit_usd


@lru_cache
def month_budget() -> MonthBudget:
    return MonthBudget(store.client(), limit_usd=get_settings().MONTHLY_BUDGET_USD)
