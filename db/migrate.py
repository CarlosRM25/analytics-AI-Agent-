"""Apply ``db/schema.sql`` then numbered ``db/migrations/NNN_*.sql``, in order.

``python -m db.migrate``

``schema.sql`` is idempotent (``CREATE TABLE IF NOT EXISTS``) and re-applied every
run; numbered migrations are applied once and recorded in ``schema_migrations``.
Alembic is deliberately not used — the schema is small and static.
"""

from __future__ import annotations

from pathlib import Path

from db.engine import rw_engine

_DB_DIR = Path(__file__).parent
_SCHEMA = _DB_DIR / "schema.sql"
_MIGRATIONS = _DB_DIR / "migrations"

_TRACK_TABLE = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    " filename TEXT PRIMARY KEY,"
    " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))"
)


def main() -> int:
    pooled = rw_engine().raw_connection()
    try:
        conn = pooled.driver_connection  # the real sqlite3.Connection
        conn.execute(_TRACK_TABLE)
        conn.commit()
        done = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}

        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
        print(f"applied {_SCHEMA.name}")

        for path in sorted(_MIGRATIONS.glob("[0-9]*.sql")):
            if path.name in done:
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (?)", (path.name,))
            conn.commit()
            print(f"applied {path.name}")
    finally:
        pooled.close()

    print("migrations up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
