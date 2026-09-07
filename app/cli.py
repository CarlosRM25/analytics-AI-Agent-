"""click CLI (milestone M6).

``analyst ask "which property types emit the most per sq ft?"`` prints the
answer, the SQL the agent ran, any chart paths, and a one-line trace summary.
``--json`` dumps the full trace.
"""

# TODO(M6): click group with an `ask` command wrapping agent.loop.answer_question.
