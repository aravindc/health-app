# Health App

A self-hosted personal health dashboard for type 1 diabetes: continuous
glucose monitor (CGM) readings from Dexcom, insulin delivery from a Tandem
pump, analytics, and research glucose predictions, in one web interface.

## Features

- **Current reading:** a dial with the latest glucose, its trend arrow and
  the change since the previous reading, synced from Dexcom Share every 60
  seconds (`SYNC_INTERVAL_SECONDS`)
- **Glucose and insulin chart:** 24 hours of CGM readings coloured by range,
  with basal delivery and modelled food and correction bolus activity in
  synced panels; drag or use the jump buttons to move through the history
- **Insights cards:** time in range (strict 4–7 and medical 4–10 mmol/L),
  average, median, SD, CV, quartiles, highs and lows, GMI and more, each
  with its own selectable period
- **Heatmaps:** daily average and daily time in range for the last 90 days
- **Insulin data:** hourly bolus, basal and CGM sync from Tandem Source
- **Predicted glucose (optional):** the next 30 minutes on the chart and in
  the header, from the [health-predict](health-predict/README.md) research
  model (not a medical device)
- **History gap-fill** from a Nightscout MongoDB backend
- **Prometheus metrics** on an internal-only port

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
| Frontend     | React 19, TypeScript, Vite, SVG charts (Recharts for sparklines) |
| Backend      | Go, Gin, GORM                        |
| Database     | PostgreSQL (partitioned by month)    |
| Dexcom sync  | Go, Dexcom Share API                 |
| MongoDB sync | Go, MongoDB driver                   |
| Prediction   | Python 3.12, scikit-learn, FastAPI, uv (research only) |
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
| Prediction replica (optional) | localhost:9086 (PostgreSQL, profile `predict-replica`) |

Every port is published on `127.0.0.1` only, so they're reachable from
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

