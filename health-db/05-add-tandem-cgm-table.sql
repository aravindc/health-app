-- Tandem CGM readings (eventCode 399, CGMReading), sourced from Tandem
-- Source's pump-logs report via the tandemdata CLI / tandemsync service,
-- same as tandem_bolus / tandem_basal (04-add-tandem-insulin-tables.sql).
--
-- Kept in its own table rather than merged into nightscoutdb/ns_part (the
-- table health-sync/health-mongo-sync write Dexcom-sourced readings into,
-- and every existing chart/TIR/GMI query reads from). The pump and the CGM
-- transmitter both report glucose from the same underlying Dexcom sensor,
-- so this is *expected* to be a near-duplicate of nightscoutdb — the point
-- of capturing it is as a fallback: if the Dexcom Share sync has a gap
-- (missed poll, outage, session issue), this table may have a reading for
-- the same moment that nightscoutdb doesn't. Reconciling/gap-filling into
-- nightscoutdb from here is a deliberate separate step (not automatic),
-- so a disagreement between the two sources for the same timestamp is
-- never silently written over what health-sync already recorded.
--
-- Safe to run multiple times (idempotent), matching 04's pattern.
--
-- trend is a plain int, not the valid_trend ENUM — nightscoutdb/ns_part.trend
-- (the column this is meant to line up with) is itself a plain int, storing
-- health-sync/common.TrendToDirection's 0-9/99 scheme (0=NONE, 1=DoubleUp,
-- 2=SingleUp, 3=FortyFiveUp, 4=Flat, 5=FortyFiveDown, 6=SingleDown,
-- 7=DoubleDown, 8=NotComputable, 9=RATE OUT OF RANGE, 99=unknown). The
-- valid_trend ENUM in this schema is only used by the separate, apparently
-- unused `sugarmate` table.

create table if not exists tandem_cgm
(
    id                    bigserial primary key,
    device_assignment_id  text not null,
    sequence_group        integer not null,
    sequence_number       integer not null,
    reading_at            timestamptz not null,
    sgv                   real not null,
    trend                 integer,
    rate                  real,
    glucose_value_status  smallint,
    event_properties      jsonb not null default '{}'::jsonb,
    unique (device_assignment_id, sequence_group, sequence_number)
);

comment on table tandem_cgm is 'Tandem-pump-reported CGM readings (eventCode 399), kept separately from nightscoutdb as a gap-fill fallback source, not merged automatically';
comment on column tandem_cgm.reading_at is 'estimatedDateTime of the CGMReading event';
comment on column tandem_cgm.sgv is 'Glucose value in mg/dL (eventProperties.currentGlucoseDisplayValue)';
comment on column tandem_cgm.trend is 'Direction bucketed from eventProperties.rate (mg/dL per 5 min) into the same 0-9/99 scheme as nightscoutdb/ns_part.trend (see health-sync/common.TrendToDirection)';
comment on column tandem_cgm.rate is 'Raw rate of change in mg/dL per 5 minutes, as reported by the pump';
comment on column tandem_cgm.glucose_value_status is 'Raw glucoseValueStatus from the pump; nonzero values include out-of-sensor-range readings (e.g. LOW clamped to a display floor) — treat as suspect/informational, not a lab-accurate value';

create index if not exists idx_tandem_cgm_reading_at on tandem_cgm (reading_at);
create index if not exists idx_tandem_cgm_properties on tandem_cgm using GIN (event_properties);
