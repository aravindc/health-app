"""DuckDB schema.

Layers:
  raw_*        exact copies of the Postgres source tables (append-only, never modified)
  clean tables built from raw on every ingest run (deterministic, rebuildable)
  dq_issues    every flagged or excluded record, so nothing is discarded silently

All timestamps are TIMESTAMPTZ in UTC.
"""

# Source table -> (raw table, SELECT list run on Postgres). Enums and jsonb are
# cast to text so they arrive unchanged.
RAW_SOURCES = {
    "tandem_cgm": (
        "raw_tandem_cgm",
        (
            "id, device_assignment_id, sequence_group, sequence_number, reading_at, sgv, trend, rate, "
            "glucose_value_status, event_properties::text AS event_properties"
        ),
    ),
    "tandem_basal": (
        "raw_tandem_basal",
        (
            "id, device_assignment_id, sequence_group, sequence_number, changed_at, commanded_rate, base_rate, "
            "max_rate, event_properties::text AS event_properties"
        ),
    ),
    "tandem_bolus": (
        "raw_tandem_bolus",
        (
            "id, device_assignment_id, bolus_id, bolus_type::text AS bolus_type, requested_at, completed_at, "
            "insulin_requested, insulin_delivered, food_bolus_size, correction_bolus_size, correction_included, "
            "carb_amount, carb_ratio, bg, completion_status, event_properties::text AS event_properties"
        ),
    ),
    "nightscoutdb": (
        "raw_nightscout",
        "id, sgv, ns_time, ns_datetime, trend, utcoffset, systime",
    ),
}

RAW_DDL = """
CREATE TABLE IF NOT EXISTS raw_tandem_cgm (
    id BIGINT PRIMARY KEY, device_assignment_id VARCHAR, sequence_group INTEGER, sequence_number INTEGER,
    reading_at TIMESTAMPTZ, sgv REAL, trend INTEGER, rate REAL, glucose_value_status SMALLINT,
    event_properties JSON, ingested_at TIMESTAMPTZ, ingest_batch VARCHAR);
CREATE TABLE IF NOT EXISTS raw_tandem_basal (
    id BIGINT PRIMARY KEY, device_assignment_id VARCHAR, sequence_group INTEGER, sequence_number INTEGER,
    changed_at TIMESTAMPTZ, commanded_rate REAL, base_rate REAL, max_rate REAL,
    event_properties JSON, ingested_at TIMESTAMPTZ, ingest_batch VARCHAR);
CREATE TABLE IF NOT EXISTS raw_tandem_bolus (
    id BIGINT PRIMARY KEY, device_assignment_id VARCHAR, bolus_id BIGINT, bolus_type VARCHAR,
    requested_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, insulin_requested REAL, insulin_delivered REAL,
    food_bolus_size REAL, correction_bolus_size REAL, correction_included BOOLEAN, carb_amount REAL,
    carb_ratio REAL, bg REAL, completion_status SMALLINT, event_properties JSON,
    ingested_at TIMESTAMPTZ, ingest_batch VARCHAR);
CREATE TABLE IF NOT EXISTS raw_nightscout (
    id INTEGER PRIMARY KEY, sgv BIGINT, ns_time BIGINT, ns_datetime TIMESTAMPTZ, trend INTEGER,
    utcoffset INTEGER, systime TIMESTAMPTZ, ingested_at TIMESTAMPTZ, ingest_batch VARCHAR);
CREATE TABLE IF NOT EXISTS ingest_runs (
    ingest_batch VARCHAR, source_table VARCHAR, mode VARCHAR, rows_added BIGINT, max_id BIGINT,
    started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ);
"""

