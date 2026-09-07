-- analytics-agent storage schema (architecture doc section 4.3).
-- Engine: MySQL 8 locally; SQLite (baked, read-only) in the deployed demo — keep
-- this DDL ANSI-ish. Applied by db/migrate.py (numbered files in db/migrations/).
--
-- Source: Seattle "Building Energy Benchmarking Data, 2015-Present"
--   https://data.seattle.gov/resource/teqw-tu6e.json   (~38.3k rows, 2015-2025)
-- Column names below are OUR names; the source-column mapping lives in
-- sources/seattle_energy.py (field_map). Verified against the live portal M1.

-- One row per loaded dataset, so list_datasets / describe_schema have something
-- to read.
CREATE TABLE IF NOT EXISTS datasets (
  dataset_key       VARCHAR(64)   NOT NULL,
  title             VARCHAR(255)  NOT NULL,
  description       TEXT          NULL,
  source_url        VARCHAR(512)  NULL,
  row_count         INT UNSIGNED  NULL,
  first_year        SMALLINT      NULL,
  last_year         SMALLINT      NULL,
  last_ingested_at  DATETIME      NULL,
  PRIMARY KEY (dataset_key)
);

-- One row per building. Attributes that are conceptually static; where the
-- source reports them per-year (e.g. epapropertytype), ingest takes the most
-- recent non-null value. TODO(M1): revisit if property type varies enough
-- year-to-year to belong in energy_records instead.
CREATE TABLE IF NOT EXISTS buildings (
  ose_building_id       VARCHAR(16)    NOT NULL,   -- source: osebuildingid (text upstream)
  building_name         VARCHAR(255)   NULL,
  building_type         VARCHAR(128)   NULL,       -- source: buildingtype
  primary_property_type VARCHAR(128)   NULL,       -- source: epapropertytype
  tax_parcel_id         VARCHAR(32)    NULL,       -- source: taxparcelidentificationnumber (permits join, v2)
  address               VARCHAR(255)   NULL,
  city                  VARCHAR(64)    NULL,
  state                 VARCHAR(16)    NULL,
  zip_code              VARCHAR(16)    NULL,
  neighborhood          VARCHAR(128)   NULL,
  council_district      TINYINT        NULL,       -- source: councildistrictcode
  year_built            SMALLINT       NULL,       -- source: yearbuilt (text upstream -> int)
  number_of_floors      SMALLINT       NULL,       -- source: numberoffloors
  number_of_buildings   SMALLINT       NULL,       -- source: numberofbuildings
  gfa_total             DOUBLE         NULL,       -- source: propertygfatotal (sq ft)
  gfa_parking           DOUBLE         NULL,       -- source: propertygfaparking (sq ft)
  latitude              DECIMAL(9,6)   NULL,
  longitude             DECIMAL(9,6)   NULL,
  PRIMARY KEY (ose_building_id)
);

-- One row per building per year. UNIQUE (ose_building_id, data_year) is the
-- upsert key for idempotent re-ingest.
CREATE TABLE IF NOT EXISTS energy_records (
  id                       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  ose_building_id          VARCHAR(16)     NOT NULL,
  data_year                SMALLINT        NOT NULL,   -- source: datayear (text upstream -> int)
  site_eui                 DOUBLE          NULL,       -- source: siteeui_kbtu_sf
  site_eui_wn              DOUBLE          NULL,       -- source: siteeuiwn_kbtu_sf
  source_eui               DOUBLE          NULL,       -- source: sourceeui_kbtu_sf
  source_eui_wn            DOUBLE          NULL,       -- source: sourceeuiwn_kbtu_sf
  site_energy_use_kbtu     DOUBLE          NULL,       -- source: siteenergyuse_kbtu
  energy_star_score        SMALLINT        NULL,       -- source: energystarscore (nullable by design — eligible types only)
  electricity_kwh          DOUBLE          NULL,
  electricity_kbtu         DOUBLE          NULL,
  natural_gas_therms       DOUBLE          NULL,
  natural_gas_kbtu         DOUBLE          NULL,
  steam_kbtu               DOUBLE          NULL,       -- source: steamuse_kbtu
  total_ghg_emissions      DOUBLE          NULL,       -- source: totalghgemissions (metric tons CO2e)
  ghg_emissions_intensity  DOUBLE          NULL,       -- source: ghgemissionsintensity (kg CO2e/sf)
  compliance_status        VARCHAR(64)     NULL,       -- source: compliancestatus
  compliance_issue         VARCHAR(255)    NULL,       -- source: complianceissue
  demolished               TINYINT(1)      NULL,       -- source: demolished (checkbox)
  PRIMARY KEY (id),
  UNIQUE KEY uq_building_year (ose_building_id, data_year),
  KEY ix_energy_year (data_year),
  KEY ix_energy_building (ose_building_id),
  CONSTRAINT fk_energy_building FOREIGN KEY (ose_building_id)
    REFERENCES buildings (ose_building_id)
);

-- Ingest audit log (architecture doc section 4.2).
CREATE TABLE IF NOT EXISTS ingestion_runs (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  dataset_key       VARCHAR(64)     NOT NULL,
  started_at        DATETIME        NOT NULL,
  finished_at       DATETIME        NULL,
  rows_upserted     INT UNSIGNED    NULL,
  source_row_count  INT UNSIGNED    NULL,
  status            VARCHAR(16)     NOT NULL DEFAULT 'running',  -- running | success | error
  message           TEXT            NULL,
  PRIMARY KEY (id),
  KEY ix_runs_dataset (dataset_key, started_at)
);

-- permits (v2, deferred) — Building Permits dataset joined on tax_parcel_id /
-- address, for "did this building renovate, and did efficiency change after?"
