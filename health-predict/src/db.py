"""Database connections.

The pipeline reads one of two Postgres databases, chosen by PREDICT_DB:

  health-db (default)  health-app's own database, with the POSTGRES_* settings from health-app's root .env
                       that every other module uses. Running on the host, where the container name health-db
                       doesn't resolve, set PREDICT_DB_HOST / PREDICT_DB_PORT in health-predict/.env (locally:
                       127.0.0.1 and 9084).
  replica              a copy of another health-db (e.g. the server's, which has the full pump history) kept
                       current by dbsync, with <prefix>_DB_* settings in health-predict/.env (prefix TARGET,
                       config/settings.yaml database.env_prefix). For training on this machine.

The .env files are read, not exported, so one database's settings can't leak into the other's. Environment
variables override both. Credentials are never logged, and the Postgres session is read-only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import duckdb
import psycopg
from dotenv import dotenv_values

from src import config

HEALTH_DB, REPLICA = "health-db", "replica"


def _env_files() -> tuple[dict, dict]:
    """(health-predict/.env, health-app's root .env) as dicts; missing files are empty."""
    return dict(dotenv_values(config.ROOT / ".env")), dict(dotenv_values(config.ROOT.parent / ".env"))


def source(environ: Mapping[str, str] | None = None, predict_env: Mapping | None = None) -> str:
    """Which database the pipeline reads: HEALTH_DB or REPLICA."""
    environ = os.environ if environ is None else environ
    if predict_env is None:
        predict_env = _env_files()[0]
    value = (environ.get("PREDICT_DB") or predict_env.get("PREDICT_DB") or HEALTH_DB).strip().lower()
    if value not in (HEALTH_DB, REPLICA):
        raise RuntimeError(f"PREDICT_DB must be {HEALTH_DB!r} or {REPLICA!r}, not {value!r}")
    return value


def connection_params(environ: Mapping[str, str] | None = None, predict_env: Mapping | None = None,
                      app_env: Mapping | None = None) -> tuple[str, dict[str, str]]:
    """(label for messages, psycopg connection parameters) for the selected database."""
    environ = os.environ if environ is None else environ
    if predict_env is None or app_env is None:
        files = _env_files()
        predict_env = files[0] if predict_env is None else predict_env
        app_env = files[1] if app_env is None else app_env

    def get(name: str, file: Mapping) -> str | None:
        return environ.get(name) or file.get(name)

    if source(environ, predict_env) == REPLICA:
        prefix = environ.get("T1D_DB_ENV_PREFIX") or config.settings()["database"]["env_prefix"]
        names = {k: f"{prefix}_DB_{v}" for k, v in
                 [("host", "HOST"), ("port", "PORT"), ("dbname", "NAME"), ("user", "USER"), ("password", "PASSWORD")]}
        params = {k: get(n, predict_env) for k, n in names.items()}
        label = f"replica ({prefix}_DB_* in health-predict/.env)"
    else:
        names = {"host": "POSTGRES_HOST", "port": "POSTGRES_PORT", "dbname": "POSTGRES_DB",
                 "user": "POSTGRES_USER", "password": "POSTGRES_PASSWORD"}
        params = {k: get(n, app_env) for k, n in names.items()}
        for key, override in (("host", "PREDICT_DB_HOST"), ("port", "PREDICT_DB_PORT")):
            if v := get(override, predict_env):
                params[key] = v
        label = "health-db (POSTGRES_* in health-app's .env)"
    missing = [names[k] for k, v in params.items() if not v]
    if missing:
        raise RuntimeError(f"{label}: {', '.join(missing)} not set (see .env.example)")
    return label, params


def postgres() -> psycopg.Connection:
    label, params = connection_params()
    try:
        conn = psycopg.connect(
            **params,
            connect_timeout=10,
            options="-c default_transaction_read_only=on -c TimeZone=UTC",
            application_name="health-predict-ingest",
        )
    except psycopg.OperationalError as e:
        # psycopg messages can include host and user; keep only the server's reason.
        reason = str(e).strip().splitlines()[-1]
        for v in params.values():
            reason = reason.replace(v, "***")
        raise RuntimeError(f"cannot connect to Postgres, {label}: {reason}") from None
    return conn


def duck(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    p = config.path(config.settings()["database"]["duckdb_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(p), read_only=read_only)
    conn.execute("SET TimeZone = 'UTC'")
    return conn
