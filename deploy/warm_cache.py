"""Warm the deployed response cache (M8, architecture doc §10).

    python -m deploy.warm_cache https://analytics-agent-xxxx.run.app
    python -m deploy.warm_cache https://... --extra deploy/recruiter_questions.txt

POSTs every question in ``evals/questions.yaml`` (plus any ``--extra`` file, one
question per line) to the live ``/ask`` once. The first hit populates the cache;
after that those questions are instant and free for real visitors. Run it right
after each deploy (the cache TTL is 30 days and a redeploy does not clear it, so
re-running is cheap — mostly cache hits).

It spends real API money on the first run (~$0.004 per novel question on Haiku).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_QUESTIONS = _ROOT / "evals" / "questions.yaml"


def _load_questions(extra: Path | None) -> list[str]:
    items = yaml.safe_load(_QUESTIONS.read_text(encoding="utf-8")) or []
    qs = [i["question"].strip() for i in items if i.get("question")]
    if extra and extra.exists():
        qs += [ln.strip() for ln in extra.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # de-dupe, keep order
    seen: set[str] = set()
    return [q for q in qs if not (q in seen or seen.add(q))]


def _ask(base: str, question: str, timeout: float) -> dict:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/ask",
        data=json.dumps({"question": question}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deploy.warm_cache")
    ap.add_argument("base_url", help="deployed service origin, e.g. https://...run.app")
    ap.add_argument("--extra", type=Path, help="file of extra questions, one per line")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    args = ap.parse_args(argv)

    questions = _load_questions(args.extra)
    print(f"warming {len(questions)} questions against {args.base_url}", file=sys.stderr)

    hits = misses = errors = 0
    spend = 0.0
    for i, q in enumerate(questions, 1):
        try:
            data = _ask(args.base_url, q, args.timeout)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors += 1
            print(f"  [{i:2}/{len(questions)}] ERROR  {q[:60]}  ({exc})")
            continue
        if data.get("disabled") or data.get("limited"):
            print(f"  [{i:2}/{len(questions)}] SKIP   demo disabled/limited — stopping")
            break
        cached = bool(data.get("cached"))
        hits += cached
        misses += not cached
        spend += 0.0 if cached else float(data.get("usage", {}).get("cost_usd", 0.0))
        print(f"  [{i:2}/{len(questions)}] {'HIT ' if cached else 'warm'}  {q[:60]}")
        time.sleep(args.delay)

    print(
        f"\ndone — {hits} hit, {misses} warmed, {errors} error · ~${spend:.4f} spent",
        file=sys.stderr,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
