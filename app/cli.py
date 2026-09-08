"""click CLI (architecture doc §4.6).

    python -m app.cli ask "which property types emit the most per sq ft?"

Prints the answer, the SQL the agent ran, any chart paths, and a one-line trace
summary (``4 turns · 2 tool calls (2 SQL) · 18.2k tok · $0.11 · 6.4s``).
``--json`` dumps the full structured result instead.

Thin by design: it is a wrapper over ``agent.loop.answer_question`` — the same
boundary the Flask API and the eval harness call.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import sys

import click

from agent.loop import AgentError, answer_question


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # answers contain CO₂e, —


@click.group()
def analyst() -> None:
    """Ask the Seattle building-energy analyst agent a question."""


@analyst.command()
@click.argument("question", nargs=-1, required=True)
@click.option("--source", default="seattle_energy", show_default=True, help="Dataset source key.")
@click.option("--json", "as_json", is_flag=True, help="Emit the full result as JSON.")
def ask(question: tuple[str, ...], source: str, as_json: bool) -> None:
    """Answer QUESTION, printing the answer, SQL, charts, and a trace summary."""
    _utf8_console()
    text = " ".join(question).strip()
    if not text:
        raise click.UsageError("question is empty")

    try:
        result = answer_question(text, source=source)
    except AgentError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        payload = dataclasses.asdict(result)
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        result.print_cli()

    if result.stopped == "max_iters":
        sys.exit(1)


if __name__ == "__main__":
    analyst()
