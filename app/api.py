"""Flask blueprint (milestone M6).

``POST /ask {question}`` -> ``{answer, steps: [{tool, sql?, chart_url?}],
usage: {tokens, cost_usd, latency_ms}}``. ``GET /health`` for the uptime monitor.
No auth, no persistence beyond ``logs/runs.jsonl``. The public deployment wraps
this same route with the ``limits`` + ``cache`` middleware (section 10); the core
stays unaware it is on the internet.
"""

# TODO(M6): create_app() with the /ask + /health blueprint.
