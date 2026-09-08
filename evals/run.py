"""Evaluation runner (architecture doc §4.7).

    python -m evals.run                       # all questions, ANALYST_MODEL
    python -m evals.run --only q08,q14        # a subset
    python -m evals.run --model claude-haiku-4-5 --judge
    python -m evals.run --dry-run             # print the plan, no API calls

Runs the agent over every item in ``questions.yaml``, grades it with the
assertions in that file (plus an optional LLM judge on the free-text answer),
and writes ``results/run-<timestamp>.json`` + a markdown summary reporting pass
rate, avg iterations, avg tokens, and **$ per question**. It calls the real API
and costs money — that is why it is here and not under ``tests/``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import click
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_QUESTIONS = _ROOT / "evals" / "questions.yaml"
_RESULTS = _ROOT / "evals" / "results"
_NUMERIC = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


# --------------------------------------------------------------------------- #
# grading
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclasses.dataclass
class ItemResult:
    id: str
    kind: str
    question: str
    passed: bool
    checks: list[Check]
    model_turns: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    stopped: str = ""
    answer: str = ""
    sql: list[str] = dataclasses.field(default_factory=list)
    charts: list[str] = dataclasses.field(default_factory=list)
    judge_pass: bool | None = None
    judge_reason: str = ""
    error: str | None = None


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUMERIC.findall(text):
        with contextlib.suppress(ValueError):
            out.append(float(m.replace(",", "")))
    return out


def grade(item: dict, result) -> list[Check]:
    """Apply the item's ``expects`` block to an ``AnswerResult``-like object."""
    expects = item.get("expects") or {}
    answer = (result.answer or "").lower()
    sql_blob = " ".join(result.sql).lower()
    tools_called = {s["tool"] for s in result.trace.get("steps", []) if s.get("kind") == "tool"}
    checks: list[Check] = []

    for tool in expects.get("tools_used", []):
        in_ = tool in tools_called
        checks.append(Check(f"tool:{tool}", in_, "" if in_ else "not called"))

    for table in expects.get("tables_touched", []):
        hit = table.lower() in sql_blob
        checks.append(Check(f"table:{table}", hit, "" if hit else "not in any run_sql"))

    for needle in expects.get("answer_contains", []):
        hit = needle.lower() in answer
        checks.append(Check(f"contains:{needle}", hit, "" if hit else "missing"))

    any_of = expects.get("answer_contains_any")
    if any_of:
        hit = any(n.lower() in answer for n in any_of)
        checks.append(Check("contains_any", hit, "" if hit else f"none of {any_of}"))

    num = expects.get("numeric")
    if num:
        found = _numbers(result.answer or "")
        ok = any(abs(v - num["value"]) <= num["tol"] for v in found)
        checks.append(
            Check("numeric", ok, "" if ok else f"want {num['value']}±{num['tol']}, saw {found[:8]}")
        )

    if expects.get("chart"):
        ok = bool(result.charts)
        checks.append(Check("chart", ok, "" if ok else "no chart produced"))

    if "max_iters" in expects:
        turns = result.trace.get("model_turns", 0)
        ok = turns <= expects["max_iters"]
        checks.append(Check("max_iters", ok, "" if ok else f"{turns} > {expects['max_iters']}"))

    return checks


# --------------------------------------------------------------------------- #
# optional LLM judge (advisory — reported, does not change pass/fail)
# --------------------------------------------------------------------------- #
_JUDGE_MODEL = "claude-haiku-4-5"
_JUDGE_SYS = (
    "You grade a data analyst's answer. Reply with ONLY a JSON object "
    '{"verdict": "pass" | "fail", "reason": "<one sentence>"}. '
    "Pass if the answer is factually reasonable and actually addresses the question."
)


