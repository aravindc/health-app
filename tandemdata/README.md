# tandemdata

A CLI for pulling your own pump/CGM data out of [Tandem Source](https://source.tandemdiabetes.com)
and loading it into PostgreSQL for analysis.

It reverse-engineers Tandem Source's login flow and reports API (no browser
needed at runtime) and speaks directly to those endpoints to download your
pump event history, then upserts it into a Postgres table you can query with
plain SQL.

## Requirements

- Go 1.27+
- A Tandem Source account (username/password)
- A PostgreSQL database (any managed or self-hosted instance)

## Setup

```sh
cp .env.example .env
```

Fill in `.env`:

```sh
TANDEM_USERNAME=your-email@example.com
TANDEM_PASSWORD=your-password

# postgres://user:password@host:5432/dbname?sslmode=require
DATABASE_URL=postgres://user:password@your-postgres-host:5432/tandemdata?sslmode=require
```

`.env` is loaded automatically from the working directory and is gitignored.

## Usage

The CLI has three modes — exactly one of `-d`, `-l`, `-p` must be given per run.

### `-d` — download

Fetches your pumper report metadata and raw pump-log events from Tandem
Source and writes them to local JSON files. Every pump-log file lands in
`-chunk-dir` (default `./data/chunks`) named `<start>_<end>.json`, the same
naming `-l -init` reads — so any `-d` run can be picked up later by
`-l -init`, not just `-d -init`.

```sh
# last 28 days (default), written to <chunk-dir>/<28-days-ago>_<now>.json
go run ./cmd/tandemdata -d

# last N days, written to <chunk-dir>/<N-days-ago>_<now>.json
go run ./cmd/tandemdata -d -days 90

# an explicit date range, written to <chunk-dir>/2025-01-01_2025-02-01.json
go run ./cmd/tandemdata -d -start 2025-01-01 -end 2025-02-01

# an explicit start date through now
go run ./cmd/tandemdata -d -start 2025-01-01

# entire available history, fetched and cached in 30-day chunks
go run ./cmd/tandemdata -d -init
```

| Flag | Default | Description |
|---|---|---|
| `-days` | `28` | Days of history to fetch, ending now (ignored with `-init` or `-start`) |
| `-init` | `false` | Fetch the full available history instead of `-days` |
| `-start` | (none) | Start date (`YYYY-MM-DD`) of an explicit range; overrides `-days` and `-init` |
| `-end` | (none) | End date (`YYYY-MM-DD`) of an explicit range; defaults to now if `-start` is set |
| `-chunk-days` | `30` | Window size per request when `-init` is set |
| `-chunk-dir` | `./data/chunks` | Where downloaded pump-logs files are written/cached |
| `-meta-out` | `pumper-report-meta.json` | Output path for pumper metadata |

A plain `-d`, `-d -days N`, or `-d -start/-end` run fetches its range as a
single request and writes one file. `-init` instead fetches the full
available history in `-chunk-days` windows, caching each window's response
to `<chunk-dir>/<start>_<end>.json` as it goes. If a chunk fails partway
through (network issue, rate limit, etc.), just re-run the same command —
completed chunks are read from cache instead of re-fetched, so you only pay
for the missing dates. `-chunk-dir` is a permanent directory, not a
scratch/tmp path.

### `-l` — load

Parses pump-logs data and upserts it into the `events` table in PostgreSQL,
running any pending schema migrations first.

```sh
# load a single file (e.g. from a plain -d run)
go run ./cmd/tandemdata -l
go run ./cmd/tandemdata -l -file pump-logs-raw.json

# load every cached chunk file from a -d -init run
go run ./cmd/tandemdata -l -init
go run ./cmd/tandemdata -l -init -chunk-dir ./data/chunks
```

| Flag | Default | Description |
|---|---|---|
| `-file` | `pump-logs-raw.json` | Path to the raw pump-logs file to load (ignored with `-init`) |
| `-init` | `false` | Load every cached chunk file in `-chunk-dir` instead of `-file` |
| `-chunk-dir` | `./data/chunks` | Directory of cached chunk files to load, when `-init` is set |

`-l -init` avoids holding an entire multi-year history in memory at once by
loading and committing one chunk file at a time, in chronological order (by
filename). Each chunk is its own transaction, so a failure partway through
leaves already-loaded chunks committed — fix the issue and re-run; already-
loaded data is upserted again harmlessly (see below), and any chunk you
haven't reached yet just runs as normal.

Rows are upserted keyed on `(device_assignment_id, sequence_group,
sequence_number, is_clock_change)`, so loading the same file (or overlapping
date ranges) more than once is safe and won't create duplicates.

### `-p` — parse

Reads a raw pump-logs file locally and prints a summary, without touching
the network or a database.

