"""SPD Crime Data (NIBRS) — the second source spec (milestone M9).

Resource ``tazs-3rd5`` on data.seattle.gov ("SPD Crime Data: 2008-Present"),
verified 2026-09-08: **~1,559,208 rows**, one row per offense within a police
report, 20 source columns, ``report_date_time`` back to 2008. We load a recent
slice — ``python -m ingest.run --source seattle_crime --since 2024`` (~214k rows).

This is the "datasets are plug-ins" proof: it is one **flat** table (no
slowly-changing dimension), keyed by a **datetime** not a year, with no model
behind ``predict``. The only ingestion change it needed was a handful of new
optional ``SourceSpec`` fields (``time_column`` / ``time_is_datetime`` /
``year_column`` / ``null_tokens``, and ``static_table=None``). The agent, the
catalog machinery, and the interface are untouched.

Sentinels: this source uses ``-`` / ``REDACTED`` / ``FK ERROR`` for missing
values (energy used ``""`` / ``NA``); ``null_tokens`` on the spec handles them.
Lat/long carry ``-1.0`` (and occasionally ``0.0``) when unknown -> a transform
nulls those.
"""

from __future__ import annotations

from sources.base import FieldMap, SourceSpec


def _real_coord(v: object) -> object | None:
    return None if v in (-1.0, 0.0) else v


FIELD_MAP: dict[str, FieldMap | tuple[FieldMap, ...]] = {
    "offense_id": FieldMap("crime_incidents", "offense_id", "str"),
    "report_number": FieldMap("crime_incidents", "report_number", "str"),
    # report_date_time feeds the ISO string and a derived integer year
    "report_date_time": (
        FieldMap("crime_incidents", "report_datetime", "str"),
        FieldMap("crime_incidents", "report_year", "year"),
    ),
    "offense_date": FieldMap("crime_incidents", "offense_datetime", "str"),
    "nibrs_group_a_b": FieldMap("crime_incidents", "group_a_b", "str"),
    "nibrs_crime_against_category": FieldMap("crime_incidents", "crime_against", "str"),
    "offense_category": FieldMap("crime_incidents", "offense_category", "str"),
    "offense_sub_category": FieldMap("crime_incidents", "offense_sub_category", "str"),
    "nibrs_offense_code": FieldMap("crime_incidents", "offense_code", "str"),
    "nibrs_offense_code_description": FieldMap("crime_incidents", "offense_description", "str"),
    "shooting_type_group": FieldMap("crime_incidents", "shooting_type", "str"),
    "block_address": FieldMap("crime_incidents", "block_address", "str"),
    "precinct": FieldMap("crime_incidents", "precinct", "str"),
    "sector": FieldMap("crime_incidents", "sector", "str"),
    "beat": FieldMap("crime_incidents", "beat", "str"),
    "neighborhood": FieldMap("crime_incidents", "neighborhood", "str"),
    "reporting_area": FieldMap("crime_incidents", "reporting_area", "str"),
    "latitude": FieldMap("crime_incidents", "latitude", "float", transform=_real_coord),
    "longitude": FieldMap("crime_incidents", "longitude", "float", transform=_real_coord),
}

SPEC = SourceSpec(
    key="seattle_crime",
    socrata_domain="data.seattle.gov",
    resource_id="tazs-3rd5",
    app_token_env="SOCRATA_APP_TOKEN",
    page_size=5000,
    field_map=FIELD_MAP,
    target_tables=("crime_incidents",),
    upsert_keys={"crime_incidents": ("offense_id",)},
    catalog_path="catalog/seattle_crime.yaml",
    model_module=None,  # no predict tool for this source
    refresh="daily",  # documentation only; ingestion is still a batch job
    time_column="report_date_time",
    time_is_datetime=True,
    year_column="report_year",
    null_tokens=("-", "REDACTED", "FK ERROR", "<Null>"),
)
