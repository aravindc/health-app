"""One-time evaluation on the held-out test period.

    python -m src.holdout            # selection run: refuses to run twice
    python -m src.holdout --force    # re-run anyway (recorded; avoid — it weakens the holdout)
    python -m src.holdout --candidate random_forest <version>
                                     # score one newly trained version for the promotion gate (recorded)

The selected model (saved by `python -m src.train --final`) and pre-declared
reference models are trained on all development data (before the test period)
and scored once on the test period. Model choice must NOT change after this.
Each run is recorded in the DuckDB table `holdout_runs`.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from src import config, db, models, registry, splits, tracking, train
from src.metrics import regression_metrics

log = logging.getLogger("holdout")
SELECTED = ("random_forest", "delta", "glucose_insulin")
REFERENCES = [("persistence", "-", "-"), ("linear", "delta", "glucose_insulin"),
              ("lightgbm", "delta", "glucose_insulin"), ("tcn", "delta", "sequence")]
TEST_PRED = "data/predictions/test_predictions.parquet"


def already_run() -> pd.DataFrame:
    with db.duck(read_only=True) as d:
        if not d.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'holdout_runs'").fetchone()[0]:
            return pd.DataFrame()
        return d.execute("SELECT * FROM holdout_runs").df()


def selection_runs_for_current_period(runs: pd.DataFrame) -> pd.DataFrame:
    """Selection runs already made on the CURRENT test period (the single-use rule applies per test period)."""
    if runs.empty:
        return runs
    if "purpose" in runs:
        runs = runs[runs["purpose"].fillna("selection") == "selection"]
    s = config.settings()
    start = splits.make_folds(s).query("role == 'test'").iloc[0]["start"]
    return runs[pd.to_datetime(runs["test_start"], utc=True) == start]


def test_period_meta(settings: dict, folds: pd.DataFrame) -> dict:
    r = folds.query("role == 'test'").iloc[0]
    return {"start_local": settings["splits"]["test_start"], "start": str(r["start"]), "end": str(r["end"])}


def record_run(git: str | None, forced: bool, purpose: str, selected: str, models_: str, folds, summary: dict) -> None:
    with db.duck() as d:
        d.execute("CREATE TABLE IF NOT EXISTS holdout_runs (run_at TIMESTAMPTZ, git_commit VARCHAR, forced BOOLEAN, "
                  "selected VARCHAR, models VARCHAR, test_start TIMESTAMPTZ, test_end TIMESTAMPTZ, mae_json VARCHAR)")
        cols = [c[0] for c in d.execute("DESCRIBE holdout_runs").fetchall()]
        if "purpose" not in cols:
            d.execute("ALTER TABLE holdout_runs ADD COLUMN purpose VARCHAR DEFAULT 'selection'")
        r = folds.query("role == 'test'").iloc[0]
        d.execute("INSERT INTO holdout_runs (run_at, git_commit, forced, selected, models, test_start, test_end, "
                  "mae_json, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  [datetime.now(UTC), git, forced, selected, models_, r["start"], r["end"], json.dumps(summary), purpose])


def evaluate_candidate(model: str, version: str) -> None:
    """Score one saved version on the fixed test period and store test metrics in its metadata."""
    s, mcfg = config.settings(), config.models()
    df, _ = train.load_dataset(s)
    folds = splits.make_folds(s)
    te_all = splits.mask(df.index, folds, "final", "test")
    summary = {}
    for h in s["targets"]["horizons_minutes"]:
        if version not in registry.versions(model, h):
            continue
        est, meta, _ = registry.load(model, h, version)
        te = te_all & train.usable(df, h, mcfg)
        pred = est.predict(df.loc[te, meta["features"]])
        if meta["target_type"] == "delta":
            pred = pred + df.loc[te, "current_glucose"].to_numpy()
        m = regression_metrics(df.loc[te, f"glucose_{h}m"], pred)
        if "test_metrics" in meta:  # keep earlier results (e.g. production re-scored on a new test period)
            meta.setdefault("test_history", []).append({"test_period": meta.get("test_period"),
                                                        "test_metrics": meta["test_metrics"]})
        if meta.get("status") != "production":
            meta["status"] = f"evaluated on the test period {datetime.now(UTC):%Y-%m-%d} (candidate)"
        meta.update(test_metrics=m, test_period=test_period_meta(s, folds))
        registry.write_meta(model, h, version, meta)
        summary[f"{model}|{h}"] = m["mae"]
        log.info("candidate %s/%s %dm test MAE %.3f", model, version, h, m["mae"])
        trn = folds.query("fold == 'final' and role == 'train'").iloc[0]
        tst = folds.query("role == 'test'").iloc[0]
        g = pd.DataFrame({"y_true": df.loc[te, f"glucose_{h}m"].to_numpy(), "y_pred": pred, "fold": "test"})
        tracking.log([tracking.experiment_row("test", g, model, h, meta["target_type"], meta["feature_set"], s, mcfg,
                                              training=(trn["start"], trn["end"]), evaluated=(tst["start"], tst["end"]),
                                              features=meta["features"], version=version, git=train.git_commit())])
    if not summary:
        raise SystemExit(f"no saved versions {model}/{version}")
    record_run(train.git_commit(), False, "candidate", f"{model}/{version}", f"{model}/{version}", folds, summary)


def rows(df, idx, h, pred, model, tt, fs):
    return pd.DataFrame({"ts": df.index[idx], "y_true": df.loc[idx, f"glucose_{h}m"].to_numpy(), "y_pred": pred,
                         "current_glucose": df.loc[idx, "current_glucose"].to_numpy(),
                         "target_censored": df.loc[idx, f"target_{h}m_censored"].to_numpy(),
                         "model": model, "horizon": h, "target_type": tt, "feature_set": fs, "fold": "test"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--candidate", nargs=2, metavar=("MODEL", "VERSION"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.candidate:
        evaluate_candidate(*args.candidate)
        return
    prev = selection_runs_for_current_period(already_run())
    if len(prev) and not args.force:
        raise SystemExit(f"The test period was already evaluated on {prev['run_at'].max()}. "
                         "Re-running weakens the holdout; use --force only with a recorded reason.")

    s, mcfg = config.settings(), config.models()
    df, catalog = train.load_dataset(s)
    folds = splits.make_folds(s)
    horizons = s["targets"]["horizons_minutes"]
    tr_all = splits.mask(df.index, folds, "final", "train")
    te_all = splits.mask(df.index, folds, "final", "test")
    seed = s["project"]["random_seed"]
    out = []

    # Selected model: load the latest saved version.
    sel_versions = {}
    for h in horizons:
        v = registry.versions(SELECTED[0], h)[-1]
        sel_versions[h] = v
        est, meta, _ = registry.load(SELECTED[0], h, v)
        te = te_all & train.usable(df, h, mcfg)
        pred = est.predict(df.loc[te, meta["features"]]) + df.loc[te, "current_glucose"].to_numpy()
        out.append(rows(df, te, h, pred, *SELECTED))

    # Reference models: trained here on the same development data.
    for m, tt, fs in REFERENCES:
        if m in models.SEQUENCE:
            from src.seq_models import SequenceRegressor
            X, _ = train.build_sequence_inputs(s, mcfg, df.index)
            Y = df[[f"delta_{h}m" for h in horizons]].to_numpy(dtype=np.float32)
            base = df["current_glucose"].notna() & ~np.all(np.isnan(Y), axis=1) & df["insulin_known"].astype(bool)
            tr = (tr_all & base).to_numpy()
            cut = df.index[tr][int(tr.sum() * (1 - mcfg["sequence"]["inner_validation_fraction"]))]
            emb = pd.Timedelta(minutes=s["splits"]["embargo_minutes"])
            reg = SequenceRegressor(m, mcfg["sequence"], seed).fit(
                X[tr & (df.index < cut - emb)], Y[tr & (df.index < cut - emb)], X[tr & (df.index >= cut)],
                Y[tr & (df.index >= cut)])
            for j, h in enumerate(horizons):
                te = (te_all & train.usable(df, h, mcfg)).to_numpy()
                out.append(rows(df, te, h, reg.predict(X[te])[:, j] + df.loc[te, "current_glucose"].to_numpy(),
                                m, tt, fs))
            continue
        for h in horizons:
            tr = tr_all & train.usable(df, h, mcfg)
            te = te_all & train.usable(df, h, mcfg)
            if m in models.RULES:
                pred = train.fit_predict(m, None, None, df.loc[te], h, mcfg, seed)
            else:
                cols = train.feature_columns(catalog, fs, mcfg)
                target = f"delta_{h}m" if tt == "delta" else f"glucose_{h}m"
                pred = train.fit_predict(m, df.loc[tr, cols], df.loc[tr, target], df.loc[te, cols], h, mcfg, seed)
                if tt == "delta":
                    pred = pred + df.loc[te, "current_glucose"].to_numpy()
            out.append(rows(df, te, h, pred, m, tt, fs))
        log.info("scored %s", m)

    preds = pd.concat(out, ignore_index=True)
    path = config.path(TEST_PRED)
    path.parent.mkdir(parents=True, exist_ok=True)
    preds.to_parquet(path, index=False)

    summary = {f"{m}|{h}": regression_metrics(g["y_true"], g["y_pred"])["mae"]
               for (m, h), g in preds.groupby(["model", "horizon"])}
    record_run(train.git_commit(), args.force, "selection", "/".join(SELECTED),
               ", ".join(["/".join(SELECTED)] + ["/".join(r) for r in REFERENCES]), folds, summary)

    for h in horizons:  # mark the selected model as evaluated
        meta = registry.read_meta(SELECTED[0], h, sel_versions[h])
        g = preds[(preds["model"] == SELECTED[0]) & (preds["horizon"] == h)]
        meta["status"] = f"evaluated on the test period {datetime.now(UTC):%Y-%m-%d}"
        meta["test_metrics"] = regression_metrics(g["y_true"], g["y_pred"])
        meta["test_period"] = test_period_meta(s, folds)
        registry.write_meta(SELECTED[0], h, sel_versions[h], meta)
    for k, v in summary.items():
        log.info("test MAE %-20s %.3f", k, v)


if __name__ == "__main__":
    main()
