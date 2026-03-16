# Health App

A personal health monitoring dashboard for tracking continuous glucose monitor (CGM) data from Dexcom devices. Provides real-time glucose readings, historical trends, and analytics through a self-hosted web interface.

## Features

- Real-time glucose readings synced from Dexcom Share API every 60 seconds
- 24-hour interactive glucose chart with scrollable history
- Time In Range (TIR) tracking — strict (4–7 mmol/L) and medical (4–10 mmol/L)
- Glucose Management Indicator (GMI) based on 90-day averages
- Daily average and quartile distribution analytics
- 120-day heatmaps for daily averages and TIR percentages
- Prometheus metrics endpoint for monitoring

## Architecture

```
health-fe/      React + TypeScript frontend (Vite)
health-api/     Go REST API (Gin)
health-sync/    Go background sync service (Dexcom → PostgreSQL)
health-db/      PostgreSQL schema and migrations
```

## Stack

| Layer    | Technology                  |
|----------|-----------------------------|
| Frontend | React 19, TypeScript, Vite, Recharts |
| Backend  | Go, Gin, GORM               |
| Database | PostgreSQL (partitioned by month) |
| Sync     | Go, Dexcom Share API        |
| Infra    | Docker Compose, nginx, pgAdmin |

## Running

**Prerequisites:** Docker and Docker Compose.

1. Copy `.env.example` to `.env` and fill in your credentials (see below).
2. Start all services:

```bash
docker-compose up -d
```

| Service  | URL                        |
|----------|----------------------------|
| Frontend | http://localhost:9083       |
| API      | http://localhost:9082       |
| pgAdmin  | http://localhost:9085       |
| Database | localhost:9084 (PostgreSQL) |

## Configuration

Create a `.env` file in the project root:

```env
# Database
POSTGRES_HOST=health-db
POSTGRES_PORT=5432
POSTGRES_DB=health
POSTGRES_USER=admin
POSTGRES_PASSWORD=your_secure_password

# Dexcom Share API
BRIDGE_SERVER=shareous1.dexcom.com
BRIDGE_USER=your_dexcom_email
BRIDGE_PASS=your_dexcom_password
APPLICATION_ID=your_dexcom_app_id

# Sync settings
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
| GET    | `/dailyavg/:days`                 | Daily averages for past N days           |
| GET    | `/dailytir/:days`                 | Daily Time In Range for past N days      |
| GET    | `/avgmmol/:period`                | Average mmol/L for period (1h–90d)       |
| GET    | `/quart/:days`                    | Quartile distribution for past N days    |
| GET    | `/gmi/:days`                      | Glucose Management Indicator (min 7 days)|
| GET    | `/percentinrange/:hours`          | % of readings in strict range            |
| GET    | `/timeinrange/:hours`             | Consecutive time in strict range         |
| PUT    | `/insulin`                        | Log an insulin injection                 |
