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
