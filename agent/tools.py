"""Agent tools: schemas (strict) + handlers (architecture doc §4.5).

M4 registers three: ``list_datasets``, ``describe_schema``, ``run_sql``.
``predict`` and ``make_chart`` arrive at M5.

``run_sql`` is the security-critical one. Defence in depth on top of
``ro_engine()`` (which is already ``PRAGMA query_only``):

* parse with ``sqlparse`` — exactly one statement, must start ``SELECT`` / ``WITH``,
  no DDL/DML/PRAGMA/ATTACH tokens anywhere;
* wrap in ``SELECT * FROM (...) LIMIT n+1`` so an unbounded query can't stream the
  whole table, and ``truncated`` is reported honestly;
* a per-connection statement timeout via SQLite's progress handler;
* on any failure, return a **structured** ``{error_type, message}`` so the model
  can read it and fix its own SQL — the self-correction loop depends on this.
"""

from __future__ import annotations

import json
import time

import sqlparse
from sqlparse.tokens import DDL, DML, Keyword

from agent import catalog
from config import get_settings
from db.engine import ro_engine

# Keyword-classified tokens to reject anywhere in the statement. DDL (CREATE /
# DROP / ALTER) and non-SELECT DML (INSERT / UPDATE / DELETE) are caught by their
# token type; these are the ones sqlparse tags as a plain Keyword.
_FORBIDDEN = {"ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "GRANT", "REVOKE"}

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "list_datasets",
        "description": "List the datasets in the database, each with a one-line description.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
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
    },
    {
        "name": "run_sql",
        "description": (
            "Run one read-only SQLite SELECT (or WITH ... SELECT) and return rows. "
            "Results are capped; list columns explicitly rather than SELECT *. On error you get "
            "a structured message — read it and correct your query."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT / WITH query."}
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
]


class GuardrailError(Exception):
    """A query rejected before it reached the database."""


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
            for cid, cname, ctype, notnull, _dflt, pk in info:  # noqa: B007
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
# dispatch
# --------------------------------------------------------------------------- #
_HANDLERS = {
    "list_datasets": lambda _inp: list_datasets(),
    "describe_schema": lambda inp: describe_schema(inp.get("dataset_key")),
    "run_sql": lambda inp: run_sql(inp["sql"]),
}


def run_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """Execute a tool call. Returns ``(json_string, is_error)`` for a ``tool_result``."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error_type": "UnknownTool", "message": name}), True
    try:
        result = handler(tool_input)
    except Exception as exc:  # noqa: BLE001 - never crash the loop on a tool bug
        return json.dumps({"error_type": type(exc).__name__, "message": str(exc)}), True
    is_error = "error_type" in result
    return json.dumps(result, default=str), is_error
