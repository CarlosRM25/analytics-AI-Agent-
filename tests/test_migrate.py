"""M1: db/migrate.py builds the schema on a fresh SQLite file, idempotently, and
the read-only engine refuses writes.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from db import migrate
from db.engine import ro_engine, rw_engine

_EXPECTED = {"datasets", "buildings", "energy_records", "ingestion_runs", "schema_migrations"}


def test_migrate_creates_all_tables(sqlite_env):
    assert migrate.main() == 0
    assert set(inspect(rw_engine()).get_table_names()) >= _EXPECTED


def test_migrate_is_idempotent(sqlite_env):
    assert migrate.main() == 0
    assert migrate.main() == 0
    assert set(inspect(rw_engine()).get_table_names()) >= _EXPECTED


def test_ro_engine_rejects_writes(sqlite_env):
    migrate.main()
    with ro_engine().connect() as conn, pytest.raises(OperationalError):
        conn.execute(text("INSERT INTO datasets (dataset_key, title) VALUES ('x', 'y')"))


def test_ro_engine_missing_file_raises(sqlite_env):
    # sqlite_env sets the path but migrate has not run, so the file does not exist
    with pytest.raises(RuntimeError):
        ro_engine()
