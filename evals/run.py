"""Evaluation runner (milestone M6).

Runs the agent over every item in ``questions.yaml``, grades with plain
assertions (plus an optional LLM judge for the free-text answer), and writes
pass rate, avg iterations, avg tokens, $ per question, and every failure with its
full trace to ``results/<timestamp>.json`` + a markdown summary.
"""

# TODO(M6): load questions.yaml, run agent.loop.answer_question, grade, report.
