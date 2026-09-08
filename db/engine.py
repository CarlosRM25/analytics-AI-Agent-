"""SQLite engines (architecture doc section 7, amended 2026-09-07).

Two engines over the one database file:

* ``rw_engine()`` — read/write, for ``ingest/`` and ``db/migrate.py``. Creates
  the file on first connect. ``PRAGMA foreign_keys = ON`` per connection.
* ``ro_engine()`` — the only engine the agent's ``run_sql`` may use. Every
  connection runs ``PRAGMA query_only = ON``, so any INSERT / UPDATE / DELETE /
  DDL fails with SQLITE_READONLY from SQLite itself. This is what replaces the
  least-privilege DB user from the original MySQL design (section 8.8); the SQL
  allowlist in ``run_sql`` (M4) is the co-layer.

Both connections register the aggregate functions SQLite lacks —
``median(x)``, ``mode(x)``, ``mean(x)`` — so the agent doesn't burn turns
re-deriving a median with window functions. NULLs are skipped; each returns NULL
over an empty set.

Same file, same code, local and deployed — ``SQLITE_PATH`` is the only knob.
"""

from __future__ import annotations

import statistics
from collections import Counter
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from config import get_settings


class _Median:
    def __init__(self) -> None:
        self._values: list = []

    def step(self, value) -> None:
        if value is not None:
            self._values.append(value)

    def finalize(self):
        return statistics.median(self._values) if self._values else None


class _Mean:
    def __init__(self) -> None:
        self._total = 0.0
        self._n = 0

    def step(self, value) -> None:
        if value is not None:
            self._total += value
            self._n += 1

    def finalize(self):
        return self._total / self._n if self._n else None


class _Mode:
    def __init__(self) -> None:
        self._values: list = []

    def step(self, value) -> None:
        if value is not None:
            self._values.append(value)

    def finalize(self):
        if not self._values:
            return None
        counts = Counter(self._values)
        most = max(counts.values())
        # deterministic tie-break: smallest of the joint-most-frequent values
        return min(v for v, c in counts.items() if c == most)


def _register_aggregates(dbapi_conn) -> None:
    dbapi_conn.create_aggregate("median", 1, _Median)
    dbapi_conn.create_aggregate("mean", 1, _Mean)
    dbapi_conn.create_aggregate("mode", 1, _Mode)


def db_path() -> Path:
    """Absolute path to the SQLite file (``SQLITE_PATH`` resolved against CWD)."""
    return Path(get_settings().SQLITE_PATH).expanduser().resolve()


@lru_cache
def rw_engine() -> Engine:
    """Read/write engine for ingestion and migrations."""
    engine = create_engine(f"sqlite:///{db_path().as_posix()}")

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()
        _register_aggregates(dbapi_conn)

    return engine


@lru_cache
def ro_engine() -> Engine:
    """Read-only engine — writes raise SQLITE_READONLY. Agent-only.

    Deployed (Cloud Run), the file is also opened ``mode=ro`` at the OS level
    (``file:...?mode=ro&uri=true``) — SQLite can't write the file even if a
    ``PRAGMA`` re-enabled it. This is the connection-level guarantee §8.8 of the
    architecture doc specified in place of the MySQL least-privilege user. Local
    keeps the plain path (Windows + the ``file:`` URI are fiddly, and
    ``query_only`` is enough for dev).
    """
    path = db_path()
    if not path.exists():
        raise RuntimeError(f"{path} does not exist — run `python -m db.migrate` first")
    if get_settings().DEPLOY_MODE == "deployed":
        url = f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true"
    else:
        url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA query_only = ON")
        cur.close()
        _register_aggregates(dbapi_conn)

    return engine
