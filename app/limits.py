"""Rate limiting for the public demo (milestone M8, architecture doc section 10).

Per-visitor (hashed IP) daily cap and a global daily question cap, backed by
Redis counters ``ratelimit:<ip_hash>:<date>`` with a 24h TTL (``INCR`` +
``EXPIRE``). Cache hits do not count. On hit: a friendly message and a fall back
to the offline example gallery.
"""

# TODO(M8): check_and_increment(ip_hash) -> RateLimitDecision.
