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

drop table if exists public.nightscoutdb cascade;
create table public.nightscoutdb
(
    id serial,
    sgv          bigint,
    ns_time bigint unique,
    ns_datetime timestamptz,
    trend        int,
    utcoffset  int,
    systime    timestamptz
);

drop table if exists public.ns_part cascade;
create table public.ns_part
(
    id serial,
    sgv          bigint,
    ns_time bigint,
    ns_datetime timestamptz,
    trend        int,
    utcoffset  int,
    systime    timestamptz
) partition by range (ns_datetime);

create index idx_ns_datetime on public.ns_part (ns_datetime);

CREATE TABLE NS_PART_2021_04 PARTITION OF public.ns_part FOR VALUES FROM ('2021-04-01T00:00:00') TO ('2021-05-01T00:00:00');
CREATE TABLE NS_PART_2021_05 PARTITION OF public.ns_part FOR VALUES FROM ('2021-05-01T00:00:00') TO ('2021-06-01T00:00:00');
CREATE TABLE NS_PART_2021_06 PARTITION OF public.ns_part FOR VALUES FROM ('2021-06-01T00:00:00') TO ('2021-07-01T00:00:00');
CREATE TABLE NS_PART_2021_07 PARTITION OF public.ns_part FOR VALUES FROM ('2021-07-01T00:00:00') TO ('2021-08-01T00:00:00');
CREATE TABLE NS_PART_2021_08 PARTITION OF public.ns_part FOR VALUES FROM ('2021-08-01T00:00:00') TO ('2021-09-01T00:00:00');
CREATE TABLE NS_PART_2021_09 PARTITION OF public.ns_part FOR VALUES FROM ('2021-09-01T00:00:00') TO ('2021-10-01T00:00:00');
CREATE TABLE NS_PART_2021_10 PARTITION OF public.ns_part FOR VALUES FROM ('2021-10-01T00:00:00') TO ('2021-11-01T00:00:00');
CREATE TABLE NS_PART_2021_11 PARTITION OF public.ns_part FOR VALUES FROM ('2021-11-01T00:00:00') TO ('2021-12-01T00:00:00');
CREATE TABLE NS_PART_2021_12 PARTITION OF public.ns_part FOR VALUES FROM ('2021-12-01T00:00:00') TO ('2022-01-01T00:00:00');
CREATE TABLE NS_PART_2022_01 PARTITION OF public.ns_part FOR VALUES FROM ('2022-01-01T00:00:00') TO ('2022-02-01T00:00:00');
CREATE TABLE NS_PART_2022_02 PARTITION OF public.ns_part FOR VALUES FROM ('2022-02-01T00:00:00') TO ('2022-03-01T00:00:00');
CREATE TABLE NS_PART_2022_03 PARTITION OF public.ns_part FOR VALUES FROM ('2022-03-01T00:00:00') TO ('2022-04-01T00:00:00');
CREATE TABLE NS_PART_2022_04 PARTITION OF public.ns_part FOR VALUES FROM ('2022-04-01T00:00:00') TO ('2022-05-01T00:00:00');
CREATE TABLE NS_PART_2022_05 PARTITION OF public.ns_part FOR VALUES FROM ('2022-05-01T00:00:00') TO ('2022-06-01T00:00:00');
CREATE TABLE NS_PART_2022_06 PARTITION OF public.ns_part FOR VALUES FROM ('2022-06-01T00:00:00') TO ('2022-07-01T00:00:00');
CREATE TABLE NS_PART_2022_07 PARTITION OF public.ns_part FOR VALUES FROM ('2022-07-01T00:00:00') TO ('2022-08-01T00:00:00');
CREATE TABLE NS_PART_2022_08 PARTITION OF public.ns_part FOR VALUES FROM ('2022-08-01T00:00:00') TO ('2022-09-01T00:00:00');
CREATE TABLE NS_PART_2022_09 PARTITION OF public.ns_part FOR VALUES FROM ('2022-09-01T00:00:00') TO ('2022-10-01T00:00:00');
CREATE TABLE NS_PART_2022_10 PARTITION OF public.ns_part FOR VALUES FROM ('2022-10-01T00:00:00') TO ('2022-11-01T00:00:00');
CREATE TABLE NS_PART_2022_11 PARTITION OF public.ns_part FOR VALUES FROM ('2022-11-01T00:00:00') TO ('2022-12-01T00:00:00');
CREATE TABLE NS_PART_2022_12 PARTITION OF public.ns_part FOR VALUES FROM ('2022-12-01T00:00:00') TO ('2023-01-01T00:00:00');
CREATE TABLE NS_PART_2023_01 PARTITION OF public.ns_part FOR VALUES FROM ('2023-01-01T00:00:00') TO ('2023-02-01T00:00:00');
CREATE TABLE NS_PART_2023_02 PARTITION OF public.ns_part FOR VALUES FROM ('2023-02-01T00:00:00') TO ('2023-03-01T00:00:00');
CREATE TABLE NS_PART_2023_03 PARTITION OF public.ns_part FOR VALUES FROM ('2023-03-01T00:00:00') TO ('2023-04-01T00:00:00');
CREATE TABLE NS_PART_2023_04 PARTITION OF public.ns_part FOR VALUES FROM ('2023-04-01T00:00:00') TO ('2023-05-01T00:00:00');
CREATE TABLE NS_PART_2023_05 PARTITION OF public.ns_part FOR VALUES FROM ('2023-05-01T00:00:00') TO ('2023-06-01T00:00:00');
CREATE TABLE NS_PART_2023_06 PARTITION OF public.ns_part FOR VALUES FROM ('2023-06-01T00:00:00') TO ('2023-07-01T00:00:00');
CREATE TABLE NS_PART_2023_07 PARTITION OF public.ns_part FOR VALUES FROM ('2023-07-01T00:00:00') TO ('2023-08-01T00:00:00');
CREATE TABLE NS_PART_2023_08 PARTITION OF public.ns_part FOR VALUES FROM ('2023-08-01T00:00:00') TO ('2023-09-01T00:00:00');
CREATE TABLE NS_PART_2023_09 PARTITION OF public.ns_part FOR VALUES FROM ('2023-09-01T00:00:00') TO ('2023-10-01T00:00:00');
CREATE TABLE NS_PART_2023_10 PARTITION OF public.ns_part FOR VALUES FROM ('2023-10-01T00:00:00') TO ('2023-11-01T00:00:00');
CREATE TABLE NS_PART_2023_11 PARTITION OF public.ns_part FOR VALUES FROM ('2023-11-01T00:00:00') TO ('2023-12-01T00:00:00');
CREATE TABLE NS_PART_2023_12 PARTITION OF public.ns_part FOR VALUES FROM ('2023-12-01T00:00:00') TO ('2024-01-01T00:00:00');
CREATE TABLE NS_PART_2024_01 PARTITION OF public.ns_part FOR VALUES FROM ('2024-01-01T00:00:00') TO ('2024-02-01T00:00:00');
CREATE TABLE NS_PART_2024_02 PARTITION OF public.ns_part FOR VALUES FROM ('2024-02-01T00:00:00') TO ('2024-03-01T00:00:00');
CREATE TABLE NS_PART_2024_03 PARTITION OF public.ns_part FOR VALUES FROM ('2024-03-01T00:00:00') TO ('2024-04-01T00:00:00');
CREATE TABLE NS_PART_2024_04 PARTITION OF public.ns_part FOR VALUES FROM ('2024-04-01T00:00:00') TO ('2024-05-01T00:00:00');
CREATE TABLE NS_PART_2024_05 PARTITION OF public.ns_part FOR VALUES FROM ('2024-05-01T00:00:00') TO ('2024-06-01T00:00:00');
CREATE TABLE NS_PART_2024_06 PARTITION OF public.ns_part FOR VALUES FROM ('2024-06-01T00:00:00') TO ('2024-07-01T00:00:00');
CREATE TABLE NS_PART_2024_07 PARTITION OF public.ns_part FOR VALUES FROM ('2024-07-01T00:00:00') TO ('2024-08-01T00:00:00');
CREATE TABLE NS_PART_2024_08 PARTITION OF public.ns_part FOR VALUES FROM ('2024-08-01T00:00:00') TO ('2024-09-01T00:00:00');
CREATE TABLE NS_PART_2024_09 PARTITION OF public.ns_part FOR VALUES FROM ('2024-09-01T00:00:00') TO ('2024-10-01T00:00:00');
CREATE TABLE NS_PART_2024_10 PARTITION OF public.ns_part FOR VALUES FROM ('2024-10-01T00:00:00') TO ('2024-11-01T00:00:00');
CREATE TABLE NS_PART_2024_11 PARTITION OF public.ns_part FOR VALUES FROM ('2024-11-01T00:00:00') TO ('2024-12-01T00:00:00');
CREATE TABLE NS_PART_2024_12 PARTITION OF public.ns_part FOR VALUES FROM ('2024-12-01T00:00:00') TO ('2025-01-01T00:00:00');
CREATE TABLE NS_PART_2025_01 PARTITION OF public.ns_part FOR VALUES FROM ('2025-01-01T00:00:00') TO ('2025-02-01T00:00:00');
CREATE TABLE NS_PART_2025_02 PARTITION OF public.ns_part FOR VALUES FROM ('2025-02-01T00:00:00') TO ('2025-03-01T00:00:00');
CREATE TABLE NS_PART_2025_03 PARTITION OF public.ns_part FOR VALUES FROM ('2025-03-01T00:00:00') TO ('2025-04-01T00:00:00');
CREATE TABLE NS_PART_2025_04 PARTITION OF public.ns_part FOR VALUES FROM ('2025-04-01T00:00:00') TO ('2025-05-01T00:00:00');
CREATE TABLE NS_PART_2025_05 PARTITION OF public.ns_part FOR VALUES FROM ('2025-05-01T00:00:00') TO ('2025-06-01T00:00:00');
CREATE TABLE NS_PART_2025_06 PARTITION OF public.ns_part FOR VALUES FROM ('2025-06-01T00:00:00') TO ('2025-07-01T00:00:00');
CREATE TABLE NS_PART_2025_07 PARTITION OF public.ns_part FOR VALUES FROM ('2025-07-01T00:00:00') TO ('2025-08-01T00:00:00');
CREATE TABLE NS_PART_2025_08 PARTITION OF public.ns_part FOR VALUES FROM ('2025-08-01T00:00:00') TO ('2025-09-01T00:00:00');
CREATE TABLE NS_PART_2025_09 PARTITION OF public.ns_part FOR VALUES FROM ('2025-09-01T00:00:00') TO ('2025-10-01T00:00:00');
CREATE TABLE NS_PART_2025_10 PARTITION OF public.ns_part FOR VALUES FROM ('2025-10-01T00:00:00') TO ('2025-11-01T00:00:00');
CREATE TABLE NS_PART_2025_11 PARTITION OF public.ns_part FOR VALUES FROM ('2025-11-01T00:00:00') TO ('2025-12-01T00:00:00');
CREATE TABLE NS_PART_2025_12 PARTITION OF public.ns_part FOR VALUES FROM ('2025-12-01T00:00:00') TO ('2026-01-01T00:00:00');
CREATE TABLE NS_PART_2026_01 PARTITION OF public.ns_part FOR VALUES FROM ('2026-01-01T00:00:00') TO ('2026-02-01T00:00:00');
CREATE TABLE NS_PART_2026_02 PARTITION OF public.ns_part FOR VALUES FROM ('2026-02-01T00:00:00') TO ('2026-03-01T00:00:00');
CREATE TABLE NS_PART_2026_03 PARTITION OF public.ns_part FOR VALUES FROM ('2026-03-01T00:00:00') TO ('2026-04-01T00:00:00');
CREATE TABLE NS_PART_2026_04 PARTITION OF public.ns_part FOR VALUES FROM ('2026-04-01T00:00:00') TO ('2026-05-01T00:00:00');
CREATE TABLE NS_PART_2026_05 PARTITION OF public.ns_part FOR VALUES FROM ('2026-05-01T00:00:00') TO ('2026-06-01T00:00:00');
CREATE TABLE NS_PART_2026_06 PARTITION OF public.ns_part FOR VALUES FROM ('2026-06-01T00:00:00') TO ('2026-07-01T00:00:00');
CREATE TABLE NS_PART_2026_07 PARTITION OF public.ns_part FOR VALUES FROM ('2026-07-01T00:00:00') TO ('2026-08-01T00:00:00');
CREATE TABLE NS_PART_2026_08 PARTITION OF public.ns_part FOR VALUES FROM ('2026-08-01T00:00:00') TO ('2026-09-01T00:00:00');
CREATE TABLE NS_PART_2026_09 PARTITION OF public.ns_part FOR VALUES FROM ('2026-09-01T00:00:00') TO ('2026-10-01T00:00:00');
CREATE TABLE NS_PART_2026_10 PARTITION OF public.ns_part FOR VALUES FROM ('2026-10-01T00:00:00') TO ('2026-11-01T00:00:00');
CREATE TABLE NS_PART_2026_11 PARTITION OF public.ns_part FOR VALUES FROM ('2026-11-01T00:00:00') TO ('2026-12-01T00:00:00');
CREATE TABLE NS_PART_2026_12 PARTITION OF public.ns_part FOR VALUES FROM ('2026-12-01T00:00:00') TO ('2027-01-01T00:00:00');
CREATE TABLE NS_PART_2027_01 PARTITION OF public.ns_part FOR VALUES FROM ('2027-01-01T00:00:00') TO ('2027-02-01T00:00:00');
CREATE TABLE NS_PART_2027_02 PARTITION OF public.ns_part FOR VALUES FROM ('2027-02-01T00:00:00') TO ('2027-03-01T00:00:00');
CREATE TABLE NS_PART_2027_03 PARTITION OF public.ns_part FOR VALUES FROM ('2027-03-01T00:00:00') TO ('2027-04-01T00:00:00');
CREATE TABLE NS_PART_2027_04 PARTITION OF public.ns_part FOR VALUES FROM ('2027-04-01T00:00:00') TO ('2027-05-01T00:00:00');
CREATE TABLE NS_PART_2027_05 PARTITION OF public.ns_part FOR VALUES FROM ('2027-05-01T00:00:00') TO ('2027-06-01T00:00:00');
CREATE TABLE NS_PART_2027_06 PARTITION OF public.ns_part FOR VALUES FROM ('2027-06-01T00:00:00') TO ('2027-07-01T00:00:00');
CREATE TABLE NS_PART_2027_07 PARTITION OF public.ns_part FOR VALUES FROM ('2027-07-01T00:00:00') TO ('2027-08-01T00:00:00');
CREATE TABLE NS_PART_2027_08 PARTITION OF public.ns_part FOR VALUES FROM ('2027-08-01T00:00:00') TO ('2027-09-01T00:00:00');
CREATE TABLE NS_PART_2027_09 PARTITION OF public.ns_part FOR VALUES FROM ('2027-09-01T00:00:00') TO ('2027-10-01T00:00:00');
CREATE TABLE NS_PART_2027_10 PARTITION OF public.ns_part FOR VALUES FROM ('2027-10-01T00:00:00') TO ('2027-11-01T00:00:00');
CREATE TABLE NS_PART_2027_11 PARTITION OF public.ns_part FOR VALUES FROM ('2027-11-01T00:00:00') TO ('2027-12-01T00:00:00');
CREATE TABLE NS_PART_2027_12 PARTITION OF public.ns_part FOR VALUES FROM ('2027-12-01T00:00:00') TO ('2028-01-01T00:00:00');

