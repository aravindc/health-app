"""Experiment tracking in DuckDB (spec sections 21 and 23).

Every cross-validation run and every test-period evaluation adds a row to the
`experiments` table, with what is needed to reproduce it: periods, features,
hyperparameters, random seed, feature version, software versions, git commit.

    python -m src.tracking backfill    # add rows for runs recorded before tracking existed
    python -m src.tracking show
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from src import config, db
from src.metrics import regression_metrics

DDL = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id VARCHAR PRIMARY KEY,
    kind VARCHAR,                    -- cross_validation | test
    model VARCHAR,
    model_version VARCHAR,           -- registry version for saved models, else NULL
    horizon INTEGER,
    target_type VARCHAR,
    feature_set VARCHAR,
    feature_version VARCHAR,
    training_start TIMESTAMPTZ,
    training_end TIMESTAMPTZ,
    validation_start TIMESTAMPTZ,    -- validation months (cross-validation) or the test period (test)
    validation_end TIMESTAMPTZ,
    folds VARCHAR,
    n_samples BIGINT,
    features VARCHAR,                -- JSON list
    hyperparameters VARCHAR,         -- JSON
    random_seed INTEGER,
    mae DOUBLE,
    rmse DOUBLE,
    r2 DOUBLE,
    bias DOUBLE,
    hypoglycaemia_recall DOUBLE,     -- future glucose < low threshold: predicted below too
    hypoglycaemia_precision DOUBLE,
    prediction_interval_coverage DOUBLE,   -- 80% conformal interval coverage, when intervals exist
    software VARCHAR,                -- JSON
    git_commit VARCHAR,
    created_at TIMESTAMPTZ
);
"""


def software() -> dict:
    import importlib.metadata as md
    out = {"python": platform.python_version()}
    for p in ("scikit-learn", "xgboost", "lightgbm", "torch", "pandas", "numpy", "duckdb"):
        try:
            out[p] = md.version(p)
        except md.PackageNotFoundError:
            pass
    return out


def hypo_scores(y_true, y_pred, low: float) -> tuple[float, float]:
    a, p = np.asarray(y_true) < low, np.asarray(y_pred) < low
    recall = (a & p).sum() / a.sum() if a.sum() else np.nan
    precision = (a & p).sum() / p.sum() if p.sum() else np.nan
    return float(recall), float(precision)


def experiment_row(kind, preds: pd.DataFrame, model, horizon, target_type, feature_set, settings, mcfg, *,
                   training: tuple, evaluated: tuple, features: list[str] | None, version: str | None = None,
                   coverage: float | None = None, git: str | None = None) -> dict:
    m = regression_metrics(preds["y_true"], preds["y_pred"])
    rec, prec = hypo_scores(preds["y_true"], preds["y_pred"], settings["glucose_thresholds_mmol"]["low"])
    params = (mcfg["sequence"] if model in ("lstm", "tcn") else mcfg["models"].get(model)) or {}
    created = datetime.now(UTC)
    key = f"{kind}|{model}|{version}|{horizon}|{target_type}|{feature_set}|{git}|{created.isoformat()}"
    return {
        "experiment_id": hashlib.sha1(key.encode()).hexdigest()[:16], "kind": kind, "model": model,
        "model_version": version, "horizon": int(horizon), "target_type": target_type, "feature_set": feature_set,
        "feature_version": settings["features"]["version"], "training_start": training[0], "training_end": training[1],
        "validation_start": evaluated[0], "validation_end": evaluated[1],
        "folds": ",".join(sorted(preds["fold"].astype(str).unique())), "n_samples": m["n"],
        "features": json.dumps(features) if features is not None else None, "hyperparameters": json.dumps(params),
        "random_seed": settings["project"]["random_seed"], "mae": m["mae"], "rmse": m["rmse"], "r2": m["r2"],
        "bias": m["bias"], "hypoglycaemia_recall": rec, "hypoglycaemia_precision": prec,
        "prediction_interval_coverage": coverage, "software": json.dumps(software()), "git_commit": git,
        "created_at": created,
    }


