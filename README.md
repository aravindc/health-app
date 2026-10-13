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
- Predicted glucose for the next 30 minutes, from the health-predict research model (optional)
- Prometheus metrics endpoint for monitoring

## Architecture

```
health-fe/           React + TypeScript frontend (Vite)
health-api/          Go REST API (Gin) — also owns the database schema (goose migrations)
health-sync/         Go background sync service (Dexcom → PostgreSQL)
health-mongo-sync/   Go one-shot gap-fill service (MongoDB → PostgreSQL)
health-db/           PostgreSQL data volume (schema lives in health-api's migrations)
health-tandem-sync/  Go services (Tandem Source → PostgreSQL insulin + CGM data)
health-predict/      Python glucose prediction (research): ML pipeline + FastAPI service
```

## Stack

| Layer        | Technology                           |
|--------------|--------------------------------------|
| Frontend     | React 19, TypeScript, Vite, Recharts |
| Backend      | Go, Gin, GORM                        |
| Database     | PostgreSQL (partitioned by month)    |
| Dexcom sync  | Go, Dexcom Share API                 |
| MongoDB sync | Go, MongoDB driver                   |
| Prediction   | Python, scikit-learn, FastAPI (research only) |
| Infra        | Docker/Podman Compose, nginx, Bytebase |

## Running

**Prerequisites:** Docker with Docker Compose, or Podman (4.7+) with
`podman compose` (backed by `podman-compose` or `docker-compose`). With
Podman, substitute `podman compose` for `docker compose` in every command
below.

1. Fill in your credentials in each service's own `.env` file (see [Configuration](#configuration) below).
2. Start all services. Compose creates the `health_fe` (10.89.2.0/24) and
   `health_be` (10.89.3.0/24) networks itself, with fixed names and subnets
   so host firewall rules can match on them:

```bash
docker compose up -d
```

| Service  | URL                        |
|----------|----------------------------|
| Frontend | http://localhost:9083       |
| API      | http://localhost:9083/api (via the frontend's nginx), or directly at http://localhost:9082 |
| Bytebase | http://localhost:9085       |
| Database | localhost:9084 (PostgreSQL) |

All four ports are published on `127.0.0.1` only, so they're reachable from
this host but not from the rest of the network. To reach them from another
machine, use an SSH tunnel (e.g. `ssh -L 9083:localhost:9083 <host>`) rather
than changing the bindings.

The frontend calls the API at `/api` on its own origin: `health-fe`'s nginx
forwards `/api/*` to `health-api:8080` with the `/api` prefix stripped
(see [`health-fe/nginx.conf`](health-fe/nginx.conf)), so the browser never
makes a cross-origin request and no CORS setup is needed. nginx looks up
`health-api`'s address once when it starts, so compose restarts `health-fe`
whenever `health-api` is recreated. If your compose tool ignores
`depends_on: restart: true`, redeploy both together
(`up -d --build health-api health-fe`), or `/api` returns 502 until
`health-fe` restarts.

Containers are named after their services (`health-db`, `health-api`, …,
via `container_name:`), so plain engine commands work without the
compose-generated prefix, e.g. `docker logs -f health-api` or
`podman exec -it health-db psql -U <user> <db>`. Container names are
unique per engine, so only one copy of the stack can run on a host at a
time.

Bytebase has no `.env` — its admin account and the `health-db` connection
are both set up through its own first-run web UI at http://localhost:9085,
not via config.

### Running with Podman

The compose file and Dockerfiles work unchanged under Podman:

- Base images are fully qualified (`docker.io/library/...`), so Podman never
  has to resolve short names or prompt for a registry.
- `health-api`'s healthcheck lives in `docker-compose.yml`, not its
  Dockerfile — Podman builds OCI-format images by default, which drop
  `HEALTHCHECK`, and the `service_healthy` dependencies below rely on it.
- Bind mounts (`health-db/data`, `bytebase/data`) carry `:Z` so they're
  relabeled on SELinux hosts (Fedora/RHEL); it's a no-op elsewhere.

Under rootless Podman, files in those bind-mounted directories are owned by
subordinate UIDs, so removing them from the host needs
`podman unshare rm -rf health-db/data`. `health-tandem-sync/run-tandemload.sh`
uses `docker compose` if `docker` is on `PATH`, otherwise `podman compose`;
set `COMPOSE="podman compose"` to force it.

To check which commit a running UI was built from, view the page source and
look for `<meta name="app-version" content="...">`. Docker builds can't see
`.git`, so pass the commit in when building, otherwise it reads `unknown`:

```bash
GIT_SHA=$(git rev-parse --short HEAD) docker compose up -d --build health-fe
```

### Running behind Caddy

If Caddy runs as a container in its own compose stack, add
`docker-compose.caddy.yml` to join `health-fe` to Caddy's network. Caddy
proxies everything to `health-fe`, whose nginx serves the UI and forwards
`/api` to `health-api`, so the `127.0.0.1` host ports aren't involved:

