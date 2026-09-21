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
health-api/          Go REST API (Gin)
health-sync/         Go background sync service (Dexcom → PostgreSQL)
health-mongo-sync/   Go one-shot gap-fill service (MongoDB → PostgreSQL)
health-db/           PostgreSQL schema and migrations
tandemdata/          Go CLI + tandemsync service (Tandem Source → PostgreSQL insulin data)
```

## Stack

| Layer        | Technology                           |
|--------------|--------------------------------------|
| Frontend     | React 19, TypeScript, Vite, Recharts |
| Backend      | Go, Gin, GORM                        |
| Database     | PostgreSQL (partitioned by month)    |
| Dexcom sync  | Go, Dexcom Share API                 |
| MongoDB sync | Go, MongoDB driver                   |
| Infra        | Docker Compose, nginx, pgAdmin       |

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
| pgAdmin  | http://localhost:9085       |
| Database | localhost:9084 (PostgreSQL) |

### Filling historical gaps from MongoDB

If you have a Nightscout MongoDB backend, run the one-shot gap-fill service after setting the `MONGO_*` variables in `health-mongo-sync/.env`:

```bash
docker compose run --rm health-mongo-sync
```

This is fully idempotent — re-running it is safe and will only insert records that are not already in Postgres.

### Syncing insulin data from a Tandem pump

If you have a Tandem insulin pump (Tandem Source account), the `tandemsync`
service logs in every hour and upserts recent bolus and basal-rate data into
the `tandem_bolus` / `tandem_basal` tables. Set `TANDEM_USERNAME` /
`TANDEM_PASSWORD` in `tandemdata/.env` (see
[`tandemdata/.env.example`](tandemdata/.env.example)), then:

```bash
docker compose up -d tandemsync
```

It's fully idempotent — each cycle re-fetches a small lookback window and
upserts, so a missed cycle or restart doesn't create duplicates or lose
data. See [`tandemdata/README.md`](tandemdata/README.md#tandemsync--hourly-insulin-sync-into-health-db)
for details, including the standalone `tandemdata` CLI this service is built
on top of (for one-off downloads/backfills of the full pump event history).

## Configuration

Config is split across a root `.env` plus one `.env` per service directory,
each loaded via `env_file:` in `docker-compose.yml` (Compose supports a list
there, and later files win on key collisions).

The root `.env` holds only the shared database credentials
(`POSTGRES_HOST/PORT/DB/USER/PASSWORD`), since `health-db`, `health-sync`,
`health-mongo-sync`, and `health-api` all connect to the same Postgres
instance with the same login. That way the DB password lives in exactly one
file, not four. Every other secret — Dexcom credentials, MongoDB URI,
pgAdmin password, Tandem credentials — stays in its own service's `.env`,
so e.g. `health-api` never sees your Dexcom password and `pgadmin` never
sees the Mongo URI.

| File | Used by | Holds |
|---|---|---|
| `.env` (root) | `health-db`, `health-sync`, `health-mongo-sync`, `health-api`, `tandemsync` (indirectly, see below) | `POSTGRES_*` |
| `health-sync/.env` | `health-sync` | `BRIDGE_*`, `APPLICATION_ID`, sync settings |
| `health-mongo-sync/.env` | `health-mongo-sync` | `MONGO_*` |
| `health-api/.env` | `health-api` | Glucose ranges, `API_KEYS` |
| `pgadmin/.env` | `pgadmin` | `PGADMIN_DEFAULT_EMAIL/PASSWORD` |
| `tandemdata/.env` | `tandemsync` | `TANDEM_USERNAME/PASSWORD` only — `HEALTHDB_URL` is built by docker-compose itself from the root `.env`'s `POSTGRES_*`, not duplicated here |

`health-db` itself has no per-service `.env` file — the root `.env` is all
it needs.

Copy each `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
for d in health-sync health-mongo-sync health-api pgadmin; do
  cp "$d/.env.example" "$d/.env"
done
cp tandemdata/.env.example tandemdata/.env
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
| GET    | `/daychart/:date`                 | Readings for a full calendar day (YYYY-MM-DD) |
| GET    | `/dailyavg/:days`                 | Daily averages for past N days           |
| GET    | `/dailytir/:days`                 | Daily Time In Range for past N days      |
| GET    | `/avgmmol/:period`                | Average mmol/L for period (1h–90d)       |
| GET    | `/quart/:days`                    | Quartile distribution for past N days    |
| GET    | `/gmi/:days`                      | Glucose Management Indicator (min 7 days)|
| GET    | `/percentinrange/:hours`          | % of readings in strict range            |
| GET    | `/timeinrange/:hours`             | Consecutive time in strict range         |
| PUT    | `/insulin`                        | Log an insulin injection                 |
