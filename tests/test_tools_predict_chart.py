"""M5: the predict and make_chart tools, the run-context threading, and the
predict-gated tool list. No network; predict uses a toy model, not the real
artifact (which is gitignored).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline

from agent import tools
from model.seattle_energy.features import CATEGORICAL_FEATURES, FEATURES


@pytest.fixture
def toy_model(monkeypatch):
    rng = np.random.default_rng(0)
    n = 80
    df = pd.DataFrame(
        {
            "building_age": rng.integers(0, 100, n).astype(float),
            "log_gfa_total": rng.normal(5, 0.4, n),
            "parking_share": rng.random(n),
            "number_of_floors": rng.integers(1, 20, n).astype(float),
            "energy_star_score": rng.integers(1, 100, n).astype(float),
            "has_energy_star": rng.integers(0, 2, n).astype(float),
            "primary_property_type": rng.choice(["Office", "Hotel", "Multifamily Housing"], n),
            "council_district": rng.choice(["1", "3", "7"], n),
        }
    )
    for col in CATEGORICAL_FEATURES:
        df[col] = df[col].astype("category")
    y = (df["building_age"] > 50).astype(int)
    pipe = Pipeline(
        [("clf", HistGradientBoostingClassifier(categorical_features="from_dtype", random_state=0))]
    )
    pipe.fit(df[FEATURES], y)
    monkeypatch.setattr(tools, "_load_model", lambda _dk: (pipe, "model-toy"))
    return pipe


# --- predict ------------------------------------------------------------------ #
def test_predict_returns_probabilities(toy_model):
    out = tools.predict(
        [
            {"building_age": 59, "log_gfa_total": 5.3, "primary_property_type": "Office"},
            {"building_age": 3, "log_gfa_total": 4.9, "primary_property_type": "Hotel"},
        ]
    )
    assert out["model_version"] == "model-toy"
    assert out["features_used"] == FEATURES
    assert len(out["predictions"]) == 2
    for p in out["predictions"]:
        assert 0.0 <= p["is_high_emitter_proba"] <= 1.0
        assert p["is_high_emitter"] in (0, 1)


def test_predict_rejects_unknown_feature(toy_model):
    out = tools.predict([{"building_age": 10, "made_up": 1}])
    assert out["error_type"] == "UnknownFeature"
    assert "made_up" in out["message"]


def test_predict_empty_records_is_error():
    assert tools.predict([])["error_type"] == "ValueError"


def test_predict_tolerates_sparse_record(toy_model):
    out = tools.predict([{"primary_property_type": "Multifamily Housing", "council_district": 7}])
    assert len(out["predictions"]) == 1


def test_predict_no_artifact_surfaces_via_run_tool(monkeypatch):
    def _boom(_dk):
        raise tools.ModelUnavailable("no trained model artifact")

    monkeypatch.setattr(tools, "_load_model", _boom)
    payload, is_error = tools.run_tool("predict", {"records": [{"building_age": 1}]})
    assert is_error and json.loads(payload)["error_type"] == "ModelUnavailable"


# --- make_chart ------------------------------------------------------------- #
def test_make_chart_inline_writes_html(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_CHART_DIR", tmp_path / "charts")
    ctx = tools.RunContext()
    out = tools.make_chart(
        {
            "chart_type": "bar",
            "x": "kind",
            "y": "n",
            "title": "t",
            "data": [{"kind": "a", "n": 3}, {"kind": "b", "n": 7}],
        },
        ctx,
    )
    assert out["chart_type"] == "bar" and out["n_rows"] == 2
    assert (tmp_path / "charts" / f"{ctx.run_id}-1.html").exists()
    assert ctx.charts == [out["chart_path"]]


def test_make_chart_last_query(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_CHART_DIR", tmp_path / "charts")
    ctx = tools.RunContext(last_query={"rows": [{"yr": 2020, "v": 1.0}, {"yr": 2021, "v": 2.0}]})
    out = tools.make_chart(
        {"chart_type": "line", "x": "yr", "y": "v", "title": "trend", "data": "last_query"}, ctx
    )
    assert out["n_rows"] == 2


def test_make_chart_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_CHART_DIR", tmp_path / "charts")
    ctx = tools.RunContext()
    assert (
        tools.make_chart(
            {"chart_type": "pie", "x": "a", "y": "b", "title": "", "data": [{"a": 1, "b": 2}]}, ctx
        )["error_type"]
        == "ValueError"
    )
    assert (
        tools.make_chart(
            {"chart_type": "bar", "x": "nope", "y": "b", "title": "", "data": [{"a": 1, "b": 2}]},
            ctx,
        )["error_type"]
        == "UnknownColumn"
    )
    assert (
        tools.make_chart(
            {"chart_type": "bar", "x": "a", "y": "b", "title": "", "data": "last_query"}, ctx
        )["error_type"]
        == "NoData"
    )


# --- context threading + tool gating ------------------------------------------ #
def test_run_tool_threads_last_query_into_make_chart(tmp_path, monkeypatch, loaded_db):
    monkeypatch.setattr(tools, "_CHART_DIR", tmp_path / "charts")
    ctx = tools.RunContext()
    tools.run_tool("run_sql", {"sql": "SELECT data_year, site_eui FROM energy_records"}, ctx)
    assert ctx.last_query and ctx.last_query["row_count"] == 5
    spec = {
        "chart_type": "line",
        "x": "data_year",
        "y": "site_eui",
        "title": "t",
        "data": "last_query",
    }
    payload, is_error = tools.run_tool("make_chart", spec, ctx)
    assert not is_error and json.loads(payload)["n_rows"] == 5


def test_tool_schemas_gates_predict_on_model_module():
    names = [t["name"] for t in tools.tool_schemas("seattle_energy")]
    assert names.count("predict") == 1 and "make_chart" in names
    assert "predict" not in [t["name"] for t in tools.tool_schemas("no_such_source")]


@pytest.fixture
def loaded_db(sqlite_env):
    from sqlalchemy import text

    from db import migrate
    from db.engine import rw_engine

    migrate.main()
    with rw_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO buildings (ose_building_id, primary_property_type) VALUES ('b1', 'X')"
            )
        )
        for yr, eui in enumerate([10, 20, 30, 40, 50], start=2019):
            conn.execute(
                text(
                    "INSERT INTO energy_records (ose_building_id, data_year, site_eui) "
                    "VALUES ('b1', :y, :e)"
                ),
                {"y": yr, "e": eui},
            )
    return sqlite_env
