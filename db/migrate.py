"""Apply numbered SQL migrations in order (milestone M1).

Reads ``db/migrations/NNN_*.sql`` sorted by prefix, tracks which have run in a
``schema_migrations`` table, and applies the rest as the ETL user inside a
transaction. Alembic is deliberately not used — the schema is small and static.
"""

# TODO(M1): implement the ordered apply + tracking table.
