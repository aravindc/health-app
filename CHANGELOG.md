# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/aravindc/health-app/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/aravindc/health-app/releases/tag/v1.0.0
