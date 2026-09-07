"""Tool schemas (strict) + their Python handlers (milestones M4-M5).

Five tools, all defined with ``strict: true`` / ``additionalProperties: false`` /
explicit ``required``:

  list_datasets   rows from ``datasets`` + one-line descriptions
  describe_schema SQLite introspection (PRAGMA table_info) + the catalog YAML
  run_sql         ro_engine (PRAGMA query_only); single SELECT/WITH, auto-LIMIT, timeout
  predict         only registered when the active source has a model_module
  make_chart      constrained spec -> Plotly figure -> outputs/charts/<run_id>-<n>.html
"""

# TODO(M4): list_datasets, describe_schema, run_sql (+ sqlparse guardrails).
# TODO(M5): predict, make_chart.
