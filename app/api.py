"""Flask interface (architecture doc §4.6).

    POST /ask   {"question": "...", "source"?: "seattle_energy"}
      -> {"answer", "steps": [{"tool", "sql"?, "chart_url"?}],
          "usage": {"tokens", "cost_usd", "latency_ms"}, "stopped"}
    GET  /health   -> {"status": "ok"}   (liveness probe for the uptime monitor)

One blueprint, no auth, no persistence beyond ``logs/runs.jsonl`` (the loop
writes that itself). The public deployment (§10) wraps this same ``/ask`` with
rate-limit + response-cache middleware; the core stays unaware it is on the
internet. Two things it does do now because they are cheap and correct:

* ``DEMO_ENABLED=false`` short-circuits ``/ask`` to ``{"disabled": true}`` (the
  §10 kill switch) so the frontend can fall back to the offline gallery.
* the client never sees a stack trace — errors are logged server-side and
  returned as a generic message (§8.11).
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, Flask, jsonify, request

from agent.loop import AgentError, answer_question
from config import get_settings

log = logging.getLogger("analytics_agent.api")

bp = Blueprint("agent", __name__)


def _steps_for_api(result) -> list[dict]:
    """Trace steps -> the compact ``[{tool, sql?, chart_url?}]`` the API returns.

    Chart paths are matched to ``make_chart`` steps in call order.
    """
    charts = list(result.charts)
    steps: list[dict] = []
    for step in result.trace.get("steps", []):
        if step.get("kind") != "tool":
            continue
        item = {"tool": step["tool"]}
        if step.get("sql"):
            item["sql"] = step["sql"]
        if step["tool"] == "make_chart" and charts:
            item["chart_url"] = charts.pop(0)
        steps.append(item)
    return steps


@bp.get("/health")
def health():
    return jsonify(status="ok")


@bp.post("/ask")
def ask():
    settings = get_settings()
    if not settings.DEMO_ENABLED:
        return jsonify(disabled=True)

    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify(error="missing 'question'"), 400
    source = (body.get("source") or "seattle_energy").strip()

    try:
        result = answer_question(question, source=source)
    except AgentError as exc:
        log.warning("agent error on %r: %s", question, exc)
        return jsonify(error="the analyst could not answer that right now"), 502
    except Exception:
        log.exception("unexpected error on %r", question)
        return jsonify(error="internal error"), 500

    trace = result.trace
    return jsonify(
        answer=result.answer,
        steps=_steps_for_api(result),
        usage={
            "tokens": trace.get("input_tokens", 0) + trace.get("output_tokens", 0),
            "cost_usd": round(trace.get("cost_usd", 0.0), 6),
            "latency_ms": trace.get("latency_ms", 0.0),
        },
        stopped=result.stopped,
    )


def create_app() -> Flask:
    """App factory — also the gunicorn entry point (``app.api:create_app()``).

    Deployed on Cloud Run: ``gunicorn -b 0.0.0.0:$PORT 'app.api:create_app()'``
    (see ``deploy/Dockerfile``).
    """
    get_settings()  # fail fast on a bad environment, before serving
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # a question is a sentence, not a payload
    app.register_blueprint(bp)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", "8000"))
    create_app().run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