DROP VIEW IF EXISTS public.base_view;
CREATE OR REPLACE VIEW public.base_view
AS SELECT a.bg_time,
    a.bg_datetime,
    a.bg_mgdl,
    a.bg_mmol,
    a.bg_trend,
    initcap(to_char(a.bg_datetime, 'dy')) AS day_of_week,
    EXTRACT(hour FROM a.bg_datetime) AS hour_of_day,
    EXTRACT(week FROM a.bg_datetime) AS week_of_year,
    EXTRACT(day FROM a.bg_datetime) AS date_of_month,
    EXTRACT(month FROM a.bg_datetime) AS month_of_year,
    EXTRACT(year FROM a.bg_datetime) AS year,
        CASE
            WHEN a.bg_mmol::double precision < 4::double precision OR a.bg_mmol::double precision > 7::double precision THEN false
            ELSE true
        END AS in_range_strict,
        CASE
            WHEN a.bg_mmol::double precision < 4::double precision OR a.bg_mmol::double precision > 10::double precision THEN false
            ELSE true
        END AS in_range_medical
   FROM ( SELECT round(n.sgv::numeric / 18::numeric, 2) AS bg_mmol,
            n.trend as bg_trend,
            n.sgv AS bg_mgdl,
            n.ns_time AS bg_time,
            n.ns_datetime AS bg_datetime
           FROM ns_part n) a;


