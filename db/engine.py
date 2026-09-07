"""SQLite engines (architecture doc section 7, amended 2026-09-07).

Two engines over the one database file:

* ``rw_engine()`` — read/write, for ``ingest/`` and ``db/migrate.py``. Creates
  the file on first connect. ``PRAGMA foreign_keys = ON`` per connection.
* ``ro_engine()`` — the only engine the agent's ``run_sql`` may use. Every
  connection runs ``PRAGMA query_only = ON``, so any INSERT / UPDATE / DELETE /
  DDL fails with SQLITE_READONLY from SQLite itself. This is what replaces the
  least-privilege DB user from the original MySQL design (section 8.8); the SQL
  allowlist in ``run_sql`` (M4) is the co-layer.

Same file, same code, local and deployed — ``SQLITE_PATH`` is the only knob.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from config import get_settings


def db_path() -> Path:
    """Absolute path to the SQLite file (``SQLITE_PATH`` resolved against CWD)."""
    return Path(get_settings().SQLITE_PATH).expanduser().resolve()


@lru_cache
def rw_engine() -> Engine:
    """Read/write engine for ingestion and migrations."""
    engine = create_engine(f"sqlite:///{db_path().as_posix()}")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()

    return engine


@lru_cache
def ro_engine() -> Engine:
    """Read-only engine — writes raise SQLITE_READONLY. Agent-only."""
    path = db_path()
    if not path.exists():
        raise RuntimeError(f"{path} does not exist — run `python -m db.migrate` first")
    engine = create_engine(f"sqlite:///{path.as_posix()}")

    @event.listens_for(engine, "connect")
    def _read_only(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA query_only = ON")
        cur.close()

    return engine
