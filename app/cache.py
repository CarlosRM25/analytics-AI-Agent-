"""Response cache for the public demo (M8, architecture doc §10).

Key: ``sha256(normalize(question) · dataset · model)`` — ``normalize`` lowercases,
trims, and collapses whitespace. Value: the JSON ``/ask`` payload
(``{answer, steps, usage, stopped}``), TTL ``RESPONSE_CACHE_TTL_DAYS`` (30). A
hit is served verbatim and does **not** count against the rate limit.

Exact-match only. The interface (``get`` / ``put``) stays the same if this later
becomes an embedding nearest-neighbour lookup. No Redis configured -> every
lookup misses and ``put`` is a no-op.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from functools import lru_cache

from app import store
from config import get_settings

_PREFIX = "respcache:"


def normalize(question: str) -> str:
    return " ".join(question.lower().split())


class ResponseCache:
    def __init__(self, client=None, ttl_seconds: int | None = None) -> None:
        self._client = client
        self._ttl = ttl_seconds or get_settings().RESPONSE_CACHE_TTL_DAYS * 86_400

    def _key(self, question: str, dataset: str, model: str) -> str:
        raw = f"{normalize(question)}|{dataset}|{model}".encode()
        return _PREFIX + hashlib.sha256(raw).hexdigest()

    def get(self, question: str, dataset: str, model: str) -> dict | None:
        if self._client is None:
            return None
        try:
            blob = self._client.get(self._key(question, dataset, model))
        except Exception:  # noqa: BLE001 - a flaky cache must never break /ask
            return None
        if not blob:
            return None
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            return None

    def put(self, question: str, dataset: str, model: str, payload: dict) -> None:
        if self._client is None:
            return
        with contextlib.suppress(Exception):  # best effort — a cache write must not break /ask
            self._client.set(self._key(question, dataset, model), json.dumps(payload), ex=self._ttl)


@lru_cache
def response_cache() -> ResponseCache:
    return ResponseCache(store.client())
