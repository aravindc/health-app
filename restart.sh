#!/usr/bin/env bash
# Rebuilds and restarts the stack with Docker or Podman.
#
# Usage: run from anywhere (it cd's into the repo root first):
#   ./restart.sh docker up                    # build, then start; only changed containers are recreated
#   ./restart.sh podman down-up               # stop and remove the stack first, then build and start
#   ./restart.sh docker up health-api health-fe     # only these services
#   ./restart.sh docker down-up health-predict      # remove and recreate just this one
#
# Options (anywhere after the first two arguments):
#   --no-caddy   don't add docker-compose.caddy.yml (by default it's added
#                whenever Caddy's network, caddy-net, exists)
#   --dry-run    print the commands instead of running them
#
# down-up never deletes data: health-db, Bytebase and model files are bind
# mounts, and volumes aren't removed (no `down -v`).
#
# Podman: uses `podman compose` (Podman 4.7+), or podman-compose if that's
# missing.
set -euo pipefail

export PODMAN_COMPOSE_PROVIDER=/usr/bin/podman-compose

usage() {
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

[[ $# -ge 2 ]] || usage 1
engine=$1 mode=$2
shift 2

case "$engine" in
  docker | podman) ;;
  -h | --help) usage ;;
  *) echo "unknown engine '$engine': use docker or podman" >&2; exit 1 ;;
esac
case "$mode" in
  up | down-up) ;;
  *) echo "unknown mode '$mode': use up or down-up" >&2; exit 1 ;;
esac

caddy=auto dry_run=false services=()
for arg in "$@"; do
  case "$arg" in
    --no-caddy) caddy=no ;;
    --dry-run) dry_run=true ;;
    -h | --help) usage ;;
    -*) echo "unknown option '$arg'" >&2; exit 1 ;;
    *) services+=("$arg") ;;
  esac
done

cd "$(dirname "$0")"

# The compose command for this engine.
if [[ $engine == docker ]]; then
  compose=(docker compose)
elif podman compose version >/dev/null 2>&1; then
  compose=(podman compose)
elif command -v podman-compose >/dev/null; then
  compose=(podman-compose)
else
  echo "neither 'podman compose' nor podman-compose is available" >&2
  exit 1
fi

# Compose files: the Caddy override only when Caddy's network exists, since
# it joins an external network that compose won't create.
files=(-f docker-compose.yml)
if [[ $caddy == auto && -f docker-compose.caddy.yml ]]; then
  if "$engine" network inspect caddy-net >/dev/null 2>&1; then
    files+=(-f docker-compose.caddy.yml)
  else
    echo "note: network caddy-net not found, starting without docker-compose.caddy.yml" >&2
  fi
fi

# The commit the UI is built from, shown as <meta name="app-version">.
if [[ -z ${GIT_SHA:-} ]] && git rev-parse --short HEAD >/dev/null 2>&1; then
  export GIT_SHA
  GIT_SHA=$(git rev-parse --short HEAD)
fi

run() {
  echo "+ $*"
  if [[ $dry_run == false ]]; then
    "$@"
  fi
}

if [[ $mode == down-up ]]; then
  if [[ ${#services[@]} -gt 0 ]]; then
    run "${compose[@]}" "${files[@]}" rm --stop --force "${services[@]}"
  else
    run "${compose[@]}" "${files[@]}" down
  fi
fi
run "${compose[@]}" "${files[@]}" up -d --build "${services[@]}"
run "${compose[@]}" "${files[@]}" ps
