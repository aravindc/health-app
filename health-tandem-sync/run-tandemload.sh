#!/usr/bin/env bash
# Runs tandemload (the one-time historical backfill, see cmd/tandemload)
# against health-db from the host — i.e. outside Docker's network, where
# POSTGRES_HOST=health-db / POSTGRES_PORT=5432 in the root .env (correct
# for containers on the health_be network) do not resolve. This script
# overrides both to the host-reachable address before running.
#
# Usage: run from anywhere (it cd's into its own directory first):
#   ./run-tandemload.sh                      # full available history
#   ./run-tandemload.sh -start 2024-01-01    # explicit range, any tandemload flag
#
# Requires: health-db running via docker compose (for the port mapping
# lookup below), TANDEM_USERNAME/PASSWORD set in health-tandem-sync/.env,
# and Go installed locally (this runs `go run`, not a container).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

repo_root="$(cd .. && pwd)"

# Ask docker compose for health-db's actual host-mapped port instead of
# hardcoding 9084, so this doesn't silently go stale if docker-compose.yml's
# port mapping ever changes.
health_db_addr="$(cd "$repo_root" && docker compose port health-db 5432 2>/dev/null || true)"
if [ -z "$health_db_addr" ]; then
    echo "error: could not determine health-db's host port via 'docker compose port health-db 5432'." >&2
    echo "       Is health-db running? Try: docker compose up -d health-db" >&2
    exit 1
fi
host_port="${health_db_addr##*:}"

# POSTGRES_DB/USER/PASSWORD are the same on the host as in the container —
# only the host/port differ — so pull those three from the root .env
# without needing to duplicate them here, and export the host-reachable
# HOST/PORT ourselves so they take precedence over whatever go run's own
# .env loading would otherwise pick up (see cmd/tandemload's LoadDotEnv
# calls: an already-exported variable is never overwritten).
if [ -f "$repo_root/.env" ]; then
    export POSTGRES_DB="$(grep -E '^POSTGRES_DB=' "$repo_root/.env" | tail -1 | cut -d= -f2-)"
    export POSTGRES_USER="$(grep -E '^POSTGRES_USER=' "$repo_root/.env" | tail -1 | cut -d= -f2-)"
    export POSTGRES_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' "$repo_root/.env" | tail -1 | cut -d= -f2-)"
else
    echo "error: $repo_root/.env not found." >&2
    exit 1
fi
export POSTGRES_HOST="localhost"
export POSTGRES_PORT="$host_port"

# health-db's Postgres has SSL off (see docker-compose.yml's other services,
# which all set this the same way); db.URLFromEnv() defaults to
# sslmode=require otherwise, which fails against it.
export DB_SSL_MODE="${DB_SSL_MODE:-disable}"

echo "Using health-db at ${POSTGRES_HOST}:${POSTGRES_PORT} (host-mapped from health-db:5432)" >&2

exec go run ./cmd/tandemload "$@"
