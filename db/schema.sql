-- analytics-agent storage schema (architecture doc section 4.3).
-- Engine: SQLite everywhere (decision 2026-09-07 — see the doc's section 7
-- amendment). The same file is used read/write by ingestion and, opened with
-- PRAGMA query_only, read-only by the agent. Applied by db/migrate.py.
--
-- Source: Seattle "Building Energy Benchmarking Data, 2015-Present"
--   https://data.seattle.gov/resource/teqw-tu6e.json   (~38.3k rows, 2015-2025)
-- Column names below are OUR names; the source-column mapping lives in
-- sources/seattle_energy.py (field_map). Verified against the live portal M1.

-- One row per loaded dataset, so list_datasets / describe_schema have something
-- to read.
CREATE TABLE IF NOT EXISTS datasets (
  dataset_key       TEXT NOT NULL PRIMARY KEY,
  title             TEXT NOT NULL,
  description       TEXT,
  source_url        TEXT,
  row_count         INTEGER,
  first_year        INTEGER,
  last_year         INTEGER,
  last_ingested_at  TEXT               -- ISO-8601 UTC
);

-- One row per building. Attributes that are conceptually static; where the
-- source reports them per-year (e.g. epapropertytype) ingest keeps the most
-- recent non-null value.
CREATE TABLE IF NOT EXISTS buildings (
  ose_building_id       TEXT NOT NULL PRIMARY KEY,  -- source: osebuildingid (text upstream)
  building_name         TEXT,
  building_type         TEXT,                       -- source: buildingtype
  primary_property_type TEXT,                       -- source: epapropertytype (>50% of floor area)
  tax_parcel_id         TEXT,                       -- source: taxparcelidentificationnumber (permits join, v2)
  address               TEXT,
  city                  TEXT,
  state                 TEXT,
  zip_code              TEXT,
  neighborhood          TEXT,
  council_district      INTEGER,                    -- source: councildistrictcode
  year_built            INTEGER,                    -- source: yearbuilt (text upstream -> int)
  number_of_floors      INTEGER,                    -- source: numberoffloors
  number_of_buildings   INTEGER,                    -- source: numberofbuildings
  gfa_total             REAL,                       -- source: propertygfatotal (sq ft)
  gfa_parking           REAL,                       -- source: propertygfaparking (sq ft)
  latitude              REAL,
  longitude             REAL
);

-- One row per building per year. UNIQUE (ose_building_id, data_year) is the
-- upsert / ON CONFLICT key.
CREATE TABLE IF NOT EXISTS energy_records (
  id                       INTEGER PRIMARY KEY,
  ose_building_id          TEXT NOT NULL REFERENCES buildings (ose_building_id),
  data_year                INTEGER NOT NULL,        -- source: datayear (text upstream -> int)
  site_eui                 REAL,                    -- source: siteeui_kbtu_sf
  site_eui_wn              REAL,                    -- source: siteeuiwn_kbtu_sf
  source_eui               REAL,                    -- source: sourceeui_kbtu_sf
  source_eui_wn            REAL,                    -- source: sourceeuiwn_kbtu_sf
  site_energy_use_kbtu     REAL,                    -- source: siteenergyuse_kbtu
  energy_star_score        INTEGER,                 -- source: energystarscore (null by design — eligible types only)
  electricity_kwh          REAL,
  electricity_kbtu         REAL,
  natural_gas_therms       REAL,
  natural_gas_kbtu         REAL,
  steam_kbtu               REAL,                    -- source: steamuse_kbtu
  total_ghg_emissions      REAL,                    -- source: totalghgemissions (metric tons CO2e)
  ghg_emissions_intensity  REAL,                    -- source: ghgemissionsintensity (kg CO2e/sf)
  compliance_status        TEXT,                    -- source: compliancestatus ('Compliant' | 'Not Compliant')
  compliance_issue         TEXT,                    -- source: complianceissue
  demolished               INTEGER,                 -- source: demolished (checkbox -> 0/1)
  UNIQUE (ose_building_id, data_year)
);

CREATE INDEX IF NOT EXISTS ix_energy_year ON energy_records (data_year);
CREATE INDEX IF NOT EXISTS ix_energy_building ON energy_records (ose_building_id);

-- Ingest audit log (architecture doc section 4.2).
CREATE TABLE IF NOT EXISTS ingestion_runs (
  id                INTEGER PRIMARY KEY,
  dataset_key       TEXT NOT NULL,
  started_at        TEXT NOT NULL,     -- ISO-8601 UTC
  finished_at       TEXT,
  rows_upserted     INTEGER,
  source_row_count  INTEGER,
  status            TEXT NOT NULL DEFAULT 'running',  -- running | success | error
  message           TEXT
);

CREATE INDEX IF NOT EXISTS ix_runs_dataset ON ingestion_runs (dataset_key, started_at);

-- ---------------------------------------------------------------------------
-- Source: SPD Crime Data (NIBRS), "SPD Crime Data: 2008-Present"
--   https://data.seattle.gov/resource/tazs-3rd5.json   (~1.56M rows total)
-- The second dataset (M9), proving the sources/ plug-in abstraction: one flat
-- table, no slowly-changing dimension, a datetime instead of a year, no model.
-- Column names below are OUR names; mapping lives in sources/seattle_crime.py.
-- Loaded as a recent slice (`ingest.run --source seattle_crime --since 2024`).
CREATE TABLE IF NOT EXISTS crime_incidents (
  offense_id           TEXT NOT NULL PRIMARY KEY,  -- source: offense_id (one offense within a report)
  report_number        TEXT,                       -- source: report_number (a report can have many offenses)
  report_datetime      TEXT,                       -- source: report_date_time (ISO-8601 local)
  report_year          INTEGER,                    -- derived from report_datetime, for range + grouping
  offense_datetime     TEXT,                       -- source: offense_date (when it occurred; may precede the report)
  group_a_b            TEXT,                       -- source: nibrs_group_a_b ('A' serious / 'B' less serious)
  crime_against        TEXT,                       -- source: nibrs_crime_against_category (PERSON | PROPERTY | SOCIETY | ...)
  offense_category     TEXT,                       -- source: offense_category (VIOLENT CRIME | PROPERTY CRIME | ALL OTHER)
  offense_sub_category TEXT,                       -- source: offense_sub_category (e.g. LARCENY-THEFT, ASSAULT OFFENSES)
  offense_code         TEXT,                       -- source: nibrs_offense_code (e.g. '13B')
  offense_description  TEXT,                       -- source: nibrs_offense_code_description (e.g. 'Simple Assault')
  shooting_type        TEXT,                       -- source: shooting_type_group (null unless a shooting incident)
  block_address        TEXT,                       -- source: block_address (anonymised to the block / intersection)
  precinct             TEXT,                       -- North | East | South | West | Southwest
  sector               TEXT,
  beat                 TEXT,
  neighborhood         TEXT,                       -- source: neighborhood (MCPP name, UPPERCASE)
  reporting_area       TEXT,
  latitude             REAL,                       -- source: latitude (sentinels -1.0 / 'REDACTED' -> NULL)
  longitude            REAL
);

CREATE INDEX IF NOT EXISTS ix_crime_year ON crime_incidents (report_year);
CREATE INDEX IF NOT EXISTS ix_crime_category ON crime_incidents (offense_category);
CREATE INDEX IF NOT EXISTS ix_crime_precinct ON crime_incidents (precinct);

-- permits (v2, deferred) — Building Permits dataset joined on tax_parcel_id /
-- address, for "did this building renovate, and did efficiency change after?"
