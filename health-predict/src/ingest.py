"""Ingest source data into DuckDB.

    python -m src.ingest            # copy new source rows, rebuild clean tables
    python -m src.ingest --full     # re-copy all source rows first

Steps:
  1. Raw snapshot: new rows (id > max ingested id) from each Postgres source
     table are appended to raw_* tables, unchanged. Source tables are
     append-only, so the id watermark captures every new row.
  2. Raw tables are also exported to data/raw/*.parquet.
  3. Clean tables and dq_issues are rebuilt from the raw tables.
"""

from __future__ import annotations

import argparse
import logging
import uuid
from datetime import UTC, datetime

import pandas as pd

from src import config, db, schema
from src import preprocessing as pp

log = logging.getLogger("ingest")


def check_source(duck, source: str, full: bool) -> None:
    """Refuse to add rows from a different database than the snapshot came from.

    Rows are copied incrementally by id, and ids differ between databases, so mixing them would silently skip or
    duplicate rows. Switching needs a full re-copy (--full). Snapshots from before this check have no record;
    they came from the replica, the only source then.
    """
    duck.execute("CREATE TABLE IF NOT EXISTS ingest_source (source VARCHAR NOT NULL)")
    row = duck.execute("SELECT source FROM ingest_source").fetchone()
    has_rows = any(duck.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                   for t, _ in schema.RAW_SOURCES.values())
    recorded = row[0] if row else (db.REPLICA if has_rows else None)
    if recorded is not None and recorded != source and not full:
        raise SystemExit(f"the raw snapshot came from {recorded!r} but PREDICT_DB selects {source!r}: set "
                         f"PREDICT_DB={recorded}, or re-copy everything from {source!r} with "
                         "`python -m src.ingest --full`")
    duck.execute("DELETE FROM ingest_source")
    duck.execute("INSERT INTO ingest_source VALUES (?)", [source])


def snapshot_raw(duck, full: bool) -> None:
    batch = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    duck.execute(schema.RAW_DDL)
    check_source(duck, db.source(), full)
    with db.postgres() as pg:
        for source, (raw_table, select) in schema.RAW_SOURCES.items():
            started = datetime.now(UTC)
            if full:
                duck.execute(f"DELETE FROM {raw_table}")
            max_id = duck.execute(f"SELECT coalesce(max(id), -1) FROM {raw_table}").fetchone()[0]
            with pg.cursor() as cur:
                cur.execute(f"SELECT {select} FROM {source} WHERE id > %s ORDER BY id", (max_id,))
                cols = [c.name for c in cur.description]
                rows = cur.fetchall()
            df = pd.DataFrame(rows, columns=cols)
            if len(df):
                df["ingested_at"] = started
                df["ingest_batch"] = batch
                duck.register("incoming", df)
                duck.execute(f"INSERT INTO {raw_table} BY NAME SELECT * FROM incoming")
                duck.unregister("incoming")
            new_max = duck.execute(f"SELECT max(id) FROM {raw_table}").fetchone()[0]
            duck.execute("INSERT INTO ingest_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [batch, source, "full" if full else "incremental", len(df), new_max, started,
                          datetime.now(UTC)])
            log.info("raw %-14s +%d rows (total id max %s)", source, len(df), new_max)

    raw_dir = config.path("data/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    for raw_table, _ in schema.RAW_SOURCES.values():
        duck.execute(f"COPY {raw_table} TO '{raw_dir / (raw_table + '.parquet')}' (FORMAT parquet)")


def build_clean(duck) -> dict[str, int]:
    s, m = config.settings(), config.mappings()
    read = lambda t: duck.execute(f"SELECT * FROM {t}").df()

    tandem, i1 = pp.clean_tandem_cgm(read("raw_tandem_cgm"), s, m)
    ns, i2 = pp.clean_nightscout_cgm(read("raw_nightscout"), s, m)
    merged = pp.merge_cgm(tandem, ns, s["cgm"]["nightscout_fill_tolerance_seconds"])
    boluses, i3 = pp.clean_boluses(read("raw_tandem_bolus"), s, m)
    segments, i4 = pp.build_basal_segments(read("raw_tandem_basal"), s, m)
    deliveries = pd.concat([pp.bolus_deliveries(boluses),
                            pp.basal_deliveries(segments, s["insulin"]["insulin_type"])], ignore_index=True)
    carbs = pp.carbs_from_boluses(boluses)
    issues = pp.concat_issues([i1, i2, i3, i4])

    tables = {
        "cgm_readings": pd.concat([tandem, ns], ignore_index=True),
        "cgm_merged": merged,
        "boluses": boluses,
        "basal_segments": segments,
        "insulin_deliveries": deliveries,
        "carbs": carbs,
        "dq_issues": issues,
    }
    duck.execute("BEGIN")
    duck.execute(schema.CLEAN_DDL)
    for name, df in tables.items():
        duck.register("df", df)
        duck.execute(f"INSERT INTO {name} BY NAME SELECT * FROM df")
        duck.unregister("df")
    duck.execute("COMMIT")
    counts = {k: len(v) for k, v in tables.items()}
    for k, v in counts.items():
        log.info("clean %-18s %d rows", k, v)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="re-copy all rows from Postgres")
    ap.add_argument("--skip-source", action="store_true", help="rebuild clean tables from existing raw data only")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with db.duck() as duck:
        if not args.skip_source:
            snapshot_raw(duck, args.full)
        build_clean(duck)


if __name__ == "__main__":
    main()
