"""The hand-written tool-use loop (architecture doc §4.5).

    from agent.loop import answer_question
    result = answer_question("which property types have the worst emissions intensity?")

Multi-step: each iteration calls the model; if it stops for ``tool_use`` we run
*every* tool block and return *all* results in one user message, then continue.
``MAX_AGENT_ITERS`` caps it. No streaming, no assistant prefill (rejected on the
current models — output is shaped by the system prompt).
"""

from __future__ import annotations

import contextlib
import sys
import time
from dataclasses import dataclass, field

import anthropic

from agent.prompts import build_system_prompt
from agent.tools import RunContext, run_tool, tool_schemas
from agent.trace import Trace
from config import get_settings

_MAX_TOKENS = 16_000
_RATE_LIMIT_RETRIES = 2


@dataclass
class AnswerResult:
    answer: str
    sql: list[str] = field(default_factory=list)
    charts: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    trace: dict = field(default_factory=dict)
    stopped: str = "end_turn"  # end_turn | max_iters | refusal

    def print_cli(self) -> None:
        print(self.answer.strip() or "(no answer)")
        for i, q in enumerate(self.sql, 1):
            print(f"\n--- SQL {i} ---\n{q.strip()}")
        for chart in self.charts:
            print(f"\n--- chart: {chart}")
        print(f"\n{self.trace.get('summary', '')}")


class AgentError(RuntimeError):
    """A call to the model failed in a way the loop can't recover from."""


def _thinking(model: str):
    # adaptive thinking on the Opus/Sonnet/Fable family; Haiku 4.5 (dev/eval/demo)
    # runs the fast path without it (it would need the old budget_tokens form).
    if model.startswith(("claude-opus", "claude-sonnet-5", "claude-fable", "claude-mythos")):
        return {"type": "adaptive"}
    return anthropic.NOT_GIVEN


def _create(client, model: str, system: list[dict], tools: list[dict], messages: list[dict]):
    last_exc: Exception | None = None
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=tools,
                thinking=_thinking(model),
                messages=messages,
            )
        except anthropic.NotFoundError as exc:
            raise AgentError(f"model {model!r} not found for this API key") from exc
        except anthropic.RateLimitError as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
        except anthropic.APIStatusError as exc:
            raise AgentError(f"API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AgentError(f"could not reach the API: {exc}") from exc
    raise AgentError(f"rate limited after {_RATE_LIMIT_RETRIES} retries") from last_exc


def _final_text(content) -> str:
    return "".join(b.text for b in content if getattr(b, "type", "") == "text").strip()


def answer_question(question: str, *, source: str = "seattle_energy") -> AnswerResult:
    settings = get_settings()
    if not settings.ANTHROPIC_API_KEY:
        raise AgentError("ANTHROPIC_API_KEY is not set — add it to .env")

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    system = build_system_prompt(source)
    tools = tool_schemas(source)
    messages: list[dict] = [{"role": "user", "content": question}]
    trace = Trace(question=question, model=settings.ANALYST_MODEL)
    ctx = RunContext()

    stopped = "max_iters"
    answer = ""
    for _ in range(settings.MAX_AGENT_ITERS):
        resp = _create(client, settings.ANALYST_MODEL, system, tools, messages)
        trace.record_response(resp)

        if resp.stop_reason == "refusal":
            stopped, answer = "refusal", "The model declined to answer this question."
            break
        if resp.stop_reason != "tool_use":
            stopped, answer = "end_turn", _final_text(resp.content)
            break

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            t0 = time.monotonic()
            tool_input = dict(block.input)
            payload, is_error = run_tool(block.name, tool_input, ctx)
            trace.record_tool(
                block.name, tool_input, payload, (time.monotonic() - t0) * 1000, is_error
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": payload,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": results})
    else:
        answer = "Reached the step limit before finishing. Partial trace below."

    trace_dict = trace.to_dict() | {"summary": trace.summary(), "stopped": stopped}
    trace.flush({"stopped": stopped, "charts": ctx.charts})
    return AnswerResult(
        answer=answer,
        sql=trace.sql_run(),
        charts=ctx.charts,
        steps=trace.steps,
        trace=trace_dict,
        stopped=stopped,
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # answers contain CO₂e, — etc.
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print('usage: python -m agent.loop "your question"', file=sys.stderr)
        return 2
    try:
        answer_question(" ".join(args)).print_cli()
    except AgentError as exc:
        print(f"agent error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
