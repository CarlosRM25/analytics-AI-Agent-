"""Tool schemas (strict) + their Python handlers (milestones M4-M5).

Five tools, all defined with ``strict: true`` / ``additionalProperties: false`` /
explicit ``required``:

  list_datasets   rows from ``datasets`` + one-line descriptions
  describe_schema information_schema + the catalog YAML (never the raw DB)
  run_sql         read-only user; single SELECT/WITH, auto-LIMIT, statement timeout
  predict         only registered when the active source has a model_module
  make_chart      constrained spec -> Plotly figure -> outputs/charts/<run_id>-<n>.html
"""

# TODO(M4): list_datasets, describe_schema, run_sql (+ sqlparse guardrails).
# TODO(M5): predict, make_chart.
