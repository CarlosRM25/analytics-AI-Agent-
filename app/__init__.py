"""Thin interface over the agent core: a click CLI and one Flask blueprint.

Everything routes through ``agent.loop.answer_question(q) -> AnswerResult`` so the
interface stays swappable. ``limits`` and ``cache`` are middleware used only by
the public deployment (architecture doc section 10).
"""
