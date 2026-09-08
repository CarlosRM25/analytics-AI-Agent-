"""Agent tools: schemas (strict) + handlers (architecture doc §4.5).

Five tools: ``list_datasets``, ``describe_schema``, ``run_sql`` (M4), plus
``predict`` and ``make_chart`` (M5). ``predict`` is only offered when the active
source has a ``model_module``.

``run_sql`` is the security-critical one. Defence in depth on top of
``ro_engine()`` (already ``PRAGMA query_only``): ``sqlparse`` allowlist — one
statement, must start ``SELECT`` / ``WITH``, no DDL / non-SELECT DML / PRAGMA /
ATTACH anywhere; wrap in ``SELECT * FROM (...) LIMIT n+1`` so an unbounded query
can't stream a whole table; a per-connection statement timeout; and on failure a
**structured** ``{error_type, message}`` so the model can fix its own SQL.

``make_chart`` takes a constrained spec (not raw Plotly JSON) and can chart the
rows from the most recent ``run_sql`` via ``data: "last_query"`` — the loop
threads a :class:`RunContext` through :func:`run_tool` to carry that.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import sqlparse
from sqlparse.tokens import DDL, DML, Keyword

from agent import catalog
from config import get_settings
from db.engine import ro_engine
from sources import base as sources_base

_ROOT = Path(__file__).resolve().parents[1]
_CHART_DIR = _ROOT / "outputs" / "charts"

# Keyword-classified tokens to reject anywhere in the statement. DDL (CREATE /
# DROP / ALTER) and non-SELECT DML (INSERT / UPDATE / DELETE) are caught by their
# token type; these are the ones sqlparse tags as a plain Keyword.
_FORBIDDEN = {"ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "GRANT", "REVOKE"}
_CHART_TYPES = {"bar", "line", "scatter", "box"}


class GuardrailError(Exception):
    """A query rejected before it reached the database."""


class ModelUnavailable(Exception):
    """No trained model artifact for the ``predict`` tool."""


@dataclass
class RunContext:
    """Per-question state the tools share (the last query's rows, a run id for
    naming chart files)."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    last_query: dict | None = None
    chart_seq: int = 0
    charts: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# tool schemas
# --------------------------------------------------------------------------- #
_LIST_DATASETS = {
    "name": "list_datasets",
    "description": "List the datasets in the database, each with a one-line description.",
    "strict": True,
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}
_DESCRIBE_SCHEMA = {
    "name": "describe_schema",
    "description": (
        "Return tables, columns, types, primary/foreign keys, enum values, row counts, and "
        "curated column notes. Call this before writing SQL if you are unsure of a column name."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "dataset_key": {
                "type": "string",
                "description": "Restrict to one dataset (e.g. 'seattle_energy'). Omit for all.",
            }
        },
        "required": [],
        "additionalProperties": False,
    },
}
_RUN_SQL = {
    "name": "run_sql",
    "description": (
        "Run one read-only SQLite SELECT (or WITH ... SELECT) and return rows. "
        "Results are capped; list columns explicitly rather than SELECT *. On error you get "
        "a structured message — read it and correct your query."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "A single SELECT / WITH query."}},
        "required": ["sql"],
        "additionalProperties": False,
    },
}
# predict / make_chart take flexible shapes (an open feature dict, an
# either-string-or-array field), which the API's strict mode disallows. Their
# handlers validate every field instead.
_PREDICT = {
    "name": "predict",
    "description": (
        "Ask the trained model what it expects for one or more hypothetical buildings. "
        "Target: is_high_emitter (GHG intensity above the median for that property type & year). "
        "Use this for 'what would the model expect / predict' questions, NOT for 'what does the "
        "data say' (use run_sql for those). Unknown feature keys are rejected; omitted numeric "
        "features are treated as unknown."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "records": {
                "type": "array",
                "description": "One feature dict per hypothetical building.",
                "items": {
                    "type": "object",
                    "properties": {
                        "building_age": {"type": "number", "description": "years since built"},
                        "log_gfa_total": {"type": "number", "description": "log10(gross sq ft)"},
                        "parking_share": {"type": "number", "description": "0-1"},
                        "number_of_floors": {"type": "number"},
                        "energy_star_score": {"type": "number", "description": "1-100"},
                        "has_energy_star": {"type": "integer", "enum": [0, 1]},
                        "primary_property_type": {
                            "type": "string",
                            "description": "e.g. 'Office', 'Hotel' — see describe_schema",
                        },
                        "council_district": {"type": "string", "description": "'1'-'7'"},
                    },
                    "additionalProperties": False,
                },
            }
        },
        "required": ["records"],
    },
}
_MAKE_CHART = {
    "name": "make_chart",
    "description": (
        "Render a chart when a comparison or trend IS the answer. Constrained spec, not Plotly "
        "JSON. data='last_query' charts the rows from your most recent run_sql; or pass rows "
        "inline. Writes an HTML file and returns its path."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "chart_type": {"type": "string", "enum": sorted(_CHART_TYPES)},
            "x": {"type": "string", "description": "column for the x axis"},
            "y": {"type": "string", "description": "column for the y axis"},
            "series": {"type": "string", "description": "optional column to colour/split by"},
            "title": {"type": "string"},
            "data": {
                "description": "'last_query' or an array of row objects",
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "object"}}],
            },
        },
        "required": ["chart_type", "x", "y", "title", "data"],
        "additionalProperties": False,
    },
}


