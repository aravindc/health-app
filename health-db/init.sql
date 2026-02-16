drop table if exists sugarmate;
drop type if exists valid_trend;

create type valid_trend as ENUM ('DOUBLE_UP', 'SINGLE_UP', 'FLAT', 'FORTY_FIVE_UP','FORTY_FIVE_DOWN','SINGLE_DOWN','DOUBLE_DOWN', 'NOT_COMPUTABLE','NONE' );
create table sugarmate
(
    id serial primary key ,
    val int not null ,
    trend valid_trend,
    trend_symbol varchar,
    trend_id int,
    source_type_id int,
    created_at timestamptz,
    time bigint unique
);

drop type if exists insulin_types;
create type insulin_types as ENUM('LONG_ACTING', 'RAPID_ACTING');

drop table if exists insulin
(
    id serial primary key,
    date_utc_millis bigint not null unique,
    date_utc timestampz,
    insulin_type insulin_types,
    insulin_qty real not null
)

drop table if exists public.nightscoutdb;
create table public.nightscoutdb
(
    id serial,
    sgv          bigint,
    ns_time bigint,
    ns_datetime timestamptz,
    trend        int,
    utcoffset  int,
    systime    timestamptz,
) partition by range (ns_datetime);

create index idx_ns_datetime on public.nightscoutdb (ns_datetime);

