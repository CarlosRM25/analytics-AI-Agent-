"""Rate limiting for the public demo (M8, architecture doc §10).

Two Redis counters, both keyed by the UTC date with a 48h TTL:

    ratelimit:visitor:<ip_hash>:<date>   -> per-visitor novel questions today
    ratelimit:global:<date>              -> all visitors' novel questions today

``check()`` reads the counters without touching them, so a cache hit or a failed
run costs nothing. ``record()`` increments both and is called only after a novel,
successful answer. "Per visitor" is per hashed IP (``X-Forwarded-For`` first hop
on Cloud Run) — shared IPs undercount, rotation overcounts; fine for a demo.

No Redis configured -> ``check`` always allows and ``record`` is a no-op.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from app import store
from config import get_settings

_TTL = 48 * 3600  # cover a viewer whose "day" straddles UTC midnight


def hash_ip(ip: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:16]


def _today() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    scope: str = ""  # "" | "visitor" | "global"


class RateLimiter:
    def __init__(self, client=None, per_visitor: int = 5, global_cap: int = 300) -> None:
        self._client = client
        self.per_visitor = per_visitor
        self.global_cap = global_cap

    def _keys(self, ip_hash: str) -> tuple[str, str]:
        d = _today()
        return f"ratelimit:visitor:{ip_hash}:{d}", f"ratelimit:global:{d}"

    def check(self, ip_hash: str) -> Decision:
        if self._client is None:
            return Decision(True)
        vkey, gkey = self._keys(ip_hash)
        try:
            vcount = int(self._client.get(vkey) or 0)
            gcount = int(self._client.get(gkey) or 0)
        except Exception:  # noqa: BLE001 - a flaky limiter fails open
            return Decision(True)
        if vcount >= self.per_visitor:
            return Decision(False, "visitor")
        if gcount >= self.global_cap:
            return Decision(False, "global")
        return Decision(True)

    def record(self, ip_hash: str) -> None:
        if self._client is None:
            return
        vkey, gkey = self._keys(ip_hash)
        try:
            pipe = self._client.pipeline()
            pipe.incr(vkey)
            pipe.expire(vkey, _TTL)
            pipe.incr(gkey)
            pipe.expire(gkey, _TTL)
            pipe.execute()
        except Exception:  # noqa: BLE001 - best effort
            pass


@lru_cache
def rate_limiter() -> RateLimiter:
    s = get_settings()
    return RateLimiter(
        store.client(),
        per_visitor=s.RATE_LIMIT_PER_VISITOR_PER_DAY,
        global_cap=s.GLOBAL_DAILY_QUESTION_CAP,
    )
