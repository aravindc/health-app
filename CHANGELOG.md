# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

### Added

- **health-api: `MAX_HISTORY_DAYS` setting** to extend the history window
  beyond 90 days, e.g. `MAX_HISTORY_DAYS=365`. Values below 90 are
  rejected at startup.

### Changed

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

### Removed

- **health-api: `PUT /insulin`**, which nothing in the project used (#59).

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

[Unreleased]: https://github.com/aravindc/health-app/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/aravindc/health-app/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/aravindc/health-app/releases/tag/v1.0.0
