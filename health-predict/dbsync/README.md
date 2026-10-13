# dbsync

Copies new and changed rows from every table in the source Postgres database into the target database.

In health-app, it keeps the optional replica current: a copy of the server's `health-db` in the
`health-predict-replica` service, for training health-predict on another machine (`PREDICT_DB=replica`). It isn't
needed where the pipeline reads `health-db` directly.

Run it as often as you like. Each run copies only what changed since the last one.

## Usage

```sh
cd dbsync
go build -o dbsync .
./dbsync -check     # test both connections and privileges, then exit
./dbsync -dry-run   # show what each table would do; writes nothing
./dbsync            # sync
./dbsync -clean     # drop and fully reload every synced table on the target (asks for confirmation; -yes to skip)
```

### Clean reload

`-clean` lists the target tables it will drop and asks you to type `yes`. Pass `-yes` for scripts; without a terminal and without `-yes` it refuses to run. It is useful for resetting a target that has drifted: missed deletes, updates that `incremental-pk` cannot see, or new source columns.

- Only tables this tool syncs (in `SYNC_SCHEMAS`, minus `SYNC_EXCLUDE_TABLES`) are dropped. Other target tables are left alone.
- Each table's drop, recreate and reload happen in one transaction. Readers see the old data until commit, and a failure leaves the table unchanged.
- Replicated types are kept, not dropped. Later runs go back to incremental syncing automatically.
- Combine it with `-dry-run` to preview.

Connection settings come from `.env` (looked up in `./` then `../`, or pass `-env path`). See `../.env.example` for variable names. Credentials are never logged. Connection errors have the host, port, user, database and password masked as `***`.

`-check` reports each database's server version and latency. On the source it confirms there are tables to sync and that each one is readable. On the target it confirms the database is writable (not a standby or read-only) and warns if the user cannot create schemas. It exits 0 if both pass and 1 otherwise. Connection attempts time out after 10s.

## How it works

1. Lists all tables in `SYNC_SCHEMAS` on the source (default `public`). The source session is read-only.
2. Replicates user-defined column types to the target: enums, domains, composite types, and arrays of these. New enum values on the source are added on every run, in the source's order. Types that come from extensions (e.g. `citext`) are not created; the table fails with a hint to run `CREATE EXTENSION` on the target.
3. Creates any missing target table with the same columns, NOT NULL constraints and primary key. Defaults, foreign keys and indexes are not copied, so tables can be loaded in any order.
4. Picks a delta strategy per table, using the target's current max value as the watermark (no separate state table):

| Mode | When | Source rows read | Applied as |
|---|---|---|---|
| `watermark` | table has a column from `SYNC_WATERMARK_COLUMNS` (or `SYNC_TABLE_WATERMARKS`) | `col >= target max` | upsert on PK; without a PK, boundary rows are deleted and re-inserted |
| `incremental-pk` | single integer primary key | `pk > target max` | insert |
| `full-upsert` | other primary key | all | upsert, changed rows only |
| `full-append` | no PK, no watermark | all | insert rows not already present |

5. Each table runs in one target transaction: source rows stream via `COPY` into a temp staging table, then merge into the real table. If one table fails, the others still run and the exit code is 1.

## Limitations

- Deletes on the source are not propagated.
- `incremental-pk` is append-only: updates to existing rows are missed. If rows are updated, add an `updated_at` column or set `SYNC_TABLE_WATERMARKS`.
- A `created_at` watermark also catches inserts only, not updates.
- `full-*` modes read the whole source table on every run. That is fine for small tables but slow for large ones. Give large tables a watermark.
- Schema changes are not migrated. New source columns are ignored until you add them to the target.
- Domain CHECK constraints are not copied, only the base type. Range types and extension types must be created on the target manually.
