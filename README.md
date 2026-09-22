# Health App

A personal health monitoring dashboard for tracking continuous glucose monitor (CGM) data from Dexcom devices. Provides real-time glucose readings, historical trends, and analytics through a self-hosted web interface.

## Features

- Real-time glucose readings synced from Dexcom Share API every 60 seconds
- Day-anchored interactive glucose chart with per-day navigation
- Time In Range (TIR) tracking — strict (4–7 mmol/L) and medical (4–10 mmol/L)
- Glucose Management Indicator (GMI) based on 90-day averages
- Daily average and quartile distribution analytics
- 120-day heatmaps for daily averages and TIR percentages
- Historical gap-fill from a Nightscout MongoDB backend
- Hourly insulin (bolus + basal) sync from a Tandem Source insulin pump
- Prometheus metrics endpoint for monitoring

## Architecture

```
health-fe/           React + TypeScript frontend (Vite)
health-api/          Go REST API (Gin) — also owns the database schema (goose migrations)
health-sync/         Go background sync service (Dexcom → PostgreSQL)
health-mongo-sync/   Go one-shot gap-fill service (MongoDB → PostgreSQL)
health-db/           PostgreSQL data volume (schema lives in health-api's migrations)
health-tandem-sync/  Go services (Tandem Source → PostgreSQL insulin + CGM data)
tandemdata/          Superseded by health-tandem-sync; kept for reference during the transition
```

## Stack

| Layer        | Technology                           |
|--------------|--------------------------------------|
| Frontend     | React 19, TypeScript, Vite, Recharts |
| Backend      | Go, Gin, GORM                        |
| Database     | PostgreSQL (partitioned by month)    |
| Dexcom sync  | Go, Dexcom Share API                 |
| MongoDB sync | Go, MongoDB driver                   |
| Infra        | Docker Compose, nginx, Bytebase      |

## Running

**Prerequisites:** Docker and Docker Compose.

1. Fill in your credentials in each service's own `.env` file (see [Configuration](#configuration) below).
2. Start all services:

```bash
docker compose up -d
```

| Service  | URL                        |
|----------|----------------------------|
| Frontend | http://localhost:9083       |
| API      | http://localhost:9082       |
| Bytebase | http://localhost:9085       |
| Database | localhost:9084 (PostgreSQL) |

Bytebase has no `.env` — its admin account and the `health-db` connection
are both set up through its own first-run web UI at http://localhost:9085,
not via config.

### Database schema