DROP VIEW IF EXISTS public.latest;
CREATE OR REPLACE VIEW public.latest
AS SELECT ns_part.ns_time / 1000 AS bg_time,
    ns_part.ns_datetime AS created_at,
    round(ns_part.sgv::numeric / 18::numeric, 2) AS bg_mmol,
    ns_part.trend AS bg_trend
   FROM ns_part
  ORDER BY ns_part.ns_time DESC;

DROP VIEW IF EXISTS public.daily_avg;
CREATE OR REPLACE VIEW public.daily_avg
AS SELECT a.bg_date,
    round(a.bg_sgv / 18::numeric, 2) AS bg_mmol
   FROM ( SELECT date(ns_part.ns_datetime) AS bg_date,
            round(avg(ns_part.sgv), 2) AS bg_sgv
           FROM ns_part
          GROUP BY (date(ns_part.ns_datetime))
          ORDER BY (date(ns_part.ns_datetime)) DESC) a;

DROP VIEW IF EXISTS public.percent_in_range cascade;
CREATE OR REPLACE VIEW public.percent_in_range
AS SELECT a.bg_mmol,
    a.bg_time,
    a.bg_datetime,
        CASE
            WHEN a.bg_mmol < 4::double precision OR a.bg_mmol > 7::double precision THEN false
            ELSE true
        END AS in_range_strict,
        CASE
            WHEN a.bg_mmol < 4::double precision OR a.bg_mmol > 10::double precision THEN false
            ELSE true
        END AS in_range_medical        
   FROM ( SELECT n.sgv::double precision / 18::double precision AS bg_mmol,
            n.ns_time AS bg_time,
            n.ns_datetime AS bg_datetime
           FROM ns_part n) a;

