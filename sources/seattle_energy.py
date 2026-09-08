"""Seattle Building Energy Benchmarking — the first source spec.

Resource ``teqw-tu6e`` on data.seattle.gov ("Building Energy Benchmarking Data,
2015-Present"), verified 2026-09-07: ~38,309 rows, 3,871 buildings, ``datayear``
2015-2025, 47 source columns.

Every source value arrives as a JSON **string** (``"2016"``, ``"47.61"``,
``"87"``) except ``demolished`` (a real bool). Coercion is by ``FieldMap.dtype``
— one of ``"str" | "int" | "float" | "bool"`` — applied generically in
``ingest/run.py`` before any per-field ``transform``. Empty string and the
literal ``"NA"`` both coerce to ``None``.

Registration is explicit via ``sources.base.load("seattle_energy")``; importing
this module has no side effects.
"""

from __future__ import annotations

from sources.base import FieldMap, SourceSpec

# source column -> FieldMap, or a tuple of them when one source column feeds
# several tables. Table order in target_tables (buildings, then energy_records)
# is the FK-safe ingest order.
FIELD_MAP: dict[str, FieldMap | tuple[FieldMap, ...]] = {
    # osebuildingid is the natural key of buildings and the FK on energy_records,
    # so it is written to both.
    "osebuildingid": (
        FieldMap("buildings", "ose_building_id", "str"),
        FieldMap("energy_records", "ose_building_id", "str"),
    ),
    # --- buildings (attributes treated as static; ingest keeps the most recent
    #     non-null value per building) ---
    "buildingname": FieldMap("buildings", "building_name", "str"),
    "buildingtype": FieldMap("buildings", "building_type", "str"),
    "epapropertytype": FieldMap("buildings", "primary_property_type", "str"),
    "taxparcelidentificationnumber": FieldMap("buildings", "tax_parcel_id", "str"),
    "address": FieldMap("buildings", "address", "str"),
    "city": FieldMap("buildings", "city", "str"),
    "state": FieldMap("buildings", "state", "str"),
    "zipcode": FieldMap("buildings", "zip_code", "str"),
    "neighborhood": FieldMap("buildings", "neighborhood", "str"),
    "councildistrictcode": FieldMap("buildings", "council_district", "int"),
    "yearbuilt": FieldMap("buildings", "year_built", "int"),
    "numberoffloors": FieldMap("buildings", "number_of_floors", "int"),
    "numberofbuildings": FieldMap("buildings", "number_of_buildings", "int"),
    "propertygfatotal": FieldMap("buildings", "gfa_total", "float"),
    "propertygfaparking": FieldMap("buildings", "gfa_parking", "float"),
    "latitude": FieldMap("buildings", "latitude", "float"),
    "longitude": FieldMap("buildings", "longitude", "float"),
    # --- energy_records (one row per building per year) ---
    "datayear": FieldMap("energy_records", "data_year", "int"),
    "siteeui_kbtu_sf": FieldMap("energy_records", "site_eui", "float"),
    "siteeuiwn_kbtu_sf": FieldMap("energy_records", "site_eui_wn", "float"),
    "sourceeui_kbtu_sf": FieldMap("energy_records", "source_eui", "float"),
    "sourceeuiwn_kbtu_sf": FieldMap("energy_records", "source_eui_wn", "float"),
    "siteenergyuse_kbtu": FieldMap("energy_records", "site_energy_use_kbtu", "float"),
    "energystarscore": FieldMap("energy_records", "energy_star_score", "int"),
    "electricity_kwh": FieldMap("energy_records", "electricity_kwh", "float"),
    "electricity_kbtu": FieldMap("energy_records", "electricity_kbtu", "float"),
    "naturalgas_therms": FieldMap("energy_records", "natural_gas_therms", "float"),
    "naturalgas_kbtu": FieldMap("energy_records", "natural_gas_kbtu", "float"),
    "steamuse_kbtu": FieldMap("energy_records", "steam_kbtu", "float"),
    "totalghgemissions": FieldMap("energy_records", "total_ghg_emissions", "float"),
    "ghgemissionsintensity": FieldMap("energy_records", "ghg_emissions_intensity", "float"),
    "compliancestatus": FieldMap("energy_records", "compliance_status", "str"),
    "complianceissue": FieldMap("energy_records", "compliance_issue", "str"),
    "demolished": FieldMap("energy_records", "demolished", "bool"),
}

SPEC = SourceSpec(
    key="seattle_energy",
    socrata_domain="data.seattle.gov",
    resource_id="teqw-tu6e",
    app_token_env="SOCRATA_APP_TOKEN",
    page_size=5000,
    field_map=FIELD_MAP,
    target_tables=("buildings", "energy_records"),
    upsert_keys={
        "buildings": ("ose_building_id",),
        "energy_records": ("ose_building_id", "data_year"),
    },
    catalog_path="catalog/seattle_energy.yaml",
    model_module="model.seattle_energy",
    refresh="annual",
    # buildings is a slowly-changing dimension: keep the latest non-null
    # attribute per building, ordered by the source's datayear.
    static_table="buildings",
    static_sort_key="datayear",
    # time_column="datayear", year_column="data_year" are the defaults
)