`health-api` owns the database schema: on startup it runs any pending
[goose](https://github.com/pressly/goose) migrations from
`health-api/database/migrations` before serving any requests, so a brand-new
`health-db` container ends up with the full schema automatically (no manual
init step, no `docker-entrypoint-initdb.d` scripts). `health-sync`,
`health-mongo-sync`, and `health-tandem-sync` all `depends_on: health-api` with a
`service_healthy` condition (backed by `health-api`'s `/health` endpoint and
a Docker healthcheck), so Compose won't start them until migrations have
finished — avoiding a race where they'd try to write to tables that don't
exist yet on a truly fresh database.

### Filling historical gaps from MongoDB

If you have a Nightscout MongoDB backend, run the one-shot gap-fill service after setting the `MONGO_*` variables in `health-mongo-sync/.env`:

```bash
docker compose run --rm health-mongo-sync
```

This is fully idempotent — re-running it is safe and will only insert records that are not already in Postgres.

### Syncing insulin and CGM data from a Tandem pump

If you have a Tandem insulin pump (Tandem Source account), the
`health-tandem-sync` service (binary `tandemsync`, from the
`health-tandem-sync` module) logs in every hour and upserts recent bolus,
basal-rate, and CGM data into the `tandem_bolus` / `tandem_basal` /
`tandem_cgm` tables. Set `TANDEM_USERNAME` / `TANDEM_PASSWORD` in
`health-tandem-sync/.env` (see
[`health-tandem-sync/.env.example`](health-tandem-sync/.env.example)), then:

```bash
docker compose up -d health-tandem-sync
```

Each cycle fetches from a watermark (the latest timestamp already stored)
rather than a fixed lookback, so a missed cycle or restart is picked up
automatically on the next successful run instead of leaving a gap — see
[`health-tandem-sync`](health-tandem-sync) for details, including
`cmd/tandemload`, a one-time historical backfill command (run manually, not
part of `docker compose up`) for the full pump event history.

`tandemload` runs on the host, not in a container, so `POSTGRES_HOST`/`PORT`
in the root `.env` (`health-db:5432`, correct for containers on the
`health_be` network) don't resolve. Use the wrapper script instead of
`go run ./cmd/tandemload` directly — it looks up `health-db`'s actual
host-mapped port via `docker compose port` and overrides `POSTGRES_HOST`/
`PORT`/`DB_SSL_MODE` accordingly:

```bash
cd health-tandem-sync
./run-tandemload.sh                    # full available history
./run-tandemload.sh -start 2024-01-01  # any tandemload flag works
```

## Configuration

Config is split across a root `.env` plus one `.env` per service directory,
each loaded via `env_file:` in `docker-compose.yml` (Compose supports a list
there, and later files win on key collisions).

The root `.env` holds only the shared database credentials
(`POSTGRES_HOST/PORT/DB/USER/PASSWORD`), since `health-db`, `health-sync`,
`health-mongo-sync`, and `health-api` all connect to the same Postgres
instance with the same login. That way the DB password lives in exactly one
file, not four. Every other secret — Dexcom credentials, MongoDB URI, Tandem
credentials — stays in its own service's `.env`, so e.g. `health-api` never
sees your Dexcom password.

| File | Used by | Holds |
|---|---|---|
| `.env` (root) | `health-db`, `health-sync`, `health-mongo-sync`, `health-api`, `health-tandem-sync` | `POSTGRES_*` |
| `health-sync/.env` | `health-sync` | `BRIDGE_*`, `APPLICATION_ID`, sync settings |
| `health-mongo-sync/.env` | `health-mongo-sync` | `MONGO_*` |
| `health-api/.env` | `health-api` | Glucose ranges, `API_KEYS` |
| `health-tandem-sync/.env` | `health-tandem-sync` | `TANDEM_USERNAME/PASSWORD` only — `POSTGRES_*` comes from the root `.env` (see its own `env_file:` list in `docker-compose.yml`), not duplicated here |

`health-db` itself has no per-service `.env` file — the root `.env` is all
it needs. Neither does `bytebase` — see [Database schema](#database-schema)
above.

Copy each `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
for d in health-sync health-mongo-sync health-api health-tandem-sync; do
  cp "$d/.env.example" "$d/.env"
done
```

See each `.env.example` for the full, commented variable list — Dexcom
Share credentials, glucose target ranges, MongoDB gap-fill settings, etc.
are documented there rather than repeated here. `VITE_API_URL` is not read
from any `.env`; it's a docker-compose build arg for `health-fe` (see
`docker-compose.yml`).

## API Endpoints

All endpoints require an `X-API-Key` header if `API_KEYS` is configured. Exceptions: `/health` and `/metrics`.

| Method | Endpoint                          | Description                              |
|--------|-----------------------------------|------------------------------------------|
| GET    | `/health`                         | Health check                             |
| GET    | `/metrics`                        | Prometheus metrics                       |
| GET    | `/lastreading`                    | Most recent glucose reading + trend      |
| GET    | `/lastxh/:hours`                  | Readings for the last N hours            |
| GET    | `/lastxh/:hours/offset/:offset`   | Readings for N hours ending offset ago   |
| GET    | `/chart?from=&to=`                | Glucose readings for an arbitrary window (RFC3339) |
| GET    | `/bolus?from=&to=`                | Bolus doses + modeled food/correction activity for a window |
| GET    | `/basal?from=&to=`                | Commanded basal-rate step points for a window |
| GET    | `/dailyavg/:days`                 | Daily averages for past N days           |
| GET    | `/dailytir/:days`                 | Daily Time In Range for past N days      |
| GET    | `/avgmmol/:period`                | Average mmol/L for period (1h–90d)       |
| GET    | `/quart/:days`                    | Quartile distribution for past N days    |
| GET    | `/gmi/:days`                      | Glucose Management Indicator (min 7 days)|
| GET    | `/percentinrange/:hours`          | % of readings in strict range            |
| GET    | `/timeinrange/:hours`             | Consecutive time in strict range         |
| PUT    | `/insulin`                        | Log an insulin injection                 |
