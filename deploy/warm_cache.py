"""Post-deploy cache warm (milestone M8).

Runs ``evals/questions.yaml`` plus a hand-listed set of likely recruiter
questions through the live endpoint once, so common questions are instant and
free from the first visitor (architecture doc section 10).
"""

# TODO(M8): iterate the question set against the deployed /ask URL.
