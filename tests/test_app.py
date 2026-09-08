"""M6: the CLI and the Flask API, driven by a fake Anthropic client. No network."""

from __future__ import annotations

import json
import types

import pytest
from click.testing import CliRunner

from agent import loop
from app import api as api_mod
from app import cli as cli_mod


def _usage():
    return types.SimpleNamespace(
        input_tokens=300,
        output_tokens=60,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def _msg(stop_reason, content):
    return types.SimpleNamespace(stop_reason=stop_reason, content=content, usage=_usage())


class _FakeMessages:
    def __init__(self, scripted):
        self._scripted = list(scripted)

    def create(self, **_kw):
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted):
        self.messages = _FakeMessages(scripted)


@pytest.fixture(autouse=True)
def _sane_demo_settings(monkeypatch):
    """Neutralise the §10 demo knobs via the environment (survives a
    ``get_settings`` cache-clear) so a stray value in the developer's .env can't
    turn on Turnstile / CORS mid-test. Individual tests re-enable what they need."""
    monkeypatch.setenv("TURNSTILE_SECRET", "")
    monkeypatch.setenv("CORS_ALLOWED_ORIGIN", "")
    monkeypatch.setenv("DEMO_ENABLED", "true")
    from config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def scripted_agent(loaded_db, monkeypatch):
    """A fake client: run_sql once, then answer. Returns nothing — just patches."""
    tool_use = types.SimpleNamespace(
        type="tool_use",
        name="run_sql",
        id="t1",
        input={"sql": "SELECT COUNT(*) c FROM energy_records"},
    )
    final = types.SimpleNamespace(
        type="text", text="There are 5 records. SQL: SELECT COUNT(*) c FROM energy_records"
    )
    scripted = [_msg("tool_use", [tool_use]), _msg("end_turn", [final])]
    monkeypatch.setattr(loop.anthropic, "Anthropic", lambda **_kw: _FakeClient(scripted))
    monkeypatch.setattr(loop.get_settings(), "ANTHROPIC_API_KEY", "test-key")


# --- CLI ----------------------------------------------------------------- #
def test_cli_ask_prints_answer_sql_and_trace(scripted_agent):
    result = CliRunner().invoke(cli_mod.analyst, ["ask", "how many records?"])
    assert result.exit_code == 0
    assert "5 records" in result.output
    assert "SELECT COUNT(*) c FROM energy_records" in result.output
    assert "turns" in result.output  # trace summary line