def log(rows: list[dict]) -> None:
    if not rows:
        return
    with db.duck() as d:
        d.execute(DDL)
        df = pd.DataFrame(rows)
        d.register("rows", df)
        d.execute("INSERT INTO experiments BY NAME SELECT * FROM rows")
        d.unregister("rows")


def cv_rows(preds: pd.DataFrame, folds: pd.DataFrame, catalog: pd.DataFrame, settings, mcfg, git) -> list[dict]:
    from src import train
    val = folds[folds["role"] == "validation"]
    tr = folds[folds["role"] == "train"]
    rows = []
    for (m, h, tt, fs), g in preds.groupby(["model", "horizon", "target_type", "feature_set"]):
        base_fs = fs.split("+")[0]   # variants such as glucose_insulin+tuned use the base set's columns
        feats = train.feature_columns(catalog, base_fs, mcfg) if base_fs in mcfg["feature_sets"] else None
        rows.append(experiment_row("cross_validation", g, m, h, tt, fs, settings, mcfg,
                                   training=(tr["start"].min(), tr[tr["fold"].str.startswith("cv")]["end"].max()),
                                   evaluated=(val["start"].min(), val["end"].max()), features=feats, git=git))
    return rows


def backfill() -> int:
    from src import intervals as iv
    from src import registry, splits, train
    s, mcfg = config.settings(), config.models()
    folds = splits.make_folds(s)
    df, catalog = train.load_dataset(s)
    git = train.git_commit()
    rows = []
    cv_path = config.path(train.PRED_DIR) / "cv_predictions.parquet"
    if cv_path.exists():
        rows += cv_rows(pd.read_parquet(cv_path), folds, catalog, s, mcfg, git)
    test_path = config.path("data/predictions/test_predictions.parquet")
    if test_path.exists():
        test = pd.read_parquet(test_path)
        test["ts"] = pd.to_datetime(test["ts"], utc=True)
        trn = folds.query("fold == 'final' and role == 'train'").iloc[0]
        tst = folds.query("role == 'test'").iloc[0]
        for (m, h, tt, fs), g in test.groupby(["model", "horizon", "target_type", "feature_set"]):
            version, cov, feats = None, None, None
            prod = registry.production(h)
            if prod and prod["model"] == m:
                version = prod["version"]
                _, meta, conf = registry.load(m, h, version)
                feats = meta["features"]
                if conf is not None:
                    c = mcfg["analysis"]["categories"]
                    f = df.loc[g["ts"].to_numpy()]
                    lo, hi = conf.interval(g["y_pred"].to_numpy(),
                                           iv.glucose_range(f["current_glucose"], c["glucose_ranges_mmol"]),
                                           iv.direction(f[c["rate_feature"]], c["stable_rate_mmol_per_min"],
                                                        c["rapid_rate_mmol_per_min"]), 0.8)
                    cov = iv.interval_metrics(g["y_true"], lo, hi)["coverage"]
            elif fs not in ("-", "sequence"):
                feats = train.feature_columns(catalog, fs, mcfg)
            rows.append(experiment_row("test", g, m, h, tt, fs, s, mcfg, training=(trn["start"], trn["end"]),
                                       evaluated=(tst["start"], tst["end"]), features=feats, version=version,
                                       coverage=cov, git=git))
    log(rows)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["backfill", "show"])
    a = ap.parse_args()
    if a.cmd == "backfill":
        print(f"added {backfill()} experiment rows")
    else:
        with db.duck(read_only=True) as d:
            print(d.execute("""SELECT kind, model, coalesce(model_version, '') AS version, horizon, target_type,
                feature_set, round(mae, 3) mae, round(rmse, 3) rmse, round(r2, 3) r2,
                round(hypoglycaemia_recall, 3) hypo_recall, round(prediction_interval_coverage, 3) pi80_coverage
                FROM experiments ORDER BY kind, horizon, mae""").df().to_string(index=False))


if __name__ == "__main__":
    main()