CREATE TABLE NS_PART_2021_04 PARTITION OF nightscoutdb FOR VALUES FROM ('2021-04-01T00:00:00') TO ('2021-05-01T00:00:00');
CREATE TABLE NS_PART_2021_05 PARTITION OF nightscoutdb FOR VALUES FROM ('2021-05-01T00:00:00') TO ('2021-06-01T00:00:00');
CREATE TABLE NS_PART_2021_06 PARTITION OF nightscoutdb FOR VALUES FROM ('2021-06-01T00:00:00') TO ('2021-07-01T00:00:00');
CREATE TABLE NS_PART_2021_07 PARTITION OF nightscoutdb FOR VALUES FROM ('2021-07-01T00:00:00') TO ('2021-08-01T00:00:00');
CREATE TABLE NS_PART_2021_08 PARTITION OF nightscoutdb FOR VALUES FROM ('2021-08-01T00:00:00') TO ('2021-09-01T00:00:00');
CREATE TABLE NS_PART_2021_09 PARTITION OF nightscoutdb FOR VALUES FROM ('2021-09-01T00:00:00') TO ('2021-10-01T00:00:00');
CREATE TABLE NS_PART_2021_10 PARTITION OF nightscoutdb FOR VALUES FROM ('2021-10-01T00:00:00') TO ('2021-11-01T00:00:00');
CREATE TABLE NS_PART_2021_11 PARTITION OF nightscoutdb FOR VALUES FROM ('2021-11-01T00:00:00') TO ('2021-12-01T00:00:00');
CREATE TABLE NS_PART_2021_12 PARTITION OF nightscoutdb FOR VALUES FROM ('2021-12-01T00:00:00') TO ('2022-01-01T00:00:00');
CREATE TABLE NS_PART_2022_01 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-01-01T00:00:00') TO ('2022-02-01T00:00:00');
CREATE TABLE NS_PART_2022_02 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-02-01T00:00:00') TO ('2022-03-01T00:00:00');
CREATE TABLE NS_PART_2022_03 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-03-01T00:00:00') TO ('2022-04-01T00:00:00');
CREATE TABLE NS_PART_2022_04 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-04-01T00:00:00') TO ('2022-05-01T00:00:00');
CREATE TABLE NS_PART_2022_05 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-05-01T00:00:00') TO ('2022-06-01T00:00:00');
CREATE TABLE NS_PART_2022_06 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-06-01T00:00:00') TO ('2022-07-01T00:00:00');
CREATE TABLE NS_PART_2022_07 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-07-01T00:00:00') TO ('2022-08-01T00:00:00');
CREATE TABLE NS_PART_2022_08 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-08-01T00:00:00') TO ('2022-09-01T00:00:00');
CREATE TABLE NS_PART_2022_09 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-09-01T00:00:00') TO ('2022-10-01T00:00:00');
CREATE TABLE NS_PART_2022_10 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-10-01T00:00:00') TO ('2022-11-01T00:00:00');
CREATE TABLE NS_PART_2022_11 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-11-01T00:00:00') TO ('2022-12-01T00:00:00');
CREATE TABLE NS_PART_2022_12 PARTITION OF nightscoutdb FOR VALUES FROM ('2022-12-01T00:00:00') TO ('2023-01-01T00:00:00');
CREATE TABLE NS_PART_2023_01 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-01-01T00:00:00') TO ('2023-02-01T00:00:00');
CREATE TABLE NS_PART_2023_02 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-02-01T00:00:00') TO ('2023-03-01T00:00:00');
CREATE TABLE NS_PART_2023_03 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-03-01T00:00:00') TO ('2023-04-01T00:00:00');
CREATE TABLE NS_PART_2023_04 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-04-01T00:00:00') TO ('2023-05-01T00:00:00');
CREATE TABLE NS_PART_2023_05 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-05-01T00:00:00') TO ('2023-06-01T00:00:00');
CREATE TABLE NS_PART_2023_06 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-06-01T00:00:00') TO ('2023-07-01T00:00:00');
CREATE TABLE NS_PART_2023_07 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-07-01T00:00:00') TO ('2023-08-01T00:00:00');
CREATE TABLE NS_PART_2023_08 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-08-01T00:00:00') TO ('2023-09-01T00:00:00');
CREATE TABLE NS_PART_2023_09 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-09-01T00:00:00') TO ('2023-10-01T00:00:00');
CREATE TABLE NS_PART_2023_10 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-10-01T00:00:00') TO ('2023-11-01T00:00:00');
CREATE TABLE NS_PART_2023_11 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-11-01T00:00:00') TO ('2023-12-01T00:00:00');
CREATE TABLE NS_PART_2023_12 PARTITION OF nightscoutdb FOR VALUES FROM ('2023-12-01T00:00:00') TO ('2024-01-01T00:00:00');
CREATE TABLE NS_PART_2024_01 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-01-01T00:00:00') TO ('2024-02-01T00:00:00');
CREATE TABLE NS_PART_2024_02 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-02-01T00:00:00') TO ('2024-03-01T00:00:00');
CREATE TABLE NS_PART_2024_03 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-03-01T00:00:00') TO ('2024-04-01T00:00:00');
CREATE TABLE NS_PART_2024_04 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-04-01T00:00:00') TO ('2024-05-01T00:00:00');
CREATE TABLE NS_PART_2024_05 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-05-01T00:00:00') TO ('2024-06-01T00:00:00');
CREATE TABLE NS_PART_2024_06 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-06-01T00:00:00') TO ('2024-07-01T00:00:00');
CREATE TABLE NS_PART_2024_07 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-07-01T00:00:00') TO ('2024-08-01T00:00:00');
CREATE TABLE NS_PART_2024_08 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-08-01T00:00:00') TO ('2024-09-01T00:00:00');
CREATE TABLE NS_PART_2024_09 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-09-01T00:00:00') TO ('2024-10-01T00:00:00');
CREATE TABLE NS_PART_2024_10 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-10-01T00:00:00') TO ('2024-11-01T00:00:00');
CREATE TABLE NS_PART_2024_11 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-11-01T00:00:00') TO ('2024-12-01T00:00:00');
CREATE TABLE NS_PART_2024_12 PARTITION OF nightscoutdb FOR VALUES FROM ('2024-12-01T00:00:00') TO ('2025-01-01T00:00:00');
CREATE TABLE NS_PART_2025_01 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-01-01T00:00:00') TO ('2025-02-01T00:00:00');
CREATE TABLE NS_PART_2025_02 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-02-01T00:00:00') TO ('2025-03-01T00:00:00');
CREATE TABLE NS_PART_2025_03 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-03-01T00:00:00') TO ('2025-04-01T00:00:00');
CREATE TABLE NS_PART_2025_04 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-04-01T00:00:00') TO ('2025-05-01T00:00:00');
CREATE TABLE NS_PART_2025_05 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-05-01T00:00:00') TO ('2025-06-01T00:00:00');
CREATE TABLE NS_PART_2025_06 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-06-01T00:00:00') TO ('2025-07-01T00:00:00');
CREATE TABLE NS_PART_2025_07 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-07-01T00:00:00') TO ('2025-08-01T00:00:00');
CREATE TABLE NS_PART_2025_08 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-08-01T00:00:00') TO ('2025-09-01T00:00:00');
CREATE TABLE NS_PART_2025_09 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-09-01T00:00:00') TO ('2025-10-01T00:00:00');
CREATE TABLE NS_PART_2025_10 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-10-01T00:00:00') TO ('2025-11-01T00:00:00');
CREATE TABLE NS_PART_2025_11 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-11-01T00:00:00') TO ('2025-12-01T00:00:00');
CREATE TABLE NS_PART_2025_12 PARTITION OF nightscoutdb FOR VALUES FROM ('2025-12-01T00:00:00') TO ('2025-01-01T00:00:00');
CREATE TABLE NS_PART_2026_01 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-01-01T00:00:00') TO ('2026-02-01T00:00:00');
CREATE TABLE NS_PART_2026_02 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-02-01T00:00:00') TO ('2026-03-01T00:00:00');
CREATE TABLE NS_PART_2026_03 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-03-01T00:00:00') TO ('2026-04-01T00:00:00');
CREATE TABLE NS_PART_2026_04 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-04-01T00:00:00') TO ('2026-05-01T00:00:00');
CREATE TABLE NS_PART_2026_05 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-05-01T00:00:00') TO ('2026-06-01T00:00:00');
CREATE TABLE NS_PART_2026_06 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-06-01T00:00:00') TO ('2026-07-01T00:00:00');
CREATE TABLE NS_PART_2026_07 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-07-01T00:00:00') TO ('2026-08-01T00:00:00');
CREATE TABLE NS_PART_2026_08 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-08-01T00:00:00') TO ('2026-09-01T00:00:00');
CREATE TABLE NS_PART_2026_09 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-09-01T00:00:00') TO ('2026-10-01T00:00:00');
CREATE TABLE NS_PART_2026_10 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-10-01T00:00:00') TO ('2026-11-01T00:00:00');
CREATE TABLE NS_PART_2026_11 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-11-01T00:00:00') TO ('2026-12-01T00:00:00');
CREATE TABLE NS_PART_2026_12 PARTITION OF nightscoutdb FOR VALUES FROM ('2026-12-01T00:00:00') TO ('2027-01-01T00:00:00');

