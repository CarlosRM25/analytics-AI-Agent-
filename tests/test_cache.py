"""M8: the response cache — key normalisation, round-trip, and no-op without Redis."""

from __future__ import annotations

from app.cache import ResponseCache, normalize, response_cache

_PAYLOAD = {
    "answer": "42",
    "steps": [{"tool": "run_sql", "sql": "SELECT 1"}],
    "stopped": "end_turn",
}


def test_normalize_collapses_case_and_whitespace():
    assert normalize("  How   MANY\tbuildings? ") == "how many buildings?"


def test_key_is_stable_and_scoped():
    c = ResponseCache(client=None)
    k1 = c._key("How many?", "seattle_energy", "claude-haiku-4-5")
    k2 = c._key("how   many?", "seattle_energy", "claude-haiku-4-5")
    assert k1 == k2 and k1.startswith("respcache:")
    assert c._key("How many?", "seattle_energy", "claude-opus-5") != k1  # model in the key
    assert c._key("How many?", "other", "claude-haiku-4-5") != k1  # dataset in the key


def test_roundtrip_with_fake_redis(fake_redis):
    c = ResponseCache(client=fake_redis, ttl_seconds=60)
    assert c.get("q", "seattle_energy", "m") is None
    c.put("q", "seattle_energy", "m", _PAYLOAD)
    assert c.get("  Q  ", "seattle_energy", "m") == _PAYLOAD
    # TTL was set
    assert 0 < fake_redis.ttl(c._key("q", "seattle_energy", "m")) <= 60


def test_no_client_is_a_silent_no_op():
    c = ResponseCache(client=None)
    c.put("q", "d", "m", _PAYLOAD)  # must not raise
    assert c.get("q", "d", "m") is None


def test_factory_is_a_noop_without_redis_url():
    # default env has no REDIS_URL -> the singleton cache never stores anything
    c = response_cache()
    c.put("q", "d", "m", _PAYLOAD)
    assert c.get("q", "d", "m") is None
