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

1. Fill in your credentials in `.env` (see [Configuration](#configuration) below).
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

If you have a Nightscout MongoDB backend, run the one-shot gap-fill service after setting the `MONGO_*` variables in `.env`:

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

All configuration lives in `.env` in the project root.

```env
# Database
POSTGRES_HOST=health-db
POSTGRES_PORT=5432
POSTGRES_DB=health
POSTGRES_USER=admin
POSTGRES_PASSWORD=your_secure_password

# Dexcom Share API
BRIDGE_SERVER=shareous1.dexcom.com   # use "US" for US Dexcom accounts
BRIDGE_USER=your_dexcom_email
BRIDGE_PASS=your_dexcom_password
APPLICATION_ID=your_dexcom_app_id

# Dexcom sync settings
SYNC_INTERVAL_SECONDS=60
BG_QUERY_MINUTES=1440
RECORD_COUNT=288

# Glucose target ranges (mmol/L)
MIN_MMOL=4.0
STRICT_MAX_MMOL=7.0
MEDICAL_MAX_MMOL=10.0

# Environment
NS_ENV=development
VITE_API_URL=http://localhost:9082

# pgAdmin
PGADMIN_DEFAULT_EMAIL=admin@example.com
PGADMIN_DEFAULT_PASSWORD=your_pgadmin_password

# Optional API key auth (comma-separated)
# API_KEYS=key1,key2

# MongoDB gap-fill (health-mongo-sync) — uncomment to enable
# MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority
# MONGO_DB=nightscout
# MONGO_COLLECTION=entries
# MONGO_SYNC_LOOKBACK_DAYS=90   # omit to sync all history back to 2020-01-01
```

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