```sh
# per-event-type counts
go run ./cmd/tandemdata -p

# the 20 most recent CGM readings (event code 399), as JSON
go run ./cmd/tandemdata -p -code 399
```

| Flag | Default | Description |
|---|---|---|
| `-file` | `pump-logs-raw.json` | Path to the raw pump-logs file to parse |
| `-code` | (none) | If set, print matching events instead of the summary |
| `-limit` | `20` | Max events to print when `-code` is set (`0` = no limit) |

## Database schema

The `events` table (see [`tandemdb/migrations`](tandemdb/migrations)) stores
every pump/CGM event with its raw, per-event-type properties as `JSONB`:

```sql
CREATE TABLE events (
    device_assignment_id TEXT NOT NULL,
    sequence_group        INTEGER NOT NULL,
    sequence_number       INTEGER NOT NULL,
    event_code            INTEGER NOT NULL,
    event_name            TEXT NOT NULL,
    pump_date_time        TEXT NOT NULL,
    estimated_date_time   TEXT NOT NULL,
    event_properties      JSONB NOT NULL,
    is_clock_change       BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (device_assignment_id, sequence_group, sequence_number, is_clock_change)
);
```

`event_properties` varies by `event_code` (e.g. a CGM reading has a glucose
value; a bolus event has insulin amounts), and is indexed with a GIN index
so it's queryable directly:

```sql
-- CGM readings from the last day
SELECT pump_date_time, event_properties->>'currentGlucoseDisplayValue' AS glucose
FROM events
WHERE event_code = 399
  AND pump_date_time > now() - interval '1 day'
ORDER BY pump_date_time;
```

Migrations are managed with [goose](https://github.com/pressly/goose) and
embedded into the binary, so `-l` applies them automatically — no separate
migration step is needed. To add a new migration (once you have the goose
CLI installed via `go install github.com/pressly/goose/v3/cmd/goose@latest`):

```sh
cd tandemdb
goose -dir migrations create add_something sql
```

## Project layout

```
cmd/tandemdata/   CLI entry point (-d / -l / -p)
cmd/tandemsync/   Long-running hourly insulin sync service (see below)
tandem/           Tandem Source auth + reports API client
tandemdb/         PostgreSQL schema, migrations, and load logic (tandemdata's own DB)
pumplog/          Parsing and summarizing raw pump-logs JSON
insulin/          Bolus/basal extraction + upsert logic, targeting health-db
```

## tandemsync — hourly insulin sync into health-db

`cmd/tandemsync` is a separate, long-running service (structured like
`health-sync` in the main health-app) that logs into Tandem Source once an
hour, fetches a recent window of bolus and basal pump-log events, and
upserts them into the `tandem_bolus` / `tandem_basal` tables in **health-db**
— the main health-app's PostgreSQL database, not tandemdata's own `events`
table. Those tables' schema is owned by `health-api`'s goose migrations
(`../health-api/database/migrations`) in the parent project.

**Note:** this `cmd/tandemsync` has been superseded by the standalone
[`health-tandem-sync`](../health-tandem-sync) module, which covers the same
sync plus CGM data and a one-time historical-load command
(`cmd/tandemload`). This section is left for reference while `tandemdata`
is phased out.

It's a different binary from `-d`/`-l`/`-p` (which remain a one-shot,
manually-run workflow against tandemdata's own database) because it talks to
a different database and runs continuously rather than on demand.

```sh
go run ./cmd/tandemsync
```

Config (env or `.env`):

| Variable | Default | Description |
|---|---|---|
| `TANDEM_USERNAME` / `TANDEM_PASSWORD` | (required) | Tandem Source login |
| `HEALTHDB_URL` | (required) | `postgres://...` URL for health-db (not `DATABASE_URL`, which is tandemdata's own DB) |
| `SYNC_INTERVAL_SECONDS` | `3600` | How often to sync |
| `SYNC_LOOKBACK_HOURS` | `6` | How much history to re-fetch each cycle, so a missed cycle or late-arriving event is still picked up |

Each cycle logs in fresh (Tandem Source's PKCE login flow is cheap enough to
redo hourly) rather than trying to keep a session alive, fetches only the
insulin-relevant event codes (3, 20, 21, 55, 59, 64, 65, 66) for the lookback
window, and upserts — so overlapping windows across cycles are safe and
won't create duplicates (`tandem_bolus` is keyed on `(device_assignment_id,
bolus_id)`, `tandem_basal` on `(device_assignment_id, sequence_group,
sequence_number)`).

To run it as part of the main stack, see the `tandemsync` service in the
root [`docker-compose.yml`](../docker-compose.yml).

## Development

```sh
go build ./...
go vet ./...
go test ./...
```

Tests cover pure logic (chunking, JSON merging, SQL building, migration file
parsing) without touching the network or a real database.