DROP VIEW IF EXISTS avg_mmol ;
create or replace view avg_mmol as 
(
select '1h' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 3600)*1000 
union all
select '3h' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 10800)*1000 
union all
select '6h' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 21600)*1000 
union all
select '12h' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 43200)*1000 
union all
select '1d' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 86400)*1000 
union all
select '7d' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 604800)*1000
union all
select '14d' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 1209600)*1000
union all
select '30d' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 2592000)*1000
union all
select '60d' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 5184000)*1000
union all
select '90d' as time_period,round(avg(sgv)/18,2) as bg_mmol from ns_part where ns_time >= (extract(epoch from now()) - 7776000)*1000
);

DROP VIEW IF EXISTS quart_mmol;
create or replace view quart_mmol as
select 
round(ns_part.sgv::numeric / 18::numeric, 2) AS bg_mmol,
ntile(4) over (order by ns_part.sgv) as quartile
from ns_part
WHERE ns_part.ns_time::numeric >= ((EXTRACT(epoch FROM now()) - 86400::numeric) * 1000::numeric)
order by 1
;

DROP VIEW IF EXISTS quart_mmol_stats;
create or replace view quart_mmol_stats as
select '1d' as time_period,
min(bg_mmol) as min_mmol,
MAX(CASE WHEN Quartile = 1 THEN bg_mmol END) as q1_mmol,MAX(CASE WHEN Quartile = 2 THEN bg_mmol END) as q2_mmol,MAX(CASE WHEN Quartile = 3 THEN bg_mmol END) as q3_mmol,
max(bg_mmol) as max_mmol,count(quartile) as num_recs
from
(select round(ns_part.sgv::numeric / 18::numeric, 2) AS bg_mmol,ntile(4) over (order by ns_part.sgv) as quartile from ns_part WHERE ns_part.ns_time::numeric >= ((EXTRACT(epoch FROM now()) - 86400::numeric) * 1000::numeric)) a
union all
select '7d' as time_period,
min(bg_mmol) as min_mmol,
MAX(CASE WHEN Quartile = 1 THEN bg_mmol END) as q1_mmol,MAX(CASE WHEN Quartile = 2 THEN bg_mmol END) as q2_mmol,MAX(CASE WHEN Quartile = 3 THEN bg_mmol END) as q3_mmol,
max(bg_mmol) as max_mmol,count(quartile) as num_recs
from
(select round(ns_part.sgv::numeric / 18::numeric, 2) AS bg_mmol,ntile(4) over (order by ns_part.sgv) as quartile from ns_part WHERE ns_part.ns_time::numeric >= ((EXTRACT(epoch FROM now()) - 604800::numeric) * 1000::numeric)) a
union all
select '14d' as time_period,
min(bg_mmol) as min_mmol,
MAX(CASE WHEN Quartile = 1 THEN bg_mmol END) as q1_mmol,MAX(CASE WHEN Quartile = 2 THEN bg_mmol END) as q2_mmol,MAX(CASE WHEN Quartile = 3 THEN bg_mmol END) as q3_mmol,
max(bg_mmol) as max_mmol,count(quartile) as num_recs
from
(select round(ns_part.sgv::numeric / 18::numeric, 2) AS bg_mmol,ntile(4) over (order by ns_part.sgv) as quartile from ns_part WHERE ns_part.ns_time::numeric >= ((EXTRACT(epoch FROM now()) - 1209600::numeric) * 1000::numeric)) a
union all
select '30d' as time_period,
min(bg_mmol) as min_mmol,
MAX(CASE WHEN Quartile = 1 THEN bg_mmol END) as q1_mmol,MAX(CASE WHEN Quartile = 2 THEN bg_mmol END) as q2_mmol,MAX(CASE WHEN Quartile = 3 THEN bg_mmol END) as q3_mmol,
max(bg_mmol) as max_mmol,count(quartile) as num_recs
from
(select round(ns_part.sgv::numeric / 18::numeric, 2) AS bg_mmol,ntile(4) over (order by ns_part.sgv) as quartile from ns_part WHERE ns_part.ns_time::numeric >= ((EXTRACT(epoch FROM now()) - 2592000::numeric) * 1000::numeric)) a
union all
select '60d' as time_period,
min(bg_mmol) as min_mmol,
MAX(CASE WHEN Quartile = 1 THEN bg_mmol END) as q1_mmol,MAX(CASE WHEN Quartile = 2 THEN bg_mmol END) as q2_mmol,MAX(CASE WHEN Quartile = 3 THEN bg_mmol END) as q3_mmol,
max(bg_mmol) as max_mmol,count(quartile) as num_recs
from
(select round(ns_part.sgv::numeric / 18::numeric, 2) AS bg_mmol,ntile(4) over (order by ns_part.sgv) as quartile from ns_part WHERE ns_part.ns_time::numeric >= ((EXTRACT(epoch FROM now()) - 5184000::numeric) * 1000::numeric)) a
union all
select '90d' as time_period,
min(bg_mmol) as min_mmol,
MAX(CASE WHEN Quartile = 1 THEN bg_mmol END) as q1_mmol,MAX(CASE WHEN Quartile = 2 THEN bg_mmol END) as q2_mmol,MAX(CASE WHEN Quartile = 3 THEN bg_mmol END) as q3_mmol,
max(bg_mmol) as max_mmol,count(quartile) as num_recs
from
(select round(ns_part.sgv::numeric / 18::numeric, 2) AS bg_mmol,ntile(4) over (order by ns_part.sgv) as quartile from ns_part WHERE ns_part.ns_time::numeric >= ((EXTRACT(epoch FROM now()) - 7776000::numeric) * 1000::numeric)) a
;