def test_cli_ask_json_flag(scripted_agent):
    result = CliRunner().invoke(cli_mod.analyst, ["ask", "--json", "how many?"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["stopped"] == "end_turn"
    assert payload["sql"] == ["SELECT COUNT(*) c FROM energy_records"]
    assert "usage" not in payload or "cost_usd" in payload["trace"]


def test_cli_ask_requires_question():
    result = CliRunner().invoke(cli_mod.analyst, ["ask", "   "])
    assert result.exit_code != 0


# --- Flask ------------------------------------------------------------------ #
@pytest.fixture
def client(scripted_agent):
    return api_mod.create_app().test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_ask_ok(client):
    resp = client.post("/ask", json={"question": "how many records?"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert "5 records" in body["answer"]
    assert body["stopped"] == "end_turn"
    assert {"tool": "run_sql", "sql": "SELECT COUNT(*) c FROM energy_records"} in body["steps"]
    assert body["usage"]["tokens"] > 0
    assert body["usage"]["cost_usd"] >= 0


def test_ask_missing_question_is_400(client):
    resp = client.post("/ask", json={})
    assert resp.status_code == 400
    assert "question" in resp.get_json()["error"]


def test_ask_respects_demo_disabled(client, monkeypatch):
    monkeypatch.setattr(api_mod.get_settings(), "DEMO_ENABLED", False)
    resp = client.post("/ask", json={"question": "anything"})
    assert resp.status_code == 200
    assert resp.get_json() == {"disabled": True}


def test_ask_chart_step_gets_url(loaded_db, monkeypatch):
    """A make_chart tool step is surfaced with its chart_url."""
    q = types.SimpleNamespace(
        type="tool_use",
        name="run_sql",
        id="t1",
        input={"sql": "SELECT data_year, site_eui FROM energy_records"},
    )
    c = types.SimpleNamespace(
        type="tool_use",
        name="make_chart",
        id="t2",
        input={
            "chart_type": "line",
            "x": "data_year",
            "y": "site_eui",
            "title": "trend",
            "data": "last_query",
        },
    )
    final = types.SimpleNamespace(type="text", text="Here is the trend.")
    scripted = [_msg("tool_use", [q]), _msg("tool_use", [c]), _msg("end_turn", [final])]
    monkeypatch.setattr(loop.anthropic, "Anthropic", lambda **_kw: _FakeClient(scripted))
    monkeypatch.setattr(loop.get_settings(), "ANTHROPIC_API_KEY", "test-key")

    resp = api_mod.create_app().test_client().post("/ask", json={"question": "show me the trend"})
    body = resp.get_json()
    chart_steps = [s for s in body["steps"] if s["tool"] == "make_chart"]
    assert len(chart_steps) == 1 and chart_steps[0]["chart_url"].endswith(".html")


# --- M8: demo middleware (cache / rate limit / budget / CORS) ------------- #
class _LoopingClient:
    """Answers every call with a fixed end_turn text — no scripted length limit."""

    def __init__(self, text="Cached-friendly answer."):
        self._text = text

    class _M:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **_kw):
            blk = types.SimpleNamespace(type="text", text=self._outer._text)
            return _msg("end_turn", [blk])

    @property
    def messages(self):
        return self._M(self)


@pytest.fixture
def looping_agent(loaded_db, monkeypatch):
    monkeypatch.setattr(loop.anthropic, "Anthropic", lambda **_kw: _LoopingClient())
    monkeypatch.setattr(loop.get_settings(), "ANTHROPIC_API_KEY", "test-key")


def test_cache_hit_is_served_and_flagged(looping_agent, fake_redis, monkeypatch):
    c = api_mod.create_app().test_client()
    first = c.post("/ask", json={"question": "how many buildings?"}).get_json()
    assert "cached" not in first
    second = c.post("/ask", json={"question": "  How   many buildings? "}).get_json()
    assert second["cached"] is True
    assert second["answer"] == first["answer"]


def test_rate_limit_kicks_in_after_the_cap(looping_agent, fake_redis, monkeypatch):
    monkeypatch.setattr(api_mod.get_settings(), "RATE_LIMIT_PER_VISITOR_PER_DAY", 2)
    c = api_mod.create_app().test_client()
    assert c.post("/ask", json={"question": "q one"}).status_code == 200
    assert c.post("/ask", json={"question": "q two"}).status_code == 200
    blocked = c.post("/ask", json={"question": "q three"})
    assert blocked.status_code == 429
    assert blocked.get_json() == {"limited": True, "scope": "visitor"}


def test_cache_hits_do_not_consume_rate_limit(looping_agent, fake_redis, monkeypatch):
    monkeypatch.setattr(api_mod.get_settings(), "RATE_LIMIT_PER_VISITOR_PER_DAY", 1)
    c = api_mod.create_app().test_client()
    assert c.post("/ask", json={"question": "same q"}).status_code == 200
    again = c.post("/ask", json={"question": "same q"})  # cached -> free
    assert again.status_code == 200 and again.get_json()["cached"] is True


def test_monthly_budget_cutoff(looping_agent, fake_redis, monkeypatch):
    monkeypatch.setattr(api_mod.get_settings(), "MONTHLY_BUDGET_USD", 0.001)
    from app.budget import month_budget

    month_budget().add(0.01)  # already over
    resp = api_mod.create_app().test_client().post("/ask", json={"question": "anything"})
    assert resp.status_code == 200 and resp.get_json() == {"disabled": True}


def test_cors_header_and_preflight(scripted_agent, monkeypatch):
    monkeypatch.setattr(api_mod.get_settings(), "CORS_ALLOWED_ORIGIN", "https://carlos.dev")
    c = api_mod.create_app().test_client()
    pre = c.open("/ask", method="OPTIONS")
    assert pre.status_code == 204
    assert pre.headers["Access-Control-Allow-Origin"] == "https://carlos.dev"
    got = c.post("/ask", json={"question": "how many records?"})
    assert got.headers["Access-Control-Allow-Origin"] == "https://carlos.dev"