CLEAN_DDL = """
CREATE OR REPLACE TABLE cgm_readings (
    reading_id VARCHAR PRIMARY KEY,     -- '<source>:<source_pk>'
    source VARCHAR NOT NULL,            -- 'tandem' | 'nightscout'
    source_pk BIGINT NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL,   -- when the sensor measured the value (backfills corrected)
    available_at TIMESTAMPTZ NOT NULL,  -- when the value became known to the system (leakage control)
    glucose_mmol DOUBLE,
    glucose_mgdl DOUBLE,                -- after censoring to sensor limits
    sgv_raw DOUBLE,                     -- value exactly as in the source
    censored VARCHAR,                   -- 'low' | 'high' | NULL
    trend_name VARCHAR,
    trend_ordinal SMALLINT,             -- +3 fastest rise ... -3 fastest fall
    rate_mmol_per_min DOUBLE,           -- device-reported rate (tandem only)
    is_backfill BOOLEAN NOT NULL,
    time_corrected BOOLEAN NOT NULL,    -- measured_at was derived from the pump clock offset
    is_suspicious BOOLEAN NOT NULL,
    exclude_reason VARCHAR              -- NULL = usable; otherwise why the row is excluded
);

-- Primary CGM series: tandem, with nightscout readings filling tandem gaps.
CREATE OR REPLACE TABLE cgm_merged (
    measured_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    glucose_mmol DOUBLE NOT NULL,
    censored VARCHAR,
    trend_ordinal SMALLINT,
    rate_mmol_per_min DOUBLE,
    source VARCHAR NOT NULL,
    reading_id VARCHAR NOT NULL
);

CREATE OR REPLACE TABLE boluses (
    bolus_pk BIGINT PRIMARY KEY,        -- raw_tandem_bolus.id
    bolus_id BIGINT,
    requested_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    delivery_type VARCHAR NOT NULL,     -- bolus | automated_correction | manual_correction | unknown
    is_automated BOOLEAN NOT NULL,
    insulin_type VARCHAR NOT NULL,
    delivery_shape VARCHAR NOT NULL,    -- standard | extended
    units_requested DOUBLE,
    units_delivered DOUBLE,
    immediate_units DOUBLE,
    extended_units DOUBLE,
    completion VARCHAR,
    carbs_g DOUBLE,
    carbs_repeat_entry BOOLEAN,         -- same carbs re-entered (cancel + re-enter); count the meal once
    carb_ratio_g_per_u DOUBLE,
    bg_mmol DOUBLE,
    isf_mmol_per_u DOUBLE,
    target_bg_mmol DOUBLE,
    pump_iob_u DOUBLE,
    extended_planned_minutes DOUBLE,    -- programmed duration of the extended part (known at request)
    standard_percent DOUBLE,            -- programmed share delivered immediately (100 for standard boluses)
    src_bolus_type VARCHAR,             -- original codes, kept for traceability
    src_json_bolus_type INTEGER,
    src_json_options INTEGER
);

CREATE OR REPLACE TABLE basal_segments (
    basal_pk BIGINT PRIMARY KEY,        -- raw_tandem_basal.id
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ,                 -- NULL for the latest, still-open segment
    duration_minutes DOUBLE,
    rate_u_per_h DOUBLE NOT NULL,
    base_rate_u_per_h DOUBLE,
    max_rate_u_per_h DOUBLE,
    units DOUBLE,                       -- NULL when the segment is a gap (delivery unknown)
    change_type VARCHAR,
    change_type_raw VARCHAR,
    profile_idp INTEGER,
    is_gap BOOLEAN NOT NULL
);

-- Every insulin delivery as an interval; point deliveries have start_at = end_at.
CREATE OR REPLACE TABLE insulin_deliveries (
    source_table VARCHAR NOT NULL,      -- 'boluses' | 'basal_segments'
    source_pk BIGINT NOT NULL,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    units DOUBLE NOT NULL,
    delivery_type VARCHAR NOT NULL,     -- basal | bolus | automated_correction | manual_correction | unknown
    basal_change_type VARCHAR,
    is_automated BOOLEAN NOT NULL,
    insulin_type VARCHAR NOT NULL
);

CREATE OR REPLACE TABLE carbs (
    bolus_pk BIGINT PRIMARY KEY,
    entered_at TIMESTAMPTZ NOT NULL,    -- carbs are entered with the bolus (requested_at)
    carbs_g DOUBLE NOT NULL
);

CREATE OR REPLACE TABLE dq_issues (
    source_table VARCHAR NOT NULL,
    source_pk BIGINT,
    issue_type VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,          -- info | warning | excluded
    occurred_at TIMESTAMPTZ,
    detail VARCHAR
);
"""