DROP VIEW IF EXISTS daily_tir_strict;
CREATE OR REPLACE VIEW public.daily_tir_strict
AS SELECT a.bg_datetime,
    a.in_range_val,
    a.total_recs,
    round(a.in_range_val::numeric / a.total_recs::numeric * 100::numeric, 2) AS pir
   FROM ( SELECT pir.bg_datetime::date AS bg_datetime,
            sum(
                CASE
                    WHEN pir.in_range_strict = true THEN 1
                    ELSE 0
                END) AS in_range_val,
            count(1) AS total_recs
           FROM percent_in_range pir
          GROUP BY (pir.bg_datetime::date)
          ORDER BY (pir.bg_datetime::date) DESC) a;

DROP VIEW IF EXISTS daily_tir_medical;
CREATE OR REPLACE VIEW public.daily_tir_medical
AS SELECT a.bg_datetime,
    a.in_range_val,
    a.total_recs,
    round(a.in_range_val::numeric / a.total_recs::numeric * 100::numeric, 2) AS pir
   FROM ( SELECT pir.bg_datetime::date AS bg_datetime,
            sum(
                CASE
                    WHEN pir.in_range_medical = true THEN 1
                    ELSE 0
                END) AS in_range_val,
            count(1) AS total_recs
           FROM percent_in_range pir
          GROUP BY (pir.bg_datetime::date)
          ORDER BY (pir.bg_datetime::date) DESC) a;

create or replace function create_ns_part_row()
returns trigger 
language PLPGSQL
as 
$$
begin
	insert into ns_part(sgv,ns_time,ns_datetime,trend,utcoffset,systime)
	values(new.sgv, new.ns_time, new.ns_datetime, new.trend, new.utcoffset, new.systime);
return new;
end
$$;


create trigger nightscoutdb_ins after insert on public.nightscoutdb for each row execute procedure create_ns_part_row();

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

-- drop type if exists insulin_types;
-- create type insulin_types as ENUM('LONG_ACTING', 'RAPID_ACTING');

-- create table if not exists insulin
-- (
--     id serial primary key,
--     date_utc_millis bigint not null unique,
--     date_utc timestampz,
--     insulin_type insulin_types,
--     insulin_qty real not null
-- )