def tool_schemas(source_key: str = "seattle_energy") -> list[dict]:
    """The tool list for a source. ``predict`` is included only when the source
    has a ``model_module`` (architecture doc §4.5)."""
    schemas = [_LIST_DATASETS, _DESCRIBE_SCHEMA, _RUN_SQL, _MAKE_CHART]
    try:
        has_model = bool(sources_base.load(source_key).model_module)
    except Exception:  # noqa: BLE001 - a missing/broken source just means no predict
        has_model = False
    if has_model:
        schemas.insert(3, _PREDICT)
    return schemas


# --------------------------------------------------------------------------- #
# run_sql
# --------------------------------------------------------------------------- #
def _validate_select(sql: str) -> None:
    statements = [s for s in sqlparse.parse(sql) if str(s).strip()]
    if len(statements) != 1:
        raise GuardrailError("provide exactly one SQL statement")
    stmt = statements[0]
    first = stmt.token_first(skip_cm=True)
    if first is None or first.normalized not in {"SELECT", "WITH"}:
        raise GuardrailError("query must start with SELECT or WITH")
    for tok in stmt.flatten():
        val = tok.value.upper()
        if tok.ttype is DDL or (tok.ttype is DML and val != "SELECT"):
            raise GuardrailError(f"forbidden statement: {tok.value}")
        if tok.ttype in Keyword and val in _FORBIDDEN:
            raise GuardrailError(f"forbidden keyword: {tok.value}")


def _statement_timeout(conn, ms: int) -> None:
    deadline = time.monotonic() + ms / 1000
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)


def run_sql(sql: str) -> dict:
    settings = get_settings()
    max_rows = settings.MAX_SQL_ROWS
    try:
        _validate_select(sql)
    except GuardrailError as exc:
        return {"error_type": "GuardrailError", "message": str(exc)}

    wrapped = f"SELECT * FROM (\n{sql.rstrip().rstrip(';')}\n) LIMIT {max_rows + 1}"
    pooled = ro_engine().raw_connection()
    try:
        conn = pooled.driver_connection
        _statement_timeout(conn, settings.SQL_TIMEOUT_MS)
        cursor = conn.execute(wrapped)
        fetched = cursor.fetchmany(max_rows + 1)
        columns = [d[0] for d in cursor.description]
        conn.set_progress_handler(None, 0)
    except Exception as exc:  # noqa: BLE001 - surface any DB error to the model, structured
        name = type(exc).__name__
        msg = str(exc)
        if "interrupted" in msg.lower():
            name = "TimeoutError"
            msg = f"query exceeded {settings.SQL_TIMEOUT_MS} ms and was cancelled"
        return {"error_type": name, "message": msg, "sql_executed": wrapped}
    finally:
        pooled.close()

    truncated = len(fetched) > max_rows
    rows = [dict(zip(columns, r, strict=False)) for r in fetched[:max_rows]]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "sql_executed": wrapped,
    }


