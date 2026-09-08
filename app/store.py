"""Shared Redis handle for the public-demo middleware (M8, architecture doc §10).

``client()`` returns one pooled ``redis.Redis`` built from ``REDIS_URL`` (Upstash
in production), or ``None`` when ``REDIS_URL`` is unset — which is the normal
local / test case. Every consumer (``cache``, ``limits``, ``budget``) treats a
``None`` client as "feature off": the cache always misses, the rate limiter
always allows, the budget never trips. So the whole demo layer is inert until a
Redis URL is configured, and `DEPLOY_MODE=deployed` requires one (`config._REQUIRED`).
"""

from __future__ import annotations

from functools import lru_cache

from config import get_settings


@lru_cache
def client():
    """A ``redis.Redis`` (decode_responses=True) or ``None`` if no ``REDIS_URL``."""
    url = get_settings().REDIS_URL
    if not url or not url.startswith(("redis://", "rediss://")):
        return None
    import redis  # local import: not needed for local dev / most tests

    return redis.from_url(url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3)
