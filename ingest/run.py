"""Generic ingestion entry point (milestone M1).

``python -m ingest.run --source seattle_energy [--full-refresh] [--since 2015] [--limit N]``

Driven entirely by the ``SourceSpec`` from ``sources.REGISTRY``: page the SODA
endpoint with ``$limit``/``$offset``, apply ``field_map`` into one DataFrame per
target table, ``INSERT ... ON DUPLICATE KEY UPDATE`` on the upsert keys as the
ETL user, then write an ``ingestion_runs`` row and refresh the ``datasets`` row.
"""

# TODO(M1): implement the paginated pull + upsert described in section 4.2.