To rebuild and restart after an update, use
[`restart.sh`](#restarting-with-restartsh).

Containers are named after their services (`health-db`, `health-api`, …,
via `container_name:`), so plain engine commands work without the
compose-generated prefix, e.g. `docker logs -f health-api` or
`podman exec -it health-db psql -U <user> <db>`. Container names are
unique per engine, so only one copy of the stack can run on a host at a
time.

Bytebase has no `.env` — its admin account and the `health-db` connection
are both set up through its own first-run web UI at http://localhost:9085,
not via config.

### Restarting with restart.sh

`restart.sh` rebuilds and restarts the stack with Docker or Podman. Run it
from anywhere; it works from the repo root:

```bash
./restart.sh <docker|podman> <up|down-up> [--no-caddy] [--dry-run] [service...]
```

| Mode | What it does |
|---|---|
| `up` | `compose up -d --build`: rebuilds images and recreates only the containers whose image or config changed. Use it after a `git pull`. |
| `down-up` | Stops and removes the stack (`compose down`) first, then runs `up`. With service names, removes and recreates only those. Use it for a clean restart, e.g. after changing networks. |

| Option | |
|---|---|
| `service...` | Limit it to these services, e.g. `health-api health-fe`. Compose also starts their dependencies. |
| `--no-caddy` | Don't add `docker-compose.caddy.yml`. By default it's added whenever Caddy's network, `caddy-net`, exists, and skipped with a note otherwise. |
| `--dry-run` | Print the commands without running them. |

```bash
./restart.sh docker up                          # after a git pull
./restart.sh podman down-up                     # clean restart of everything
./restart.sh docker up health-fe                # rebuild and restart the UI only
./restart.sh docker down-up health-predict      # recreate one service
./restart.sh podman up --dry-run                # see what it would run
```

- **Data is never deleted.** `down-up` doesn't use `down -v`, and the
  database, Bytebase and model files are bind mounts.
- **The UI is stamped with the current commit** (`GIT_SHA`, shown as
  `<meta name="app-version">`).
- **It ends by listing the containers** (`compose ps`).
- **Podman:** it uses `podman compose` (Podman 4.7+), or `podman-compose`
  directly if that's missing. It sets `PODMAN_COMPOSE_PROVIDER` to
  `/usr/bin/podman-compose`, so `podman compose` uses podman-compose. If
  podman-compose is installed elsewhere (`command -v podman-compose`), change
  that line.

**If a build can't pull a base image**, e.g. `too many requests to
registry`: Docker Hub limits anonymous pulls per IP address, and a full
rebuild pulls several base images. Log in once (`docker login` or
`podman login docker.io`) for a higher limit, or wait and run it again;
images already pulled stay cached. If the error also lists a mirror that
times out (e.g. `docker.mirrors.ustc.edu.cn`), remove that mirror from
`/etc/containers/registries.conf` (Podman) or the `registry-mirrors` setting
in `/etc/docker/daemon.json` (Docker).

### Running with Podman

The compose file and Dockerfiles work unchanged under Podman:

- Base images are fully qualified (`docker.io/library/...`), so Podman never
  has to resolve short names or prompt for a registry.
- `health-api`'s healthcheck lives in `docker-compose.yml`, not its
  Dockerfile — Podman builds OCI-format images by default, which drop
  `HEALTHCHECK`, and the `service_healthy` dependencies below rely on it.
- Bind mounts (`health-db/data`, `bytebase/data`, `health-predict/models`,
  `health-predict/replica-db/data`) carry `:Z` so they're relabeled on
  SELinux hosts (Fedora/RHEL); it's a no-op elsewhere.
- Healthchecks (`health-api`, `health-predict`) also live in
  `docker-compose.yml` for the same reason.

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
`docker-compose.caddy.yml` to join `health-fe` and `health-api` to Caddy's
network, `caddy-net`. Caddy proxies to them by service name, so the
`127.0.0.1` host ports aren't involved:

```bash
docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d --build
```

The network must be called `caddy-net` and must already exist: it's
Caddy's own network (`docker network create caddy-net` if you create it
yourself). Every container on it can reach `health-api:8080` directly,
without Caddy's authentication, so keep only trusted containers there.

Matching Caddyfile:

```caddyfile
health.example.com {
    reverse_proxy health-fe:80
}
```

Caddy passes the original `Host` header through by default. Keep it that
way (no `header_up Host ...`): `health-api` treats a request as same-origin
only when the browser's `Origin` matches that `Host`.

### Security

The dashboard has no login of its own: **run it behind a reverse proxy that
authenticates every request**. With Caddy and Authentik, for example, use
Authentik's forward auth (its embedded outpost) in front of `health-fe`:

```caddyfile
health.example.com {
    route {
        reverse_proxy /outpost.goauthentik.io/* http://authentik-server:9000
        forward_auth http://authentik-server:9000 {
            uri /outpost.goauthentik.io/auth/caddy
            copy_headers X-Authentik-Username X-Authentik-Groups X-Authentik-Email
            trusted_proxies private_ranges
        }
        reverse_proxy health-fe:80
    }
}
```

Create a Proxy Provider (forward auth, single application) for the domain
in Authentik and add it to the embedded outpost.

The rest of the stack is locked down by default:

- **Ports:** every published port is bound to `127.0.0.1`. Prometheus
  metrics and the prediction service aren't published at all. Behind Caddy,
  `health-api` is also on `caddy-net` (see
  [Running behind Caddy](#running-behind-caddy)), where other containers
  can reach it without authentication.
- **Containers** run as non-root users. `health-fe`'s nginx sends a strict
  Content-Security-Policy and other security headers, and hides its version.
- **health-api:**
  - It runs in gin's release mode unless `APP_ENV=development`.
  - It serves only the last `MAX_HISTORY_DAYS` (default 90) of data.
  - It returns generic error messages and logs the details.
  - It trusts `X-Forwarded-For` only from `TRUSTED_PROXIES`, for its per-IP
    rate limit.
  - `API_KEYS` requires an `X-API-Key` header (compared in constant time)
    on every endpoint except `/health`. The dashboard doesn't send one, so
    leave it unset when you use the dashboard, and rely on the proxy.
- **CI** scans dependencies on every push and weekly: `govulncheck`,
  `npm audit` and `pip-audit`.

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
3. Start it with the rest of the stack (`docker compose up -d`), or on its
   own:

   ```bash
   docker compose up -d --build health-predict health-api
   ```

   Without models, the service starts but reports unhealthy, and the
   dashboard shows no forecast.

Training runs on the host with `make` targets in `health-predict/`. By
default the pipeline reads `health-db` with the root `.env`'s `POSTGRES_*`
settings, like every other module; to train on another machine from a copy
of the server's data, use the optional `health-predict-replica` service
(profile `predict-replica`) with `PREDICT_DB=replica`. See
[health-predict's README](health-predict/README.md).

**Every prediction is recorded** in `health-db`, whether or not the
dashboard is open. With `PREDICT_URL` set, health-api predicts each new
reading in the background and stores one row per horizon in
`glucose_predictions`. Each minute it also looks back 6 hours:
- Readings Dexcom delivered in a batch, which were never the latest, are
  predicted from the data up to their own time.
- Readings predicted while the pump data lagged are predicted again once
  that data arrives. Both versions are kept, told apart by
  `pump_data_until`.

The view `glucose_prediction_outcomes` adds what actually happened: the
Dexcom reading nearest T + horizon, the error (predicted − actual) and
whether it fell inside the 80% and 95% ranges. For example, live accuracy
by glucose level over the last 14 days:

```sql
SELECT width_bucket(current_mmol, array[4, 6, 8, 10, 12, 15]) AS band,
       horizon_minutes,
       count(*)                                   AS n,
       round(avg(error_mmol)::numeric, 2)         AS mean_error,
       round(avg(abs(error_mmol))::numeric, 2)    AS mae,
       round(100 * avg(within_80::int)::numeric)  AS pct_in_80
FROM glucose_prediction_outcomes
WHERE actual_mmol IS NOT NULL AND NOT actual_censored
  AND predicted_at > now() - interval '14 days'
GROUP BY 1, 2 ORDER BY 1, 2;
```

To see what pump data lag costs, compare the first and the last
`pump_data_until` for each reading.

Without `PREDICT_URL`, or when the latest reading is more than 15 minutes
old, there's no forecast. Pump data arrives through Tandem Source, so it
often lags the Dexcom readings; when it's more than 30 minutes behind, the
prediction notes that recent insulin isn't included.

## Configuration

Config is split across a root `.env` plus one `.env` per service directory,
each loaded via `env_file:` in `docker-compose.yml` (Compose supports a list
there, and later files win on key collisions).

The root `.env` holds the shared database credentials
(`POSTGRES_HOST/PORT/DB/USER/PASSWORD`), since `health-db`, `health-sync`,
`health-mongo-sync`, `health-tandem-sync`, `health-api` and
health-predict's training pipeline all connect to the same Postgres
instance with the same login. That way the DB password lives in exactly one
file, not four. Every other secret — Dexcom credentials, MongoDB URI, Tandem
credentials — stays in its own service's `.env`, so e.g. `health-api` never
sees your Dexcom password.

| File | Used by | Holds |
|---|---|---|
| `.env` (root) | `health-db`, `health-sync`, `health-mongo-sync`, `health-api`, `health-tandem-sync`, health-predict's pipeline | `POSTGRES_*` |
| `health-sync/.env` | `health-sync` | `BRIDGE_*`, `APPLICATION_ID`, sync settings |
| `health-mongo-sync/.env` | `health-mongo-sync` | `MONGO_*` |
| `health-api/.env` | `health-api` | Glucose ranges, `APP_ENV` (production unless `development`), optional `API_KEYS`, `PREDICT_URL`, `MAX_HISTORY_DAYS`, `TRUSTED_PROXIES`, `CORS_ALLOWED_ORIGINS`, `METRICS_PORT` |
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
# Only to train health-predict on this machine:
cp health-predict/.env.example health-predict/.env
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
Data endpoints serve only the last `MAX_HISTORY_DAYS` (default 90): hour and day parameters beyond it get a 400, and
`from`/`to` windows are clipped to it.

Prometheus metrics are served separately, at `/metrics` on port `9090` (`METRICS_PORT`). That port isn't published by `docker-compose.yml` or proxied by health-fe, so scrape it from a container on health-api's network: `http://health-api:9090/metrics`.

| Method | Endpoint                          | Description                              |
|--------|-----------------------------------|------------------------------------------|
| GET    | `/health`                         | Health check (database reachable)        |
| GET    | `/`                               | Welcome message                          |
| GET    | `/lastreading`                    | Most recent glucose reading + trend      |
| GET    | `/latest/:page`                   | Latest readings, 36 (~3 h) per page (page 1 = newest) |
| GET    | `/last4h/:page`                   | Readings in 4-hour pages (48 per page)   |
| GET    | `/last12h/:page`                  | Readings in 12-hour pages (144 per page) |
| GET    | `/lastxh/:hours`                  | Readings for the last N hours            |
| GET    | `/lastxh/:hours/offset/:offset`   | Readings for N hours ending offset ago   |
| GET    | `/chart?from=&to=`                | Glucose readings for an arbitrary window (RFC3339) |
| GET    | `/bolus?from=&to=`                | Bolus doses + modeled food/correction activity for a window |
| GET    | `/basal?from=&to=`                | Commanded basal-rate step points for a window |
| GET    | `/firstdate`                      | Earliest date with data (or the start of the history window) |
| GET    | `/dailyavg/:days`                 | Daily averages for past N days           |
| GET    | `/dailytir/:days`                 | Daily Time In Range for past N days      |
| GET    | `/avgmmol/:period`                | Average mmol/L for period (1h–90d)       |
| GET    | `/quart/:days`                    | Quartile distribution for past N days    |
| GET    | `/gmi/:days`                      | Glucose Management Indicator (more than 7 days)|
| GET    | `/percentinrange/:hours`          | % of readings in strict range            |
| GET    | `/prediction`                     | Predicted glucose 15–60 min after the latest reading, from health-predict (`{"available": false, "reason": ...}` when there's none) |
| GET    | `/insightstats/:hours`            | All Insights card stats for the last N hours (% in range, mean, median, SD, CV, quartiles, highs/lows, unicorns, range splits, 288-point sparkline) |
| GET    | `/timeinrange/:hours`             | Consecutive time in strict range         |
| GET    | `/7dsparkline/:day`               | One day's readings for a 7-day sparkline (day 1 = today) |
| GET    | `/last24hsparkline`               | Readings from the last 24 hours, for a sparkline |

## Development

Each module has its own tests, run in CI (`.github/workflows/ci.yml`) for
the modules a change touches:

```bash
(cd health-api && go test ./...)        # likewise health-sync, health-mongo-sync, health-tandem-sync
(cd health-fe && npm ci && npm run lint && npm test)
(cd health-predict && uv sync --group dev --extra pipeline --extra ml --extra api --extra dashboard && make lint test)
```

`make install` in health-predict also installs PyTorch (several GB) for the
sequence models; the tests skip those models without it.

For the frontend with live reload, run `npm run dev` in `health-fe`: the
Vite dev server proxies `/api` to `health-api` on `localhost:9082`, as
nginx does in the container.

`.github/workflows/security.yml` scans dependencies on every push and PR and
weekly: `govulncheck` for each Go module, `npm audit` for health-fe's
production dependencies and `pip-audit` for health-predict's API image.
