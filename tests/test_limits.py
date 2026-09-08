"""M8: the rate limiter — per-visitor + global caps, read-without-consume, fail-open."""

from __future__ import annotations

from app.limits import RateLimiter, hash_ip


def test_hash_ip_is_deterministic_and_salted():
    a = hash_ip("1.2.3.4", "salt")
    assert a == hash_ip("1.2.3.4", "salt")
    assert a != hash_ip("1.2.3.4", "other-salt")
    assert a != hash_ip("1.2.3.5", "salt")
    assert len(a) == 16


def test_check_does_not_consume_quota(fake_redis):
    rl = RateLimiter(client=fake_redis, per_visitor=2, global_cap=100)
    ip = hash_ip("9.9.9.9", "s")
    for _ in range(10):
        assert rl.check(ip).allowed  # reads only — never trips on its own
    assert fake_redis.get(rl._keys(ip)[0]) is None


def test_per_visitor_cap(fake_redis):
    rl = RateLimiter(client=fake_redis, per_visitor=2, global_cap=100)
    ip = hash_ip("9.9.9.9", "s")
    rl.record(ip)
    assert rl.check(ip).allowed
    rl.record(ip)
    d = rl.check(ip)
    assert not d.allowed and d.scope == "visitor"
    assert hash_ip("8.8.8.8", "s") and rl.check(hash_ip("8.8.8.8", "s")).allowed  # other IP fine


def test_global_cap(fake_redis):
    rl = RateLimiter(client=fake_redis, per_visitor=100, global_cap=3)
    for i in range(3):
        rl.record(hash_ip(f"10.0.0.{i}", "s"))
    d = rl.check(hash_ip("10.0.0.99", "s"))
    assert not d.allowed and d.scope == "global"


def test_record_sets_ttl(fake_redis):
    rl = RateLimiter(client=fake_redis, per_visitor=5, global_cap=50)
    ip = hash_ip("1.1.1.1", "s")
    rl.record(ip)
    vkey, gkey = rl._keys(ip)
    assert fake_redis.ttl(vkey) > 0 and fake_redis.ttl(gkey) > 0


def test_no_client_fails_open():
    rl = RateLimiter(client=None, per_visitor=1, global_cap=1)
    rl.record("x")  # no-op
    assert rl.check("x").allowed
