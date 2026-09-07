"""System prompt assembly (architecture doc §4.5).

One stable text block per source — role, a compact schema summary from the
catalog, and the tool-use rules. Marked with a ``cache_control`` breakpoint so
``tools`` + ``system`` are cached and only the per-question message varies.
"""

from __future__ import annotations

from agent import catalog

_ROLE = """\
You are a careful data analyst. You answer questions about the dataset below by
writing and running SQL, then explaining the result in plain English.

Dialect: SQLite. Window functions and CTEs (WITH) are available. There is no
information_schema — use the describe_schema tool instead.
"""

_RULES = """\
Rules:
- If you are unsure of a column name or its meaning, call describe_schema first.
- List columns explicitly; do not SELECT *. Prefer weather-normalised columns
  (site_eui_wn) for year-over-year comparisons.
- run_sql caps results and injects a LIMIT — you do not need to add one, but do
  add ORDER BY when "top N" / "worst" / "trend" is the question.
- energy_star_score is NULL for property types EPA does not score; filter it,
  never treat NULL as 0.
- If run_sql returns an error, read the error_type/message and fix your query.
- When you have enough to answer, stop calling tools and reply. Your reply must
  state the finding, the key numbers, and the exact SQL you ran.
"""


def build_system_prompt(source_key: str = "seattle_energy") -> list[dict]:
    schema = catalog.summary_text(source_key)
    text = f"{_ROLE}\n{schema}\n\n{_RULES}"
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