drop table if exists mysugr;
create table if not exists mysugr
(
    id serial primary key ,
    local_datetime timestamp,
    tags text,    
    bgreading_mmol  float,
    insulin_inj_units_pen float,
    basal_inj_units float,
    basal_pump_units float,
    bolus_inj_meal float,
    bolus_inj_correction float,
    temp_basal_percentage float,
    temp_basal_duration_mins float,
    meal_carbs_grams float,
    meal_description text,
    activity_duration_mins float,
    activity_intensity float,
    activity_description text,
    steps float,
    notes text,
    location text,
    blood_pressure text,
    body_weight_kg float,
    hba1c_mmol float,
    ketones float,
    food_type text,
    medication text,
    timezone text,
    utc_datetime timestamp,
    time bigint unique
);

comment on table mysugr is 'Carb and bolus details';
comment on column mysugr.id is 'Primary key';
comment on column mysugr.local_datetime is 'Local datetime';
comment on column mysugr.tags is 'Tags';
comment on column mysugr.bgreading_mmol is 'Blood sugar reading (mmol/L)';
comment on column mysugr.insulin_inj_units_pen is 'Insulin injection units (Pen)';
comment on column mysugr.basal_inj_units is 'Basal injection units';
comment on column mysugr.basal_pump_units is 'Insulin injection units (pump)';
comment on column mysugr.bolus_inj_meal is 'Insulin (Meal)';
comment on column mysugr.bolus_inj_correction is 'Insulin (Correction)';
comment on column mysugr.temp_basal_percentage is 'Temporary basal percentage';
comment on column mysugr.temp_basal_duration_mins is 'Temporary basal duration (minutes)';
comment on column mysugr.meal_carbs_grams is 'Meal carbohydrates (grams, factor 1)';
comment on column mysugr.meal_description is 'Meal descriptions';
comment on column mysugr.activity_duration_mins is 'Activity duration (minutes)';
comment on column mysugr.activity_intensity is 'Activity intensity (1: Cosy, 2: Ordinary, 3: Demanding)';
comment on column mysugr.activity_description is 'Activity description';
comment on column mysugr.steps is 'Steps';
comment on column mysugr.notes is 'Notes';
comment on column mysugr.location is 'Location';
comment on column mysugr.blood_pressure is 'Blood pressure';
comment on column mysugr.body_weight_kg is 'Body weight (kg)';
comment on column mysugr.hba1c_mmol is 'HbA1c (mmol/mol)';
comment on column mysugr.ketones is 'Ketones';
comment on column mysugr.food_type is 'Food type';
comment on column mysugr.medication is 'Medication';
comment on column mysugr.timezone is 'Timezone';
comment on column mysugr.utc_datetime is 'UTC datetime';
comment on column mysugr.time is 'Unix timestamp';

create view public.latest(bg_time, created_at, bg_mmol, bg_trend) as
SELECT (nightscoutdb.ns_time / 1000)                           AS bg_time,
       nightscoutdb.ns_datetime                                AS created_at,
       round(((nightscoutdb.sgv)::numeric / (18)::numeric), 2) AS bg_mmol,
       nightscoutdb.trend                                      AS bg_trend
FROM nightscoutdb
ORDER BY nightscoutdb.ns_time DESC;

create or replace view daily_avg as
select bg_date,round(bg_sgv/18, 2) as bg_mmol from (
select date(ns_datetime) as bg_date,round(avg(sgv),2) as bg_sgv from nightscoutdb group by 1 order by 1 desc) a;