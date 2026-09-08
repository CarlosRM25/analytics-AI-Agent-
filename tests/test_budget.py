"""M8: the monthly spend cutoff."""

from __future__ import annotations

from app.budget import MonthBudget


def test_accumulates_and_trips(fake_redis):
    b = MonthBudget(client=fake_redis, limit_usd=0.10)
    assert b.spent() == 0.0 and not b.over_budget()
    b.add(0.04)
    b.add(0.04)
    assert round(b.spent(), 2) == 0.08 and not b.over_budget()
    b.add(0.03)
    assert b.over_budget()


def test_add_sets_ttl(fake_redis):
    b = MonthBudget(client=fake_redis, limit_usd=5)
    b.add(0.01)
    assert fake_redis.ttl(b._key()) > 0


def test_zero_cost_is_ignored(fake_redis):
    b = MonthBudget(client=fake_redis, limit_usd=1)
    b.add(0)
    b.add(0.0)
    assert b.spent() == 0.0


def test_no_client_never_trips():
    b = MonthBudget(client=None, limit_usd=1.0)
    b.add(999)  # untracked without Redis
    assert b.spent() == 0.0 and not b.over_budget()
