-- +goose Up
-- Converted from health-db/03-add-insulin-table.sql, which was a manual,
-- unmounted migration for databases created before 00001_init_schema had
-- this table. Kept idempotent (IF NOT EXISTS) even though 00001 now
-- already creates it on a fresh database, so this remains a safe no-op
-- there and only does real work against a pre-00001 database.
-- +goose StatementBegin
do $$
begin
    if not exists (select 1 from pg_type where typname = 'insulin_types') then
        create type insulin_types as ENUM('LONG_ACTING', 'RAPID_ACTING');
    end if;
end
$$;
-- +goose StatementEnd

create table if not exists insulin
(
    id serial primary key,
    date_utc_millis bigint not null unique,
    date_utc timestamptz,
    insulin_type insulin_types,
    insulin_qty real not null
);

-- +goose Down
-- No-op: 00001_init_schema.sql's Down already drops the insulin table and
-- insulin_types type, and this migration never creates anything 00001
-- didn't already account for.
