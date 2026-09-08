"""Generic Socrata SODA ingestion, driven by a SourceSpec (architecture doc 4.2).

    python -m ingest.run --source seattle_energy [--full-refresh] [--since 2015] [--limit N]
    python -m ingest.run --source seattle_crime  --since 2024        (a slice of a 1.5M-row set)

Steps: resolve the spec from ``sources.REGISTRY``; page the SODA endpoint with
``$limit``/``$offset``; map each record onto one dict per target table via
``field_map`` (string -> typed by ``FieldMap.dtype``); upsert the slowly-changing
dimension (``spec.static_table``, if any) then the fact table on the spec's
upsert keys; refresh the ``datasets`` row; write one ``ingestion_runs`` audit row.

Everything source-specific lives on the ``SourceSpec`` (``time_column``,
``static_table``, ``year_column``, ``null_tokens``, ...). ``--full-refresh``
deletes existing rows first; ``--since YEAR`` filters on ``spec.time_column``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests
import yaml
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from config import get_settings
from db.engine import db_path, rw_engine
from sources.base import SourceSpec, load

_SODA_TIMEOUT = 60
_CHUNK = 500


# --------------------------------------------------------------------------- #
# value coercion
# --------------------------------------------------------------------------- #
def _coerce(value: object, dtype: str, null_tokens: tuple[str, ...] = ()) -> object | None:
    """Socrata sends every field as a JSON string (except real bools). Map to a
    Python value by ``dtype``. ``""`` and ``"NA"`` (plus any ``null_tokens`` the
    source declares, e.g. ``"-"`` / ``"REDACTED"``) -> None. ``dtype="year"``
    takes the leading 4 digits of a date/datetime string."""
    if value is None:
        return None
    v = value.strip() if isinstance(value, str) else value
    if isinstance(v, str) and (v == "" or v.upper() == "NA" or v in null_tokens):
        return None
    try:
        if dtype == "str":
            return str(v)
        if dtype == "int":
            return int(round(float(v)))
        if dtype == "float":
            return float(v)
        if dtype == "year":
            return int(str(v)[:4])
        if dtype == "bool":
            if isinstance(v, bool):
                return int(v)
            return int(str(v).strip().lower() in {"1", "true", "yes", "t"})
    except (TypeError, ValueError):
        return None
    raise ValueError(f"unknown dtype {dtype!r}")


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def _fetch_all(spec: SourceSpec, since: int | None, limit: int | None) -> list[dict]:
    token = getattr(get_settings(), spec.app_token_env, None)
    headers = {"X-App-Token": token} if token else {}
    url = f"https://{spec.socrata_domain}/resource/{spec.resource_id}.json"
    rows: list[dict] = []
    offset = 0
    while True:
        params: dict[str, object] = {
            "$limit": spec.page_size,
            "$offset": offset,
            "$order": ":id",
        }
        if since is not None:
            floor = f"{since}-01-01" if spec.time_is_datetime else str(since)
            params["$where"] = f"{spec.time_column} >= '{floor}'"
        resp = requests.get(url, params=params, headers=headers, timeout=_SODA_TIMEOUT)
        if resp.status_code == 403 and headers:
            # A stale / wrong app token 403s where anonymous would 200. Anonymous
            # is rate-limited but fine for this ~8-page pull — drop it and retry.
            print(
                f"warning: {spec.app_token_env} rejected ({resp.status_code}) — "
                "continuing anonymously",
                file=sys.stderr,
            )
            headers = {}
            resp = requests.get(url, params=params, headers=headers, timeout=_SODA_TIMEOUT)
        resp.raise_for_status()
        batch = resp.json()
        rows.extend(batch)
        if limit is not None and len(rows) >= limit:
            return rows[:limit]
        if len(batch) < spec.page_size:
            return rows
        offset += spec.page_size


# --------------------------------------------------------------------------- #
# stage: raw records -> one list[dict] per target table
# --------------------------------------------------------------------------- #
def _stage(spec: SourceSpec, raw_rows: list[dict]) -> dict[str, list[dict]]:
    targets = list(spec.field_targets())
    staged: dict[str, list[dict]] = {t: [] for t in spec.target_tables}
    for rec in raw_rows:
        per_table: dict[str, dict] = {t: {} for t in spec.target_tables}
        for src_col, fm in targets:
            val = _coerce(rec.get(src_col), fm.dtype, spec.null_tokens)
            if fm.transform is not None:
                val = fm.transform(val)
            per_table[fm.table][fm.column] = val
        if spec.static_table:
            per_table[spec.static_table]["__sort"] = _coerce(rec.get(spec.static_sort_key), "int")
        for t in spec.target_tables:
            staged[t].append(per_table[t])

    for t in spec.target_tables:
        if t == spec.static_table:
            (key,) = spec.upsert_keys[t]
            staged[t] = _dedupe_latest_non_null(staged[t], key)
        else:
            staged[t] = _dedupe_by_key(staged[t], spec.upsert_keys[t])
    return staged


def _dedupe_latest_non_null(rows: list[dict], key: str) -> list[dict]:
    """One row per ``key`` for a slowly-changing dimension. Walk oldest -> newest
    by ``__sort``; later non-null values win, so each attribute reflects the most
    recent source row that reported it."""
    merged: dict[object, dict] = {}
    for row in sorted(rows, key=lambda r: r.get("__sort") or 0):
        ident = row.get(key)
        if ident is None:
            continue
        target = merged.get(ident)
        if target is None:
            merged[ident] = {c: v for c, v in row.items() if c != "__sort"}
        else:
            for col, val in row.items():
                if col != "__sort" and val is not None:
                    target[col] = val
    return list(merged.values())


def _dedupe_by_key(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """Last row wins per key tuple; rows with a null key part are dropped."""
    out: dict[tuple, dict] = {}
    for row in rows:
        ident = tuple(row.get(k) for k in keys)
        if None in ident:
            continue
        out[ident] = row
    return list(out.values())


# --------------------------------------------------------------------------- #
# upsert
# --------------------------------------------------------------------------- #
def _upsert(conn, table, rows: list[dict], conflict_cols: tuple[str, ...]) -> int:
    if not rows:
        return 0
    conflict = list(conflict_cols)
    updatable = [c.name for c in table.columns if c.name not in conflict and c.name != "id"]
    done = 0
    for start in range(0, len(rows), _CHUNK):
        chunk = rows[start : start + _CHUNK]
        stmt = sqlite_insert(table).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict,
            set_={col: stmt.excluded[col] for col in updatable},
        )
        conn.execute(stmt)
        done += len(chunk)
    return done


# --------------------------------------------------------------------------- #
# ingestion_runs + datasets
# --------------------------------------------------------------------------- #
def _utcnow() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_run(engine, meta: MetaData, key: str) -> int:
    runs = meta.tables["ingestion_runs"]
    with engine.begin() as conn:
        res = conn.execute(
            runs.insert().values(dataset_key=key, started_at=_utcnow(), status="running")
        )
        return int(res.inserted_primary_key[0])


def _close_run(engine, meta: MetaData, run_id: int, status: str, **fields) -> None:
    runs = meta.tables["ingestion_runs"]
    with engine.begin() as conn:
        conn.execute(
            runs.update()
            .where(runs.c.id == run_id)
            .values(
                finished_at=_utcnow(),
                status=status,
                rows_upserted=fields.get("rows"),
                source_row_count=fields.get("source_rows"),
                message=fields.get("message"),
            )
        )


def _catalog_dataset(spec: SourceSpec) -> dict:
    path = Path(spec.catalog_path)
    if not path.exists():
        return {}
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("dataset", {})


def _refresh_dataset(conn, spec: SourceSpec, meta: MetaData) -> None:
    ds = _catalog_dataset(spec)
    yc = spec.year_column
    row = conn.execute(
        text(f"SELECT COUNT(*) AS n, MIN({yc}) AS lo, MAX({yc}) AS hi FROM {spec.fact_table}")
    ).one()
    values = {
        "dataset_key": spec.key,
        "title": ds.get("title", spec.key),
        "description": ds.get("description"),
        "source_url": ds.get(
            "source_url", f"https://{spec.socrata_domain}/resource/{spec.resource_id}"
        ),
        "row_count": row.n,
        "first_year": row.lo,
        "last_year": row.hi,
        "last_ingested_at": _utcnow(),
    }
    stmt = sqlite_insert(meta.tables["datasets"]).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["dataset_key"],
        set_={k: v for k, v in values.items() if k != "dataset_key"},
    )
    conn.execute(stmt)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest.run", description="Socrata -> SQLite ingestion.")
    parser.add_argument("--source", required=True, help="registered source key (seattle_energy)")
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="delete existing rows for this source before loading",
    )
    parser.add_argument(
        "--since",
        type=int,
        metavar="YEAR",
        help="only pull rows from YEAR onward (spec.time_column)",
    )
    parser.add_argument("--limit", type=int, metavar="N", help="stop after N source rows (debug)")
    args = parser.parse_args(argv)

    if not db_path().exists():
        print(f"{db_path()} not found — run `python -m db.migrate` first", file=sys.stderr)
        return 1

    spec = load(args.source)
    engine = rw_engine()
    needed = (*spec.target_tables, "datasets", "ingestion_runs")
    have = set(inspect(engine).get_table_names())
    if missing := [t for t in needed if t not in have]:
        print(f"missing tables {missing} — run `python -m db.migrate` first", file=sys.stderr)
        return 1

    meta = MetaData()
    meta.reflect(bind=engine, only=list(needed))

    run_id = _open_run(engine, meta, spec.key)
    try:
        raw = _fetch_all(spec, args.since, args.limit)
        staged = _stage(spec, raw)
        upserted = 0
        with engine.begin() as conn:
            if args.full_refresh:
                for name in reversed(spec.target_tables):  # children first (FK)
                    conn.execute(meta.tables[name].delete())
            for name in spec.target_tables:  # parents first
                upserted += _upsert(conn, meta.tables[name], staged[name], spec.upsert_keys[name])
            _refresh_dataset(conn, spec, meta)
    except Exception as exc:  # noqa: BLE001 - job boundary: record the failure, then surface it
        _close_run(engine, meta, run_id, "error", message=repr(exc))
        print(f"ingest failed: {exc!r}", file=sys.stderr)
        return 1

    _close_run(engine, meta, run_id, "success", rows=upserted, source_rows=len(raw))
    per_table = ", ".join(f"{name}={len(staged[name])}" for name in spec.target_tables)
    print(f"{spec.key}: {len(raw)} source rows -> upserted {upserted} ({per_table})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
