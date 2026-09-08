"""Flask interface (architecture doc §4.6 + §10).

    POST /ask   {"question": "...", "source"?: "...", "turnstile_token"?: "..."}
      200 -> {"answer", "steps": [{"tool", "sql"?, "chart_url"?}],
              "usage": {"tokens", "cost_usd", "latency_ms"}, "stopped", "cached"?}
      200 -> {"disabled": true}                 demo off / monthly budget spent
      429 -> {"limited": true, "scope": "visitor" | "global"}
    GET  /health   -> {"status": "ok"}          liveness probe for the uptime monitor

The agent core (``answer_question``) stays unaware it is on the internet. This
module is the §10 wrapper: a response cache (a hit is free and doesn't count), a
per-visitor + global daily rate limit, an app-level monthly-budget cutoff, CORS
locked to the portfolio origin, and an optional Cloudflare Turnstile gate. All
of the Redis-backed pieces are inert until ``REDIS_URL`` is set, so local runs
behave exactly as before.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

from flask import Blueprint, Flask, jsonify, request

from agent.loop import AgentError, answer_question
from app.budget import month_budget
from app.cache import response_cache
from app.limits import hash_ip, rate_limiter
from config import get_settings

log = logging.getLogger("analytics_agent.api")

bp = Blueprint("agent", __name__)

_TURNSTILE_VERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _steps_for_api(result) -> list[dict]:
    """Trace steps -> the compact ``[{tool, sql?, chart_url?}]`` the API returns."""
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


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() or request.remote_addr or "0.0.0.0"


def _turnstile_ok(token: str, secret: str) -> bool:
    data = urllib.parse.urlencode({"secret": secret, "response": token or ""}).encode()
    try:
        with urllib.request.urlopen(_TURNSTILE_VERIFY, data=data, timeout=5) as resp:
            return bool(json.load(resp).get("success"))
    except Exception:  # noqa: BLE001 - a verifier outage should not 500 the API
        log.warning("turnstile verify failed to reach Cloudflare")
        return False


@bp.after_request
def _cors(resp):
    origin = get_settings().CORS_ALLOWED_ORIGIN
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@bp.route("/ask", methods=["OPTIONS"])
def ask_preflight():
    return ("", 204)


@bp.get("/health")
def health():
    return jsonify(status="ok")


@bp.post("/ask")
def ask():
    settings = get_settings()
    if not settings.DEMO_ENABLED or month_budget().over_budget():
        return jsonify(disabled=True)

    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify(error="missing 'question'"), 400
    source = (body.get("source") or "seattle_energy").strip()

    if settings.TURNSTILE_SECRET and not _turnstile_ok(
        body.get("turnstile_token", ""), settings.TURNSTILE_SECRET
    ):
        return jsonify(error="challenge failed"), 403

    model = settings.ANALYST_MODEL
    cache = response_cache()
    hit = cache.get(question, source, model)
    if hit is not None:
        return jsonify(**hit, cached=True)

    ip_hash = hash_ip(_client_ip(), settings.RATE_LIMIT_SALT)
    decision = rate_limiter().check(ip_hash)
    if not decision.allowed:
        return jsonify(limited=True, scope=decision.scope), 429

    try:
        result = answer_question(question, source=source)
    except AgentError as exc:
        log.warning("agent error on %r: %s", question, exc)
        return jsonify(error="the analyst could not answer that right now"), 502
    except Exception:
        log.exception("unexpected error on %r", question)
        return jsonify(error="internal error"), 500

    trace = result.trace
    payload = {
        "answer": result.answer,
        "steps": _steps_for_api(result),
        "usage": {
            "tokens": trace.get("input_tokens", 0) + trace.get("output_tokens", 0),
            "cost_usd": round(trace.get("cost_usd", 0.0), 6),
            "latency_ms": trace.get("latency_ms", 0.0),
        },
        "stopped": result.stopped,
    }
    cache.put(question, source, model, payload)
    rate_limiter().record(ip_hash)
    month_budget().add(payload["usage"]["cost_usd"])
    return jsonify(**payload)


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
