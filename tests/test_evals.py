"""M6: the eval question set is well-formed and the grader / reporter work.
No API calls — the grader is fed synthetic ``AnswerResult``-shaped objects.
"""

from __future__ import annotations

import types

from click.testing import CliRunner

from evals import run as ev


def _result(answer="", sql=None, charts=None, steps=None, model_turns=1):
    return types.SimpleNamespace(
        answer=answer,
        sql=sql or [],
        charts=charts or [],
        trace={"steps": steps or [], "model_turns": model_turns},
    )


# --- question set --------------------------------------------------------- #
def test_questions_yaml_is_well_formed():
    items = ev.load_questions()
    assert 12 <= len(items) <= 15
    seen = set()
    for it in items:
        assert it["id"] not in seen, f"duplicate id {it['id']}"
        seen.add(it["id"])
        assert it["question"].strip()
        assert it["kind"] in {"descriptive", "predictive", "chart"}
        assert isinstance(it.get("expects"), dict) and it["expects"]
        if "numeric" in it["expects"]:
            assert {"value", "tol"} <= it["expects"]["numeric"].keys()


def test_every_kind_is_represented():
    kinds = {it["kind"] for it in ev.load_questions()}
    assert kinds == {"descriptive", "predictive", "chart"}


# --- _numbers ----------------------------------------------------------------- #
def test_numbers_parses_commas_and_decimals():
    got = ev._numbers("about 3,871 buildings, 93.4% compliant, down to -2.5")
    assert 3871.0 in got and 93.4 in got and -2.5 in got


# --- grade() ------------------------------------------------------------------ #
def test_grade_numeric_within_and_outside_tol():
    item = {"expects": {"numeric": {"value": 3871, "tol": 50}}}
    assert all(c.ok for c in ev.grade(item, _result("There are 3,871 buildings.")))
    assert not all(c.ok for c in ev.grade(item, _result("There are 4,200 buildings.")))


def test_grade_contains_and_contains_any():
    item = {"expects": {"answer_contains": ["office"], "answer_contains_any": ["yes", "down"]}}
    assert all(c.ok for c in ev.grade(item, _result("Office use is DOWN since 2018.")))
    assert not all(c.ok for c in ev.grade(item, _result("Retail is flat.")))


def test_grade_tools_and_tables_from_trace():
    steps = [{"kind": "tool", "tool": "run_sql"}, {"kind": "tool", "tool": "make_chart"}]
    item = {
        "expects": {
            "tools_used": ["run_sql", "make_chart"],
            "tables_touched": ["energy_records", "buildings"],
            "chart": True,
        }
    }
    good = _result(
        sql=["SELECT x FROM energy_records JOIN buildings USING (ose_building_id)"],
        charts=["outputs/charts/ab-1.html"],
        steps=steps,
    )
    assert all(c.ok for c in ev.grade(item, good))

    no_chart = _result(sql=good.sql, charts=[], steps=[{"kind": "tool", "tool": "run_sql"}])
    failed = [c.name for c in ev.grade(item, no_chart) if not c.ok]
    assert "chart" in failed and "tool:make_chart" in failed


def test_grade_empty_expects_is_not_a_pass():
    # an item with no checks should never count as passed
    checks = ev.grade({"expects": {}}, _result("anything"))
    assert checks == []


# --- report rendering ------------------------------------------------------- #
def test_render_markdown_smoke():
    items = [
        ev.ItemResult(
            "q01",
            "descriptive",
            "How many?",
            True,
            [ev.Check("numeric", True)],
            model_turns=2,
            tokens=4000,
            cost_usd=0.009,
        ),
        ev.ItemResult(
            "q06",
            "descriptive",
            "Which neighborhood?",
            False,
            [ev.Check("contains:east", False, "missing")],
            model_turns=3,
            tokens=6000,
            cost_usd=0.02,
            answer="It is Ballard.",
            sql=["SELECT neighborhood FROM buildings"],
        ),
    ]
    report = ev.build_report(items, "claude-haiku-4-5", "seattle_energy", 12.3)
    md = ev.render_markdown(report)
    assert "pass rate: 1/2" in md
    assert "## Failures" in md and "### q06" in md
    assert "SELECT neighborhood FROM buildings" in md
    assert report.cost_per_question > 0


# --- CLI dry-run (no API) ------------------------------------------------- #
def test_run_dry_run_lists_questions():
    res = CliRunner().invoke(ev.main, ["--dry-run", "--limit", "3"])
    assert res.exit_code == 0
    assert res.output.count("[") >= 3  # one "[kind]" per listed question