def judge(question: str, answer: str, notes: str, api_key: str) -> tuple[bool, str]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    user = f"Question: {question}\n\nAnswer: {answer}\n\nGrader notes: {notes}"
    resp = client.messages.create(
        model=_JUDGE_MODEL,
        max_tokens=200,
        system=_JUDGE_SYS,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        obj = json.loads(text[text.index("{") : text.rindex("}") + 1])
        return obj.get("verdict") == "pass", str(obj.get("reason", ""))
    except (ValueError, json.JSONDecodeError):
        return False, f"unparseable judge reply: {text[:120]}"


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class RunReport:
    started_at: str
    model: str
    source: str
    n: int
    n_passed: int
    avg_turns: float
    avg_tokens: float
    total_cost_usd: float
    cost_per_question: float
    wall_seconds: float
    cache_hit_rate: float
    items: list[ItemResult]

    @property
    def pass_rate(self) -> float:
        return self.n_passed / self.n if self.n else 0.0


def run_items(items: list[dict], source: str, use_judge: bool) -> list[ItemResult]:
    from agent.loop import AgentError, answer_question
    from config import get_settings

    api_key = get_settings().ANTHROPIC_API_KEY
    results: list[ItemResult] = []
    for item in items:
        click.echo(f"  {item['id']}: {item['question'].strip()[:70]}...", err=True)
        try:
            ans = answer_question(item["question"], source=source)
        except AgentError as exc:
            results.append(
                ItemResult(
                    item["id"],
                    item.get("kind", "?"),
                    item["question"],
                    False,
                    [],
                    error=str(exc),
                )
            )
            continue

        checks = grade(item, ans)
        tr = ans.trace
        row = ItemResult(
            id=item["id"],
            kind=item.get("kind", "?"),
            question=item["question"],
            passed=bool(checks) and all(c.ok for c in checks),
            checks=checks,
            model_turns=tr.get("model_turns", 0),
            tokens=tr.get("input_tokens", 0) + tr.get("output_tokens", 0),
            cost_usd=tr.get("cost_usd", 0.0),
            latency_ms=tr.get("latency_ms", 0.0),
            cache_read_tokens=tr.get("cache_read_tokens", 0),
            cache_creation_tokens=tr.get("cache_creation_tokens", 0),
            stopped=ans.stopped,
            answer=ans.answer,
            sql=list(ans.sql),
            charts=list(ans.charts),
        )
        if use_judge and api_key:
            row.judge_pass, row.judge_reason = judge(
                item["question"], ans.answer, item.get("notes", ""), api_key
            )
        results.append(row)
    return results


def build_report(items: list[ItemResult], model: str, source: str, wall: float) -> RunReport:
    ok = [r for r in items if not r.error]
    n = len(items)
    cache_read = sum(r.cache_read_tokens for r in ok)
    cacheable = cache_read + sum(r.cache_creation_tokens for r in ok)
    return RunReport(
        started_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        model=model,
        source=source,
        n=n,
        n_passed=sum(1 for r in items if r.passed),
        avg_turns=round(sum(r.model_turns for r in ok) / len(ok), 2) if ok else 0.0,
        avg_tokens=round(sum(r.tokens for r in ok) / len(ok)) if ok else 0.0,
        total_cost_usd=round(sum(r.cost_usd for r in items), 4),
        cost_per_question=round(sum(r.cost_usd for r in items) / n, 4) if n else 0.0,
        wall_seconds=round(wall, 1),
        cache_hit_rate=round(cache_read / cacheable, 3) if cacheable else 0.0,
        items=items,
    )


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def render_markdown(report: RunReport) -> str:
    lines = [
        f"# Eval run {report.started_at}",
        "",
        f"model **{report.model}** · source `{report.source}` · {report.n} questions",
        "",
        f"**pass rate: {report.n_passed}/{report.n} ({report.pass_rate:.0%})**",
        "",
        f"- avg iterations: {report.avg_turns}",
        f"- avg tokens: {report.avg_tokens / 1000:.1f}k",
        f"- total cost: ${report.total_cost_usd:.4f}  (${report.cost_per_question:.4f} / question)",
        f"- wall time: {report.wall_seconds}s",
        f"- prompt-cache hit rate (of cacheable prefix tokens): {report.cache_hit_rate:.0%}",
        "",
        "| id | kind | pass | iters | tok | $ | failed checks |",
        "|----|------|:----:|:-----:|:---:|:--:|---------------|",
    ]
    for r in report.items:
        if r.error:
            lines.append(f"| {r.id} | {r.kind} | 💥 | - | - | - | ERROR |")
            continue
        failed = ", ".join(f"`{c.name}`" for c in r.checks if not c.ok)
        judged = "" if r.judge_pass is None else (" · judge✅" if r.judge_pass else " · judge❌")
        lines.append(
            f"| {r.id} | {r.kind} | {'✅' if r.passed else '❌'} | {r.model_turns} | "
            f"{r.tokens / 1000:.1f}k | {r.cost_usd:.4f} | {failed}{judged} |"
        )

    fails = [r for r in report.items if not r.passed]
    if fails:
        lines += ["", "## Failures", ""]
        for r in fails:
            lines += [f"### {r.id} — {r.kind}", "", f"**Q:** {r.question.strip()}", ""]
            if r.error:
                lines += [f"_errored:_ {r.error}", ""]
                continue
            lines += [f"**answer:** {r.answer.strip()[:900]}", ""]
            bad = [f"- `{c.name}` — {c.detail}" for c in r.checks if not c.ok]
            if bad:
                lines += ["**failed checks:**", *bad]
            if r.judge_pass is False:
                lines.append(f"- `judge` — {r.judge_reason}")
            if r.sql:
                joined = "\n".join(r.sql)
                lines += ["", f"```sql\n{joined}\n```"]
            lines.append("")
    return "\n".join(lines) + "\n"


def write_report(report: RunReport) -> tuple[Path, Path]:
    _RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.replace(":", "").replace("-", "").replace("+00:00", "Z")
    json_path = _RESULTS / f"run-{stamp}.json"
    md_path = _RESULTS / f"run-{stamp}.md"
    body = json.dumps(dataclasses.asdict(report), indent=2, default=str)
    json_path.write_text(body, encoding="utf-8", newline="\n")
    md_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return json_path, md_path


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def load_questions(path: Path = _QUESTIONS) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise ValueError("questions.yaml must be a list of items")
    return data


@click.command()
@click.option("--source", default="seattle_energy", show_default=True)
@click.option("--only", default="", help="Comma-separated ids to run (default: all).")
@click.option("--limit", type=int, default=0, help="Run at most N items.")
@click.option("--model", default="", help="Override ANALYST_MODEL for this run.")
@click.option("--judge", "use_judge", is_flag=True, help="Also grade answers with an LLM judge.")
@click.option("--dry-run", is_flag=True, help="Print the plan and exit — no API calls.")
def main(source: str, only: str, limit: int, model: str, use_judge: bool, dry_run: bool) -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    items = load_questions()
    if only:
        wanted = {s.strip() for s in only.split(",") if s.strip()}
        items = [i for i in items if i["id"] in wanted]
    if limit:
        items = items[:limit]
    if not items:
        raise click.ClickException("no questions selected")

    if model:
        os.environ["ANALYST_MODEL"] = model
    from config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    if dry_run:
        click.echo(f"{len(items)} question(s) · source={source} · model={settings.ANALYST_MODEL}")
        for i in items:
            exp = ", ".join((i.get("expects") or {}).keys()) or "(no checks)"
            click.echo(f"  {i['id']} [{i.get('kind', '?')}] {i['question'].strip()[:80]}  <- {exp}")
        return

    if not settings.ANTHROPIC_API_KEY:
        raise click.ClickException("ANTHROPIC_API_KEY is not set — add it to .env")

    click.echo(f"running {len(items)} question(s) on {settings.ANALYST_MODEL}...", err=True)
    t0 = time.monotonic()
    results = run_items(items, source, use_judge)
    report = build_report(results, settings.ANALYST_MODEL, source, time.monotonic() - t0)
    json_path, md_path = write_report(report)

    click.echo(render_markdown(report))
    click.echo(f"wrote {json_path.relative_to(_ROOT)} and {md_path.relative_to(_ROOT)}", err=True)
    if report.n_passed < report.n:
        sys.exit(1)


if __name__ == "__main__":
    main()
