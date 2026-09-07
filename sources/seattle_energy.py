"""Seattle Building Energy Benchmarking — the first source spec (milestone M1).

Builds a ``SourceSpec`` for the dataset on data.seattle.gov and registers it.
The resource id and field names are confirmed against the live portal at M1
(architecture doc section 12 risk: "Socrata drift"); the v0.1 sketch assumed
resource id ``teqw-tu6e``.
"""

# TODO(M1): construct SourceSpec(...) with the confirmed resource_id + field_map
#           and call sources.base.register(spec).