```bash
CADDY_NETWORK=caddy_default \
  docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d --build
```

`CADDY_NETWORK` is the name of Caddy's network (list them with
`docker network ls`). It must already exist, and can go in the root `.env`
instead of the command line.

Matching Caddyfile:

```caddyfile
health.example.com {
    reverse_proxy health-fe:80
}
```

Caddy passes the original `Host` header through by default. Keep it that
way (no `header_up Host ...`): `health-api` treats a request as same-origin
only when the browser's `Origin` matches that `Host`.

### Database schema

`health-api` owns the database schema: on startup it runs any pending
[goose](https://github.com/pressly/goose) migrations from
`health-api/database/migrations` before serving any requests, so a brand-new
`health-db` container ends up with the full schema automatically (no manual
init step, no `docker-entrypoint-initdb.d` scripts). `health-sync`,
`health-mongo-sync`, and `health-tandem-sync` all `depends_on: health-api` with a
`service_healthy` condition (backed by `health-api`'s `/health` endpoint and
a healthcheck defined in `docker-compose.yml`), so Compose won't start them until migrations have
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

### Predicted glucose (health-predict)

The dashboard can show predicted glucose for the 30 minutes after the
latest reading: a dashed line with shaded 80% and 95% ranges on the Sensor
glucose chart, and the 15- and 30-minute values in a Predicted block on
the right of the header. The predictions come from
[`health-predict`](health-predict/README.md), a research model trained on
this pump and CGM data. **It is not a medical device, and its predictions
must not be used for insulin doses or treatment decisions.** The
dashboard shows this disclaimer next to the forecast.

`health-api` builds each request from its own tables, the same way
health-predict builds its training data: Tandem and Dexcom CGM readings
from the last 6 hours, basal rate changes and boluses. It calls the
`health-predict` service once per new reading and caches the answer, at
`GET /prediction`. The service is on the internal `health_be` network only.

1. Train the models, or copy an existing `models/` folder into
   `health-predict/models/` (it's not in git: the models are built from
   personal health data). See [health-predict's README](health-predict/README.md).
2. Set `PREDICT_URL=http://health-predict:8000` in `health-api/.env`.
3. Start the service, which is behind the `predict` profile:

   ```bash
   docker compose --profile predict up -d --build health-predict health-api
   ```

   Or set `COMPOSE_PROFILES=predict` in the root `.env` so a plain
   `docker compose up -d` includes it.

Training runs on the host with `make` targets in `health-predict/`. By
default the pipeline reads `health-db` with the root `.env`'s `POSTGRES_*`
settings, like every other module; to train on another machine from a copy
of the server's data, use the optional `health-predict-replica` service
(profile `predict-replica`) with `PREDICT_DB=replica`. See
[health-predict's README](health-predict/README.md).

Without `PREDICT_URL`, or when the latest reading is more than 15 minutes
old, there's no forecast. Pump data arrives through Tandem Source, so it
often lags the Dexcom readings; when it's more than 30 minutes behind, the
prediction notes that recent insulin isn't included.

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
| `health-api/.env` | `health-api` | Glucose ranges, `API_KEYS`, `APP_ENV` (production unless set to `development`) |
| `health-predict/.env` | health-predict's training pipeline (run on the host) | `PREDICT_DB`, host overrides; replica and `dbsync` settings |
| `health-predict/replica.env` | `health-predict-replica` (optional) | The replica Postgres's own credentials |
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
by any service at runtime; it's a docker-compose build arg for `health-fe`,
baked into the frontend bundle at build time. It defaults to `/api`
(proxied by `health-fe`'s nginx, and by the Vite dev server for
`npm run dev`) and only needs overriding, from the shell or the root
`.env`, to point the UI at an API on another origin.

## API Endpoints

All endpoints require an `X-API-Key` header if `API_KEYS` is configured, except `/health`.

Prometheus metrics are served separately, at `/metrics` on port `9090` (`METRICS_PORT`). That port isn't published by `docker-compose.yml` or proxied by health-fe, so scrape it from a container on health-api's network: `http://health-api:9090/metrics`.

| Method | Endpoint                          | Description                              |
|--------|-----------------------------------|------------------------------------------|
| GET    | `/health`                         | Health check                             |
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
| GET    | `/gmi/:days`                      | Glucose Management Indicator (more than 7 days)|
| GET    | `/percentinrange/:hours`          | % of readings in strict range            |
| GET    | `/prediction`                     | Predicted glucose 15–60 min after the latest reading, from health-predict (`{"available": false, "reason": ...}` when there's none) |
| GET    | `/insightstats/:hours`            | All Insights card stats for the last N hours (% in range, mean, median, SD, CV, quartiles, highs/lows, unicorns, range splits, 288-point sparkline) |
| GET    | `/timeinrange/:hours`             | Consecutive time in strict range         |
