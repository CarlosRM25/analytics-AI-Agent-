"""Per-run structured trace (milestone M4).

Records each step: tool calls, input/output token counts, ``stop_reason``,
estimated cost, latency, and any errors. Flushed as one line to
``logs/runs.jsonl`` per question, and summarised for the CLI
(``4 steps · 2 queries · 1 chart · 18.2k tok · $0.11 · 6.4s``).
"""

# TODO(M4): Trace class — record(resp), record_tool(...), summary(), flush().
