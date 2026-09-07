"""Feature layer for the Seattle energy model (milestone M3).

Builds the model matrix from ``buildings`` + ``energy_records``: building age
(``data_year - year_built``), ``log(gfa_total)``, parking share,
``primary_property_type``, ``council_district``. Rows missing the target are
dropped; rows missing ``energy_star_score`` are kept, with a ``has_energy_star``
indicator if that column is used at all (architecture doc section 4.4).
"""

# TODO(M3): implement the feature builder + the leakage-aware train/valid/test split.
