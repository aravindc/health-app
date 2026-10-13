# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-10-13

### Security

- **health-api: a single request could return the whole reading history**
  ([GHSA-pqw5-gg8q-4r5v](https://github.com/aravindc/health-app/security/advisories/GHSA-pqw5-gg8q-4r5v)).
  Endpoints taking hours, days or a `from`/`to` window had no upper bound,
  so one request could return every reading ever stored (~73 MB on a
  four-year history). The API now serves only the last `MAX_HISTORY_DAYS`
  (default 90):
  - Hour and day parameters beyond that are rejected with 400.
  - `/chart`, `/bolus` and `/basal` windows are clipped to it.
  - `/firstdate` reports its start, so the chart pages back no further.
  - Paged and daily endpoints return nothing older.

- **health-api: database errors were sent to clients**
  ([GHSA-9v5g-jrhj-5mj7](https://github.com/aravindc/health-app/security/advisories/GHSA-9v5g-jrhj-5mj7)).
  Failed requests answered with the raw database or driver error, which
  can include the database user, name and host. The public `/health`
  endpoint did the same. They now answer with a generic message (`/health`:
  `"database": "unavailable"`), with the same status codes, and the full
  error goes to the server log along with the request's route.

- **Go 1.27.2 for all Go services.** Go advisories published since the last
  scan affected health-api, health-sync, health-mongo-sync and
  health-tandem-sync through the standard library (`crypto/tls`,
  `net/http`, HTTP/2, `net/textproto`), fixed in Go 1.27.2. Each module now
  requires Go 1.27.2, and the Dockerfiles pin `golang:1.27.2-alpine`.
  health-api also updates `golang.org/x/net` to v0.60.0 (four HTTP/2
  advisories). govulncheck now reports no reachable vulnerabilities.

### Added

- **health-predict: glucose prediction (research).** The t1danalysis
  project moves into the repo as `health-predict/`: the ML pipeline that
  trains models on this CGM and pump data, and a prediction API that runs
  as the `health-predict` service (internal network only). Trained models and data stay out of git. The pipeline
  reads `health-db` with the shared `POSTGRES_*` settings by default, or,
  with `PREDICT_DB=replica`, a copy of the server's database kept by
  `dbsync` in the optional `health-predict-replica` service.
- **Predicted glucose on the dashboard.** With `PREDICT_URL` set,
  health-api serves `GET /prediction`: predicted glucose 15–60 minutes
  after the latest reading, with 80% and 95% ranges. The dashboard shows
  the next 30 minutes as a dashed forecast on the Sensor glucose chart and
  the 15- and 30-minute values in a Predicted block on the right of the
  header, with the model's notes and its research-only disclaimer.
- **health-api: `MAX_HISTORY_DAYS` setting** to extend the history window
  beyond 90 days, e.g. `MAX_HISTORY_DAYS=365`. Values below 90 are
  rejected at startup.

### Changed

- **health-api: Prometheus metrics on an internal-only port** (#62).
  `/metrics` moves off the API's port (and health-fe's `/api` proxy) to its
  own server on `METRICS_PORT` (default 9090), which isn't published:
  scrape `http://health-api:9090/metrics` from a container on health-api's
  network.
- **All containers run as non-root users** (#63, #64). health-fe uses
  nginx's unprivileged image and still listens on port 80, which compose
  allows through `net.ipv4.ip_unprivileged_port_start`.
- **Smaller images, about 1.63 GB → 750 MB in all.**
  - health-predict: 1.43 GB → 629 MB. It builds in two stages, so uv and
    its download cache stay out, and it installs only what serving needs:
    the training pipeline's packages (DuckDB, psycopg, matplotlib, seaborn)
    move to a new `pipeline` extra, used by `make install` and CI.
  - health-fe: 61 → 19 MB, on nginx's `alpine-slim` image.
  - Go services: 20–30% smaller, with binaries built without debug symbols
    (`-trimpath -ldflags="-s -w"`). health-api drops the `wget` package,
    since busybox's `wget` runs its healthcheck.
- **health-predict loads its models at startup.** If they can't be loaded,
  it still starts, and `/health` and `/predict` answer 503 until they can.
  Before, `/predict` answered 500 in that case.
- **Images build in OCI format (Podman) as they do with Docker.**
  health-predict's healthcheck moves from its Dockerfile, where OCI images
  drop it, to `docker-compose.yml`. Its base image is fully qualified, and
  health-mongo-sync pins `alpine:3.21` instead of `latest`.
- **health-predict always runs** (no compose profile). Without models it
  reports unhealthy and the dashboard shows no forecast. The optional
  replica Postgres moves onto `health_be`.
- **Behind Caddy, health-api also joins `caddy-net`**, a fixed network
  name that replaces `CADDY_NETWORK`.

- **health-fe: security headers on every response** (#52). nginx now sends
  a strict Content-Security-Policy (only `'self'`, no inline scripts or
  styles, no framing), `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` and a
  `Permissions-Policy`, and no longer reveals its version.
- **health-api: API keys are compared in constant time** (#54). The
  `X-API-Key` header is checked against every configured key as a SHA-256
  digest with `subtle.ConstantTimeCompare`, instead of with `==`, so
  response times reveal nothing about the keys.
- **health-api: production mode is the default** (#53). gin ran in debug
  mode unless `NS_ENV=production`, and `.env.example` set
  `NS_ENV=development`, so a deployment copied from it ran in debug mode.
  It now runs in release mode unless `APP_ENV=development`; `NS_ENV` is
  still read when `APP_ENV` is unset. Any other value stops startup with
  an error, and the active mode is logged at startup. **Upgrading:** remove
  `NS_ENV=development` from `health-api/.env` on a production server.
- **Bytebase is pinned to 3.23.0** (#55) instead of `latest`, so a rebuild
  no longer upgrades it without notice. Update the tag deliberately.
- **Dependency vulnerability scanning** (#56). A new Security workflow runs
  `govulncheck` on each Go module and `npm audit --omit=dev` on health-fe,
  on every push and PR and weekly. health-fe's build tooling was updated
  to clear three advisories (esbuild 0.28.2, ajv 6.15.0, source-map-js
  1.2.2); none reached the shipped UI.

### Fixed

- **health-predict: live predictions at times off the 5-minute grid.**
  Insulin deliveries were binned on the clock's 5-minute marks rather than
  on the prediction's own 5-minute slots, so at a live CGM time (never
  exactly on the grid) the bins straddled the slots. Every live prediction
  warned that "basal history does not cover the last 240 min" even with
  complete basal, and the insulin windows and estimated insulin on board
  were understated (e.g. 8% lower IOB). Bins now follow the slots. Training
  is unaffected: grid-aligned features are identical, so no retraining is
  needed.

### Removed

- **health-api: `PUT /insulin`**, which nothing in the project used (#59).

### Upgrade notes

- **Rebuild everything:** every image changed (Go 1.27.2, non-root users,
  slimmer bases). Behind Caddy:
  `docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d --build`.
- **Caddy's network must be called `caddy-net`** and exist already.
  `CADDY_NETWORK` is no longer read. health-api joins it as well, so keep
  only trusted containers on it.
- **Predictions:** health-predict now starts with the stack. Copy the
  trained models into `health-predict/models/` first (they're not in git),
  and set `PREDICT_URL=http://health-predict:8000` in `health-api/.env`.
  Without models, health-predict reports unhealthy and the dashboard shows
  no forecast; nothing else is affected.
- **Production mode is the default.** `NS_ENV=production` still works.
  Remove any `NS_ENV=development` copied from the old `.env.example`.
- **The API serves the last 90 days only** by default. Set
  `MAX_HISTORY_DAYS` (at least 90) in `health-api/.env` to serve more.
- **Prometheus:** scrape `http://health-api:9090/metrics` (the internal
  metrics port) instead of `/metrics` on the API.
- **`PUT /insulin` is gone**, in case anything outside this project called
  it.

## [1.0.1] - 2026-10-06

### Security

- **health-api: the rate limiter could be bypassed by spoofing `X-Forwarded-For`**
  ([GHSA-wmqg-c3q9-fwfp](https://github.com/aravindc/health-app/security/advisories/GHSA-wmqg-c3q9-fwfp)).
  health-api didn't configure trusted proxies, so gin accepted
  `X-Forwarded-For` from any client when working out the client IP. A client
  could send a different fake IP with each request and never hit the
  per-IP limit (10 requests/s, burst 30). health-api now trusts that header
  only from known proxy addresses, so public clients are limited by their
  real IP.

### Added

- **health-api: `TRUSTED_PROXIES` setting**, a comma-separated list of IPs
  or CIDRs whose `X-Forwarded-For` entries are trusted. If unset, it trusts
  loopback and the private ranges (`10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `fc00::/7`), which covers the default Caddy → health-fe →
  health-api Docker setup. An invalid entry stops health-api at startup.

### Upgrade notes

- **Default Docker or Caddy setup:** no action needed.
- **A CDN or other proxy with public IPs in front of Caddy** (e.g.
  Cloudflare): add its IP ranges to `TRUSTED_PROXIES`, and to Caddy's
  `trusted_proxies`. Otherwise every visitor appears to come from the CDN's
  addresses and they all share one rate limit.
- **Clients on a trusted private network** (your LAN or Docker) can still
  set their own `X-Forwarded-For`. To stop that, narrow `TRUSTED_PROXIES` to
  just your proxies' subnet, e.g. `TRUSTED_PROXIES=172.18.0.0/16`.

## [1.0.0]

- Initial release.

[Unreleased]: https://github.com/aravindc/health-app/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/aravindc/health-app/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/aravindc/health-app/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/aravindc/health-app/releases/tag/v1.0.0
