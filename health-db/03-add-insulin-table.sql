-- Migration for existing databases created before the insulin table existed.
-- Fresh databases get this table via 02-createtables.sql on initdb; this
-- script lets an already-running instance pick it up without a reset.
-- Safe to run multiple times.
do $$
begin
    if not exists (select 1 from pg_type where typname = 'insulin_types') then
        create type insulin_types as ENUM('LONG_ACTING', 'RAPID_ACTING');
    end if;
end
$$;

create table if not exists insulin
(
    id serial primary key,
    date_utc_millis bigint not null unique,
    date_utc timestamptz,
    insulin_type insulin_types,
    insulin_qty real not null
);