# --------------------------------------------------------------------------- #
# list_datasets / describe_schema
# --------------------------------------------------------------------------- #
def list_datasets() -> dict:
    with ro_engine().connect() as conn:
        cur = conn.exec_driver_sql(
            "SELECT dataset_key, title, description, row_count, first_year, last_year"
            " FROM datasets ORDER BY dataset_key"
        )
        cols = list(cur.keys())
        rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
    return {"datasets": rows}


def describe_schema(dataset_key: str | None = None) -> dict:
    cat = catalog.load(dataset_key or "seattle_energy")
    tables_meta = cat.get("tables", {})
    out: list[dict] = []
    with ro_engine().connect() as conn:
        for table, spec in tables_meta.items():
            info = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            (count,) = conn.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").fetchone()
            col_docs = spec.get("columns") or {}
            columns = []
            for _cid, cname, ctype, notnull, _dflt, pk in info:
                meta = col_docs.get(cname) or {}
                columns.append(
                    {
                        "name": cname,
                        "type": ctype or meta.get("type"),
                        "nullable": not notnull,
                        "pk": bool(pk),
                        "fk": meta.get("fk"),
                        "enum": meta.get("enum"),
                        "note": meta.get("doc"),
                    }
                )
            out.append(
                {
                    "table": table,
                    "description": spec.get("description", "").strip(),
                    "row_count": count,
                    "columns": columns,
                }
            )
    return {"dataset_key": dataset_key or "seattle_energy", "tables": out}


# --------------------------------------------------------------------------- #
# predict
# --------------------------------------------------------------------------- #
def _load_model(dataset_key: str):
    spec = sources_base.load(dataset_key)
    if not spec.model_module:
        raise ModelUnavailable(f"dataset {dataset_key!r} has no model")
    art_dir = _ROOT / spec.model_module.replace(".", "/") / "artifacts"
    joblibs = sorted(art_dir.glob("model-*.joblib"))
    if not joblibs:
        raise ModelUnavailable(
            "no trained model artifact — run `python -m model.seattle_energy.train`"
        )
    import joblib

    return joblib.load(joblibs[-1]), joblibs[-1].stem


def predict(records: list[dict], dataset_key: str = "seattle_energy") -> dict:
    import numpy as np
    import pandas as pd

    from model.seattle_energy.features import CATEGORICAL_FEATURES, FEATURES, NUMERIC_FEATURES

    if not isinstance(records, list) or not records:
        return {"error_type": "ValueError", "message": "records must be a non-empty list of dicts"}
    unknown = sorted({k for r in records for k in r} - set(FEATURES))
    if unknown:
        return {
            "error_type": "UnknownFeature",
            "message": f"not model features: {unknown}. valid features: {FEATURES}",
        }

    model, version = _load_model(dataset_key)
    frame = pd.DataFrame(records)
    for col in FEATURES:
        if col not in frame.columns:
            frame[col] = np.nan
    frame = frame[FEATURES]
    for col in NUMERIC_FEATURES:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    for col in CATEGORICAL_FEATURES:
        series = frame[col]
        if col == "council_district":
            series = pd.to_numeric(series, errors="coerce").astype("Int64")
        frame[col] = series.astype("string").astype("category")

    proba = model.predict_proba(frame)[:, 1]
    return {
        "model_version": version,
        "target": "is_high_emitter",
        "target_meaning": "GHG intensity above the median for this property type in a given year",
        "features_used": FEATURES,
        "predictions": [
            {"is_high_emitter_proba": round(float(p), 4), "is_high_emitter": int(p >= 0.5)}
            for p in proba
        ],
        "caveat": "generalises to a genuinely new building at ROC-AUC ~0.76 (see model_card.md)",
    }


