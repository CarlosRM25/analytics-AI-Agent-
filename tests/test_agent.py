"""M4: tools, guardrails, prompt assembly, trace math, and the loop with a fake
client. No network, no real API calls.
"""

from __future__ import annotations

import json
import types

import pytest

from agent import loop, prompts, tools, trace


# --- run_sql guardrails ---------------------------------------------------- #
@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE buildings",
        "DELETE FROM buildings",
        "UPDATE buildings SET x = 1",
        "SELECT 1; SELECT 2",
        "SELECT 1; PRAGMA table_info(buildings)",
        "INSERT INTO datasets VALUES (1)",
    ],
)
def test_run_sql_rejects_non_select(sql, loaded_db):
    out = tools.run_sql(sql)
    assert out["error_type"] == "GuardrailError"
    assert "rows" not in out


def test_run_sql_allows_select_and_cte(loaded_db):
    rows = tools.run_sql("SELECT data_year, site_eui FROM energy_records ORDER BY data_year")
    assert rows["row_count"] == 5
    assert rows["columns"] == ["data_year", "site_eui"]

    cte = tools.run_sql("WITH x AS (SELECT site_eui FROM energy_records) SELECT COUNT(*) c FROM x")
    assert cte["rows"] == [{"c": 5}]


def test_run_sql_caps_rows_and_flags_truncation(loaded_db, monkeypatch):
    monkeypatch.setattr(tools.get_settings(), "MAX_SQL_ROWS", 2)
    out = tools.run_sql("SELECT data_year FROM energy_records")
    assert out["row_count"] == 2
    assert out["truncated"] is True


def test_run_sql_structured_error_on_bad_column(loaded_db):
    out = tools.run_sql("SELECT nope FROM energy_records")
    assert out["error_type"] == "OperationalError"
    assert "nope" in out["message"]


def test_run_tool_unknown_is_error():
    payload, is_error = tools.run_tool("no_such_tool", {})
    assert is_error and json.loads(payload)["error_type"] == "UnknownTool"


def test_describe_schema_merges_catalog_and_live(loaded_db):
    payload, is_error = tools.run_tool("describe_schema", {"dataset_key": "seattle_energy"})
    data = json.loads(payload)
    assert not is_error
    tables = {t["table"]: t for t in data["tables"]}
    assert {"buildings", "energy_records"} <= set(tables)
    assert tables["energy_records"]["row_count"] == 5
    cs = next(c for c in tables["energy_records"]["columns"] if c["name"] == "compliance_status")
    assert cs["enum"] == ["Compliant", "Not Compliant"]


# --- prompt ------------------------------------------------------------------ #
def test_system_prompt_has_schema_and_cache_breakpoint():
    blocks = prompts.build_system_prompt("seattle_energy")
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    body = blocks[0]["text"]
    assert "energy_records" in body and "primary_property_type" in body
    assert "Compliant" in body  # enum surfaced


# --- M9: a second source drops in with no agent change --------------------- #
def test_tool_schemas_and_prompt_for_seattle_crime():
    names = [t["name"] for t in tools.tool_schemas("seattle_crime")]
    assert "run_sql" in names and "make_chart" in names
    assert "predict" not in names  # seattle_crime has no model_module

    body = prompts.build_system_prompt("seattle_crime")[0]["text"]
    assert "crime_incidents" in body and "NIBRS" in body
    assert "VIOLENT CRIME" in body  # an enum from the crime catalog


def test_describe_schema_and_run_sql_against_crime(loaded_crime_db):
    payload, is_error = tools.run_tool("describe_schema", {"dataset_key": "seattle_crime"})
    data = json.loads(payload)
    assert not is_error
    (tbl,) = data["tables"]
    assert tbl["table"] == "crime_incidents" and tbl["row_count"] == 3
    cat_col = next(c for c in tbl["columns"] if c["name"] == "offense_category")
    assert "VIOLENT CRIME" in cat_col["enum"]

    out = tools.run_sql(
        "SELECT precinct, COUNT(*) n FROM crime_incidents GROUP BY 1 ORDER BY n DESC"
    )
    assert out["rows"][0] == {"precinct": "North", "n": 2}


# --- trace ----------------------------------------------------------------- #
def test_trace_cost_and_summary():
    tr = trace.Trace(question="q", model="claude-haiku-4-5")
    usage = types.SimpleNamespace(
        input_tokens=1000,
        output_tokens=500,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    tr.record_response(types.SimpleNamespace(stop_reason="end_turn", usage=usage))
    # haiku: $1/MTok in, $5/MTok out -> 1000*1 + 500*5 = 3500 / 1e6
    assert tr.cost_usd == pytest.approx(0.0035, rel=1e-6)
    assert "1 turns" in tr.summary() and "$0.00" in tr.summary()


# --- the loop, with a fake Anthropic client ------------------------------- #
def _msg(stop_reason, content):
    usage = types.SimpleNamespace(
        input_tokens=200,
        output_tokens=50,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return types.SimpleNamespace(stop_reason=stop_reason, content=content, usage=usage)


class _FakeMessages:
    def __init__(self, scripted):
        self._scripted = list(scripted)

    def create(self, **_kw):
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted):
        self.messages = _FakeMessages(scripted)


def test_loop_runs_tool_then_answers(loaded_db, monkeypatch):
    tool_use = types.SimpleNamespace(
        type="tool_use",
        name="run_sql",
        id="t1",
        input={"sql": "SELECT COUNT(*) c FROM energy_records"},
    )
    final = types.SimpleNamespace(type="text", text="There are 5 rows. SQL: SELECT COUNT(*) ...")
    scripted = [_msg("tool_use", [tool_use]), _msg("end_turn", [final])]

    monkeypatch.setattr(loop.anthropic, "Anthropic", lambda **_kw: _FakeClient(scripted))
    monkeypatch.setattr(loop.get_settings(), "ANTHROPIC_API_KEY", "test-key")

    result = loop.answer_question("how many rows?")
    assert result.stopped == "end_turn"
    assert "5 rows" in result.answer
    assert result.sql == ["SELECT COUNT(*) c FROM energy_records"]
    assert any(s["kind"] == "tool" and not s["is_error"] for s in result.steps)


def test_loop_stops_at_max_iters(loaded_db, monkeypatch):
    loop_tool = types.SimpleNamespace(
        type="tool_use", name="run_sql", id="t", input={"sql": "SELECT 1"}
    )
    always_tool = [_msg("tool_use", [loop_tool]) for _ in range(20)]
    monkeypatch.setattr(loop.anthropic, "Anthropic", lambda **_kw: _FakeClient(always_tool))
    monkeypatch.setattr(loop.get_settings(), "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(loop.get_settings(), "MAX_AGENT_ITERS", 3)

    result = loop.answer_question("loop forever")
    assert result.stopped == "max_iters"
    assert result.trace["model_turns"] == 3
