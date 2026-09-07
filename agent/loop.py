"""The hand-written tool-use loop (milestone M4).

Multi-step by design. Each iteration: call the Claude API with the system prompt
+ tools + running message list; if ``stop_reason != "tool_use"`` finalize and
return the plain-English answer; otherwise execute *every* ``tool_use`` block,
append *all* ``tool_result`` blocks in a single user message, and continue.
``MAX_AGENT_ITERS`` (default 8) caps it; hitting the cap returns a best-effort
answer with a flag.

Model: ``ANALYST_MODEL`` env knob (``claude-opus-5`` local, ``claude-haiku-4-5``
for evals + the public demo), ``thinking={"type": "adaptive"}``, no prefill.
Prompt caching on ``tools`` + the schema-summary portion of ``system``.
"""

# TODO(M4): implement answer_question(question) -> AnswerResult.
