"""Database selection (src/db.py) and the ingest source check (src/ingest.py)."""

import duckdb
import pytest

from src import db, ingest, schema

APP = {"POSTGRES_HOST": "health-db", "POSTGRES_PORT": "5432", "POSTGRES_DB": "health",
       "POSTGRES_USER": "app", "POSTGRES_PASSWORD": "app-secret"}
PREDICT = {"TARGET_DB_HOST": "127.0.0.1", "TARGET_DB_PORT": "9086", "TARGET_DB_NAME": "replica",
           "TARGET_DB_USER": "rep", "TARGET_DB_PASSWORD": "rep-secret",
           # A replica container's own settings in health-predict/.env must not reach health-db's connection.
           "POSTGRES_USER": "rep", "POSTGRES_PASSWORD": "rep-secret"}


def test_default_is_health_db_from_the_root_env():
    label, p = db.connection_params({}, PREDICT, APP)
    assert label.startswith("health-db")
    assert p == {"host": "health-db", "port": "5432", "dbname": "health", "user": "app", "password": "app-secret"}


def test_host_overrides_for_running_outside_docker():
    _, p = db.connection_params({}, {**PREDICT, "PREDICT_DB_HOST": "127.0.0.1", "PREDICT_DB_PORT": "9084"}, APP)
    assert (p["host"], p["port"], p["user"]) == ("127.0.0.1", "9084", "app")


def test_replica_from_health_predict_env():
    label, p = db.connection_params({"PREDICT_DB": "replica"}, PREDICT, APP)
    assert label.startswith("replica")
    assert p == {"host": "127.0.0.1", "port": "9086", "dbname": "replica", "user": "rep", "password": "rep-secret"}
    # PREDICT_DB can also live in health-predict/.env.
    assert db.source({}, {"PREDICT_DB": "Replica"}) == db.REPLICA


def test_environment_overrides_files():
    _, p = db.connection_params({"POSTGRES_HOST": "elsewhere"}, {}, APP)
    assert p["host"] == "elsewhere"


def test_missing_settings_are_named_without_values():
    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD") as e:
        db.connection_params({}, {}, {k: v for k, v in APP.items() if k != "POSTGRES_PASSWORD"})
    assert "app-secret" not in str(e.value)


def test_unknown_source_is_rejected():
    with pytest.raises(RuntimeError, match="PREDICT_DB"):
        db.source({"PREDICT_DB": "prod"}, {})


def _snapshot(rows: bool):
    d = duckdb.connect()
    d.execute(schema.RAW_DDL)
    if rows:
        d.execute("INSERT INTO raw_nightscout (id) VALUES (1)")
    return d


def test_ingest_records_and_keeps_its_source():
    d = _snapshot(rows=False)
    ingest.check_source(d, db.HEALTH_DB, full=False)
    ingest.check_source(d, db.HEALTH_DB, full=False)
    assert d.execute("SELECT source FROM ingest_source").fetchall() == [(db.HEALTH_DB,)]


def test_ingest_refuses_to_switch_source_without_full():
    d = _snapshot(rows=True)  # an older snapshot: no record, so it came from the replica
    with pytest.raises(SystemExit, match="--full"):
        ingest.check_source(d, db.HEALTH_DB, full=False)
    ingest.check_source(d, db.REPLICA, full=False)
    ingest.check_source(d, db.HEALTH_DB, full=True)
    assert d.execute("SELECT source FROM ingest_source").fetchall() == [(db.HEALTH_DB,)]