# --------------------------------------------------------------------------- #
# make_chart
# --------------------------------------------------------------------------- #
def make_chart(spec: dict, ctx: RunContext) -> dict:
    import pandas as pd
    import plotly.express as px

    ctype = spec.get("chart_type")
    if ctype not in _CHART_TYPES:
        return {
            "error_type": "ValueError",
            "message": f"chart_type must be one of {sorted(_CHART_TYPES)}",
        }

    data = spec.get("data", "last_query")
    if data == "last_query":
        if not ctx.last_query or not ctx.last_query.get("rows"):
            return {
                "error_type": "NoData",
                "message": "no previous run_sql result — run a query first, or pass data inline",
            }
        rows = ctx.last_query["rows"]
    elif isinstance(data, list):
        rows = data
    else:
        return {
            "error_type": "ValueError",
            "message": "data must be 'last_query' or a list of row objects",
        }

    df = pd.DataFrame(rows)
    x, y, series, title = spec.get("x"), spec.get("y"), spec.get("series"), spec.get("title", "")
    for col in (x, y, series):
        if col and col not in df.columns:
            return {
                "error_type": "UnknownColumn",
                "message": f"'{col}' is not in the data columns {list(df.columns)}",
            }

    builder = {"bar": px.bar, "line": px.line, "scatter": px.scatter, "box": px.box}[ctype]
    kwargs = {"x": x, "y": y, "title": title}
    if series:
        kwargs["color"] = series
    fig = builder(df, **kwargs)
    fig.update_layout(template="plotly_white", margin={"t": 60, "r": 20, "b": 50, "l": 60})

    ctx.chart_seq += 1
    html = fig.to_html(include_plotlyjs="cdn", full_html=True)

    if get_settings().DEPLOY_MODE == "deployed":
        # No writable disk on Cloud Run — hand the chart back inline. A
        # data:text/html URI renders in an <iframe src=…> on the frontend.
        b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
        uri = f"data:text/html;base64,{b64}"
        ctx.charts.append(uri)
        return {"chart_data_uri": uri, "chart_type": ctype, "n_rows": len(df)}

    _CHART_DIR.mkdir(parents=True, exist_ok=True)
    path = _CHART_DIR / f"{ctx.run_id}-{ctx.chart_seq}.html"
    path.write_text(html, encoding="utf-8")
    try:
        shown = str(path.relative_to(_ROOT)).replace("\\", "/")
    except ValueError:
        shown = str(path)
    ctx.charts.append(shown)
    return {"chart_path": shown, "chart_type": ctype, "n_rows": len(df)}


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
def run_tool(name: str, tool_input: dict, ctx: RunContext | None = None) -> tuple[str, bool]:
    """Execute a tool call. Returns ``(json_string, is_error)`` for a ``tool_result``."""
    ctx = ctx or RunContext()
    try:
        if name == "list_datasets":
            result = list_datasets()
        elif name == "describe_schema":
            result = describe_schema(tool_input.get("dataset_key"))
        elif name == "run_sql":
            result = run_sql(tool_input["sql"])
            if "rows" in result:
                ctx.last_query = result
        elif name == "predict":
            result = predict(tool_input.get("records"))
        elif name == "make_chart":
            result = make_chart(tool_input, ctx)
        else:
            return json.dumps({"error_type": "UnknownTool", "message": name}), True
    except Exception as exc:  # noqa: BLE001 - never crash the loop on a tool bug
        return json.dumps({"error_type": type(exc).__name__, "message": str(exc)}), True

    is_error = "error_type" in result
    return json.dumps(result, default=str), is_error
