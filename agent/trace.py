"""Per-run structured trace (architecture doc §4.5 / §4.8).

One ``Trace`` per question: records every model turn and tool call, tallies
tokens and estimated cost, and flushes a single JSON line to
``logs/runs.jsonl``. ``summary()`` is the CLI one-liner.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_LOG = Path(__file__).resolve().parents[1] / "logs" / "runs.jsonl"

# USD per input / output MTok, and the cache multipliers (architecture doc §7 / skill table).
_RATES = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
}
_CACHE_READ_MULT = 0.1
_CACHE_WRITE_MULT = 1.25


def _price(model: str, usage) -> float:
    in_rate, out_rate = _RATES.get(model, _RATES["claude-opus-5"])
    fresh_in = getattr(usage, "input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return (
        fresh_in * in_rate
        + cache_read * in_rate * _CACHE_READ_MULT
        + cache_write * in_rate * _CACHE_WRITE_MULT
        + out * out_rate
    ) / 1_000_000


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


@dataclass
class Trace:
    question: str
    model: str
    started_at: str = field(default_factory=_now)
    _t0: float = field(default_factory=time.monotonic, repr=False)
    steps: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    model_turns: int = 0

    def record_response(self, resp) -> None:
        self.model_turns += 1
        u = resp.usage
        fresh = getattr(u, "input_tokens", 0) or 0
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        self.input_tokens += fresh + cache_read + cache_write
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_read_tokens += cache_read
        self.cache_creation_tokens += cache_write
        self.cost_usd += _price(self.model, u)
        self.steps.append(
            {
                "kind": "model",
                "stop_reason": resp.stop_reason,
                "output_tokens": getattr(u, "output_tokens", 0) or 0,
                "cache_read": cache_read,
                "cache_write": cache_write,
            }
        )

    def record_tool(
        self, name: str, tool_input: dict, result: str, ms: float, is_error: bool
    ) -> None:
        step = {"kind": "tool", "tool": name, "ms": round(ms, 1), "is_error": is_error}
        if name == "run_sql":
            step["sql"] = tool_input.get("sql", "")
        self.steps.append(step)

    # -- outputs ------------------------------------------------------------- #
    def sql_run(self) -> list[str]:
        return [s["sql"] for s in self.steps if s.get("kind") == "tool" and s.get("sql")]

    def latency_ms(self) -> float:
        return round((time.monotonic() - self._t0) * 1000, 1)

    def summary(self) -> str:
        n_tools = sum(1 for s in self.steps if s["kind"] == "tool")
        n_sql = len(self.sql_run())
        tok = (self.input_tokens + self.output_tokens) / 1000
        return (
            f"{self.model_turns} turns · {n_tools} tool calls ({n_sql} SQL) · "
            f"{tok:.1f}k tok · ${self.cost_usd:.4f} · {self.latency_ms() / 1000:.1f}s"
        )

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "model": self.model,
            "question": self.question,
            "model_turns": self.model_turns,
            "steps": self.steps,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": self.latency_ms(),
        }

    def flush(self, extra: dict | None = None) -> None:
        record = self.to_dict() | (extra or {})
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
