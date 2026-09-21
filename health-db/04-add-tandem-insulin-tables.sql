-- Tandem pump insulin delivery data (bolus + basal), sourced from Tandem
-- Source's pump-logs report via the tandemdata CLI / tandemsync service.
--
-- Unlike health-db's other tables, these two are normalized rather than
-- generic-JSONB: tandemdata's own `events` table (tandemdb/migrations) keeps
-- the raw per-event-code payloads for every pump/CGM event type, but only a
-- handful of those codes represent insulin delivery, and each carries the
-- data split across several sibling events (bolus request/detail/carb/split
-- are all emitted together and completed later by a separate "completed"
-- event). These tables merge that into one row per bolus / basal-rate-change
-- so they're directly queryable without reassembling event sequences.
--
-- Safe to run multiple times (idempotent), matching the pattern in
-- 03-add-insulin-table.sql, so an already-running instance can pick this up
-- without a reset; a fresh database gets it the same way via
-- docker-entrypoint-initdb.d.

do $$
begin
    if not exists (select 1 from pg_type where typname = 'tandem_bolus_type') then
        create type tandem_bolus_type as ENUM ('STANDARD', 'EXTENDED');
    end if;
end
$$;

-- One row per bolus (keyed by the pump's bolusId), combining the
-- BolusRequested* events (eventCode 55/59/64/65/66) with the matching
-- BolusCompleted event (eventCode 20/21). A bolus that has been requested
-- but not yet completed (still in flight, or an incomplete/cancelled
-- delivery) has completed_at / insulin_delivered left null;
-- completion_status distinguishes a completed delivery (3) from other
-- outcomes reported by the pump.
--
-- A bolus is often a mix of a food (carb-covering) component and a
-- correction (high-BG-covering) component delivered together as one dose;
-- food_bolus_size + correction_bolus_size sum to insulin_requested
-- (eventCode 66, BolusRequestedSplit). correction_included and carb_ratio
-- come from eventCode 64 (BolusRequestedCarb) and explain why a correction
-- was applied.
create table if not exists tandem_bolus
(
    id                    bigserial primary key,
    device_assignment_id  text not null,
    bolus_id              bigint not null,
    bolus_type            tandem_bolus_type,
    requested_at          timestamptz,
    completed_at          timestamptz,
    insulin_requested     real,
    insulin_delivered     real,
    food_bolus_size       real,
    correction_bolus_size real,
    correction_included   boolean,
    carb_amount           real,
    carb_ratio            real,
    bg                    real,
    completion_status     smallint,
    event_properties      jsonb not null default '{}'::jsonb,
    unique (device_assignment_id, bolus_id)
);

-- Adds the food/correction split columns for a tandem_bolus table created by
-- an earlier version of this file, before they existed. Safe to run
-- multiple times and safe on a fresh table (all no-ops there).
alter table tandem_bolus add column if not exists food_bolus_size real;
alter table tandem_bolus add column if not exists correction_bolus_size real;
alter table tandem_bolus add column if not exists correction_included boolean;
alter table tandem_bolus add column if not exists carb_ratio real;

comment on table tandem_bolus is 'Tandem pump bolus deliveries, merged from BolusRequested*/BolusCompleted pump-log events';
comment on column tandem_bolus.bolus_id is 'Pump-assigned bolus id (eventProperties.bolusId), unique per device_assignment_id';
comment on column tandem_bolus.requested_at is 'estimatedDateTime of the BolusRequested* event';
comment on column tandem_bolus.completed_at is 'estimatedDateTime of the BolusCompleted event';
comment on column tandem_bolus.insulin_requested is 'Units of insulin requested (eventProperties.insulinRequested / bolusSize / totalBolusSize)';
comment on column tandem_bolus.insulin_delivered is 'Units of insulin actually delivered (eventProperties.insulinDelivered), null until completed';
comment on column tandem_bolus.food_bolus_size is 'Portion of insulin_requested covering carbs (eventProperties.foodBolusSize)';
comment on column tandem_bolus.correction_bolus_size is 'Portion of insulin_requested covering high BG (eventProperties.correctionBolusSize)';
comment on column tandem_bolus.correction_included is 'Whether a correction was applied at all (eventProperties.correctionBolusIncluded)';
comment on column tandem_bolus.carb_ratio is 'Carb ratio used for this bolus (eventProperties.carbRatio)';
comment on column tandem_bolus.completion_status is 'Raw completionStatus from the BolusCompleted event (3 = completed normally)';
comment on column tandem_bolus.event_properties is 'Raw eventProperties merged from the contributing events, for fields not broken out into columns';

create index if not exists idx_tandem_bolus_requested_at on tandem_bolus (requested_at);
create index if not exists idx_tandem_bolus_properties on tandem_bolus using GIN (event_properties);

-- One row per basal rate change (eventCode 3, BasalRateChange), which is
-- what actually drives insulin delivery between boluses. commanded_rate is
-- normalized to U/hr (the pump reports it directly in U/hr for this event
-- code, unlike the milli-units/hr used by eventCode 279/BasalRateDelivered).
create table if not exists tandem_basal
(
    id                    bigserial primary key,
    device_assignment_id  text not null,
    sequence_group        integer not null,
    sequence_number       integer not null,
    changed_at            timestamptz not null,
    commanded_rate        real not null,
    base_rate             real,
    max_rate              real,
    event_properties      jsonb not null default '{}'::jsonb,
    unique (device_assignment_id, sequence_group, sequence_number)
);

comment on table tandem_basal is 'Tandem pump basal rate changes (eventCode 3 / BasalRateChange), rate in U/hr';
comment on column tandem_basal.changed_at is 'estimatedDateTime of the BasalRateChange event';
comment on column tandem_basal.commanded_rate is 'New commanded basal rate in U/hr (eventProperties.commandedBasalRate)';
comment on column tandem_basal.base_rate is 'Profile base basal rate in U/hr (eventProperties.baseBasalRate)';
comment on column tandem_basal.max_rate is 'Profile max basal rate in U/hr (eventProperties.maxBasalRate)';

create index if not exists idx_tandem_basal_changed_at on tandem_basal (changed_at);

-- Convenience view: every insulin delivery (bolus + basal-implied) as one
-- timeline, similar in spirit to the mysugr/insulin tables already in this
-- database, for dashboards that just want "how much insulin, when".
drop view if exists tandem_insulin_timeline;
create view tandem_insulin_timeline as
select
    'bolus'::text as source,
    device_assignment_id,
    coalesce(completed_at, requested_at) as event_time,
    coalesce(insulin_delivered, insulin_requested) as insulin_units,
    bolus_type::text as detail
from tandem_bolus
where coalesce(insulin_delivered, insulin_requested) is not null
order by event_time desc;

comment on view tandem_insulin_timeline is 'Bolus insulin deliveries as a flat timeline (basal is a continuous rate, not a discrete dose, so it is not included here — see tandem_basal)';
