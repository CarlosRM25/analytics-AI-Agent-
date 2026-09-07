"""Response cache for the public demo (milestone M8, architecture doc section 10).

Key: ``sha256(normalize(question) · dataset · model)`` where ``normalize``
lowercases, trims, and collapses whitespace. Value: the full
``{answer, sql, chart, steps}`` payload, TTL 30 days. Exact-match to start; the
interface stays the same if this later becomes an embedding nearest-neighbour
lookup.
"""

# TODO(M8): get(question) / put(question, payload) over Redis.
