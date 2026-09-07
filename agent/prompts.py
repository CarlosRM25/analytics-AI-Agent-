"""System prompt assembly (milestone M4).

Carries: the analyst role, a compact schema summary generated from the catalog at
startup, and the rules — always ``LIMIT``, list columns explicitly, prefer
``run_sql`` for "what does the data say" and ``predict`` for "what would the model
expect", call ``make_chart`` when a comparison or trend is the answer, always
surface the SQL, and stop (plain ``end_turn``) once the question can be answered.
Also states the SQL dialect (MySQL locally, SQLite in the deployed demo).
"""

# TODO(M4): build_system_prompt(source_key) -> str, with a cache breakpoint marker.
