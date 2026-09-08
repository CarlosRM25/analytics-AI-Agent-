"""System prompt assembly (architecture doc §4.5).

One stable text block per source: a generic role + tool-use rules, plus the
schema, facts, worked examples, and column-value cheat sheet **read from that
source's catalog** (``catalog/<key>.yaml`` under ``prompt:``). So a new dataset
is still config only — nothing source-specific lives here.

Marked with a ``cache_control`` breakpoint so ``tools`` + ``system`` are cached
and only the per-question message varies.

**Cache floor (M6):** ``claude-haiku-4-5`` does not cache a ~4,214-token prefix
but does cache ~4,514 (so ~4,096; Opus/Sonnet are 1,024). The energy catalog's
``prompt:`` block is sized to keep ``tools`` + ``system`` comfortably over 4,600
tokens. A smaller source (crime) may fall under the floor — that is fine, it just
does not benefit from caching. Everything is static (no timestamps, file-order
iteration) so the cache key holds.
"""

from __future__ import annotations

from agent import catalog

_ROLE = """\
You are a careful data analyst. You answer questions about the dataset below by
writing and running SQL (or, when a model is offered, calling it for predictive
questions), then explaining the result in plain English.

Dialect: SQLite. Window functions and CTEs (WITH) are available. There is no
information_schema. The full schema is below — you rarely need describe_schema.
"""

_RULES = """\
Tools:
- run_sql — for "what does the data say". One SELECT/WITH; a LIMIT is injected,
  but add ORDER BY for "top N" / "worst" / "trend".
- predict — offered only for sources with a trained model. ONLY for "what would
  the model expect / predict / estimate for a hypothetical case"; never for
  summarising the data. See its tool schema for the exact feature names.
- make_chart — when a comparison or trend IS the answer. Usual flow: run_sql to
  get the rows, then make_chart with data="last_query".
- describe_schema — only if you hit a column you don't recognise.

Rules:
- List columns explicitly; never SELECT *.
- Aggregates: AVG, plus registered median(x), mode(x), mean(x). Use them
  directly. SQLite has no PERCENTILE_CONT — for other percentiles use a
  ROW_NUMBER() window.
- A column that is NULL where unreported is not 0 — filter it, don't sum it as 0.
- If a tool returns an error_type/message, read it and fix the call.
- When you have enough to answer, stop calling tools and reply. State the
  finding, the key numbers, the SQL you ran, and any chart path.
"""

_SECTIONS = ("facts", "examples", "values", "rules")


def build_system_prompt(source_key: str = "seattle_energy") -> list[dict]:
    cat = catalog.load(source_key)
    prompt = cat.get("prompt") or {}
    parts = [_ROLE.strip(), catalog.summary_text(source_key), _RULES.strip()]
    for section in _SECTIONS:
        body = (prompt.get(section) or "").strip()
        if body:
            parts.append(body)
    text = "\n\n".join(parts)
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
