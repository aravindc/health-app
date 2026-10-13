"""Train and cross-validate models.

    python -m src.train --cv                         # all experiments in config/models.yaml
    python -m src.train --cv --experiment ablation
    python -m src.train --cv --model xgboost --horizon 30 --target-type delta
    python -m src.train --final --model lightgbm --horizon 30   # fit on all development data, save to models/

Cross-validation uses the rolling-origin folds (cv01 …) only. The test period
is never read here except by `--final`, which only fits on data before it.
One model per horizon (see config/models.yaml for hyperparameters).
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import subprocess
import time
from datetime import UTC, datetime

import joblib
import numpy as np
import pandas as pd

from src import baselines, config, db, models, splits, tracking
from src.metrics import regression_metrics

log = logging.getLogger("train")
PRED_DIR = "data/predictions"


def load_dataset(settings: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    v = settings["features"]["version"]
    df = pd.read_parquet(config.path(f"data/features/features_{v}.parquet")).set_index("ts")
    with db.duck(read_only=True) as d:
        catalog = d.execute(f"SELECT * FROM feature_catalog_{v}").df()
    return df, catalog


def feature_columns(catalog: pd.DataFrame, feature_set: str, mcfg: dict) -> list[str]:
    groups = mcfg["feature_sets"][feature_set]
    return catalog.loc[catalog["group"].isin(groups), "feature"].tolist()


def usable(df: pd.DataFrame, horizon: int, mcfg: dict) -> pd.Series:
    m = df["current_glucose"].notna() & df[f"glucose_{horizon}m"].notna()
    if mcfg["sample_filter"]["require_insulin_known"]:
        m &= df["insulin_known"].astype(bool)
    return m


def fit_predict(model: str, X_tr, y_tr, X_va, horizon: int, mcfg: dict, seed: int) -> np.ndarray:
    params = mcfg["models"].get(model, {}) or {}
    if model == "persistence":
        return baselines.persistence(X_va, horizon)
    if model == "linear_trend":
        return baselines.linear_trend(X_va, horizon, params["velocity_features"])
    est = models.make_model(model, params, seed)
    est.fit(X_tr, y_tr)
    return est.predict(X_va)


def run_cv(df, folds, catalog, model, horizon, target_type, feature_set, settings, mcfg) -> pd.DataFrame:
    cols = feature_columns(catalog, feature_set, mcfg)
    ok = usable(df, horizon, mcfg)
    target = f"glucose_{horizon}m" if target_type == "direct" else f"delta_{horizon}m"
    seed = settings["project"]["random_seed"]
    out = []
    for fold in sorted(f for f in folds["fold"].unique() if f.startswith("cv")):
        tr = splits.mask(df.index, folds, fold, "train") & ok
        va = splits.mask(df.index, folds, fold, "validation") & ok
        X_tr = df.loc[tr, cols]
        X_va = df.loc[va] if model in models.RULES else df.loc[va, cols]  # rules read named columns
        pred = fit_predict(model, X_tr, df.loc[tr, target], X_va, horizon, mcfg, seed)
        if target_type == "delta" and model in models.LEARNED:
            pred = pred + df.loc[va, "current_glucose"].to_numpy()
        out.append(pd.DataFrame({"ts": df.index[va], "fold": fold, "y_true": df.loc[va, f"glucose_{horizon}m"].to_numpy(),
                                 "y_pred": pred, "current_glucose": df.loc[va, "current_glucose"].to_numpy(),
                                 "target_censored": df.loc[va, f"target_{horizon}m_censored"].to_numpy()}))
    res = pd.concat(out, ignore_index=True)
    return res.assign(model=model, horizon=horizon, target_type=target_type if model in models.LEARNED else "-",
                      feature_set=feature_set if model in models.LEARNED else "-")


def fold_metrics(preds: pd.DataFrame) -> pd.DataFrame:
    keys = ["model", "horizon", "target_type", "feature_set"]
    rows = []
    for k, g in preds.groupby(keys + ["fold"]):
        rows.append({**dict(zip(keys + ["fold"], k)), **regression_metrics(g["y_true"], g["y_pred"])})
    for k, g in preds.groupby(keys):
        rows.append({**dict(zip(keys, k)), "fold": "pooled", **regression_metrics(g["y_true"], g["y_pred"])})
    return pd.DataFrame(rows)


def build_sequence_inputs(settings: dict, mcfg: dict, index: pd.DatetimeIndex) -> tuple[np.ndarray, list[str]]:
    from src import features as F
    from src import sequences

    scfg = mcfg["sequence"]
    with db.duck(read_only=True) as d:
        cgm, boluses, segments = F.load_inputs(d)
    start, end = F.window_bounds(settings)
    ts, X, names = sequences.build_sequences(cgm, boluses, segments, settings, start, end,
                                             scfg["lookback_minutes"], scfg["include_carbs"])
    if not ts.equals(pd.DatetimeIndex(index)):
        raise RuntimeError("sequence grid does not match the feature grid; rebuild features")
    return X, names


def run_cv_sequence(df, folds, X, model, settings, mcfg) -> pd.DataFrame:
    """Rolling-origin CV for a multi-horizon sequence model. Same evaluation rows as the tabular models."""
    from src.seq_models import SequenceRegressor

    horizons = settings["targets"]["horizons_minutes"]
    scfg = mcfg["sequence"]
    emb = pd.Timedelta(minutes=settings["splits"]["embargo_minutes"])
    Y = df[[f"delta_{h}m" for h in horizons]].to_numpy(dtype=np.float32)
    base = df["current_glucose"].notna() & ~np.all(np.isnan(Y), axis=1)
    if mcfg["sample_filter"]["require_insulin_known"]:
        base &= df["insulin_known"].astype(bool)
    out = []
    for fold in sorted(f for f in folds["fold"].unique() if f.startswith("cv")):
        tr = (splits.mask(df.index, folds, fold, "train") & base).to_numpy()
        va = splits.mask(df.index, folds, fold, "validation").to_numpy()
        # Early stopping on the END of the training fold, separated by the embargo.
        tr_times = df.index[tr]
        cut = tr_times[int(len(tr_times) * (1 - scfg["inner_validation_fraction"]))]
        inner_tr = tr & (df.index < cut - emb)
        inner_va = tr & (df.index >= cut)
        reg = SequenceRegressor(model, scfg, settings["project"]["random_seed"])
        reg.fit(X[inner_tr], Y[inner_tr], X[inner_va], Y[inner_va])
        pred = reg.predict(X[va]) + df.loc[va, "current_glucose"].to_numpy()[:, None]
        log.info("  %s %s: %d epochs, inner-val loss %.4f, device %s", model, fold, reg.epochs_,
                 reg.best_val_loss_, reg.device)
        sub = df.loc[va]
        for j, h in enumerate(horizons):
            ok = usable(sub, h, mcfg).to_numpy()
            out.append(pd.DataFrame({
                "ts": sub.index[ok], "fold": fold, "y_true": sub.loc[ok, f"glucose_{h}m"].to_numpy(),
                "y_pred": pred[ok, j], "current_glucose": sub.loc[ok, "current_glucose"].to_numpy(),
                "target_censored": sub.loc[ok, f"target_{h}m_censored"].to_numpy(), "model": model,
                "horizon": h, "target_type": "delta", "feature_set": "sequence"}))
    return pd.concat(out, ignore_index=True)


def experiment_runs(mcfg: dict, which: list[str]) -> list[tuple[str, str, str]]:
    runs = []
    for name in which:
        e = mcfg["experiments"][name]
        for m in e["models"]:
            trainable = m in models.LEARNED or m in models.SEQUENCE
            for fs in e["feature_sets"] if trainable else ["-"]:
                for tt in e["target_types"] if trainable else ["-"]:
                    if (m, tt, fs) not in runs:
                        runs.append((m, tt, fs))
    return runs


def save_cv(preds: pd.DataFrame) -> None:
    """Upsert predictions and metrics by (model, horizon, target_type, feature_set)."""
    keys = ["model", "horizon", "target_type", "feature_set"]
    out = config.path(PRED_DIR)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "cv_predictions.parquet"
    if path.exists():
        old = pd.read_parquet(path)
        new_keys = preds[keys].drop_duplicates()
        old = old.merge(new_keys, on=keys, how="left", indicator=True)
        old = old[old["_merge"] == "left_only"].drop(columns="_merge")
        preds = pd.concat([old, preds], ignore_index=True)
    preds.to_parquet(path, index=False)
    with db.duck() as d:
        res = fold_metrics(preds)
        d.register("res", res)
        d.execute("CREATE OR REPLACE TABLE cv_results AS SELECT * FROM res")


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=config.ROOT,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def fit_final(df, folds, catalog, model, horizon, target_type, feature_set, settings, mcfg,
              version: str | None = None) -> str:
    """Fit on all development data (before the test period) and save with metadata."""
    if model in models.SEQUENCE:
        raise SystemExit(f"--final for sequence models is not implemented ({model} was not selected in Phase 4)")
    if model not in models.LEARNED:
        raise SystemExit(f"{model} has nothing to fit")
    cols = feature_columns(catalog, feature_set, mcfg)
    tr = splits.mask(df.index, folds, "final", "train") & usable(df, horizon, mcfg)
    target = f"glucose_{horizon}m" if target_type == "direct" else f"delta_{horizon}m"
    params = mcfg["models"][model]
    est = models.make_model(model, params, settings["project"]["random_seed"])
    est.fit(df.loc[tr, cols], df.loc[tr, target])
    import lightgbm
    import sklearn
    import xgboost

    from src import registry
    created = datetime.now(UTC).isoformat()
    version = version or registry.version_id(created, git_commit())
    out = registry.version_dir(model, horizon, version)
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(est, out / "model.joblib")
    meta = {
        "model": model, "horizon": horizon, "version": version, "target_type": target_type,
        "feature_set": feature_set, "feature_version": settings["features"]["version"], "features": cols,
        "hyperparameters": params, "random_seed": settings["project"]["random_seed"],
        "training_period": {"start": str(df.index[tr].min()), "end": str(df.index[tr].max()), "samples": int(tr.sum())},
        "created_at": created, "git_commit": git_commit(),
        "software": {"python": platform.python_version(), "scikit-learn": sklearn.__version__,
                     "xgboost": xgboost.__version__, "lightgbm": lightgbm.__version__},
        "status": "candidate — not evaluated on the test set",
    }
    (out / "metadata.json").write_text(json.dumps(meta, indent=2))
    try:
        registry.calibrate(model, horizon, version)
    except LookupError as e:
        log.warning("no interval calibration: %s", e)
    log.info("saved %s", out)
    return version


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--cv", action="store_true", help="rolling-origin cross-validation")
    mode.add_argument("--final", action="store_true", help="fit on all development data and save")
    ap.add_argument("--experiment", nargs="*", default=None, help="experiments from config/models.yaml")
    ap.add_argument("--model", nargs="*", help="override: models to run")
    ap.add_argument("--horizon", type=int, nargs="*", help="override: horizons in minutes")
    ap.add_argument("--target-type", nargs="*", choices=["direct", "delta"])
    ap.add_argument("--feature-set", nargs="*")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    s, mcfg = config.settings(), config.models()
    df, catalog = load_dataset(s)
    folds = splits.make_folds(s)
    horizons = args.horizon or s["targets"]["horizons_minutes"]

    if args.final:
        from src import registry
        for m in args.model or [None]:
            version = registry.version_id(datetime.now(UTC).isoformat(), git_commit())  # one version per run
            for h in horizons:
                fit_final(df, folds, catalog, m, h, (args.target_type or ["delta"])[0],
                          (args.feature_set or [mcfg["default_feature_set"]])[0], s, mcfg, version)
            log.info("candidate version %s — evaluate with `python -m src.holdout --candidate %s %s`", version, m,
                     version)
        return

    if args.model and all(m in models.SEQUENCE for m in args.model):
        runs = [(m, "delta", "sequence") for m in args.model]
    elif args.model:
        runs = [(m, tt if m in models.LEARNED else "-", fs if m in models.LEARNED else "-")
                for m in args.model for tt in (args.target_type or ["delta"]) for fs in
                (args.feature_set or [mcfg["default_feature_set"]])]
        runs = list(dict.fromkeys(runs))
    else:
        runs = experiment_runs(mcfg, args.experiment or list(mcfg["experiments"]))

    all_preds = []
    seq_X = None
    for m, tt, fs in runs:
        if m in models.SEQUENCE:
            if seq_X is None:
                t0 = time.time()
                seq_X, names = build_sequence_inputs(s, mcfg, df.index)
                log.info("sequence inputs %s, channels %s (%.0fs)", seq_X.shape, names, time.time() - t0)
            t0 = time.time()
            p = run_cv_sequence(df, folds, seq_X, m, s, mcfg)
            for h, g in p.groupby("horizon"):
                log.info("%-14s %-6s %-24s %2dm  MAE %.3f", m, "delta", "sequence", h,
                         regression_metrics(g["y_true"], g["y_pred"])["mae"])
            log.info("%s: %.0fs", m, time.time() - t0)
            tracking.log(tracking.cv_rows(p, folds, catalog, s, mcfg, git_commit()))
            all_preds.append(p)
            save_cv(pd.concat(all_preds, ignore_index=True))
            continue
        for h in horizons:
            t0 = time.time()
            p = run_cv(df, folds, catalog, m, h, tt if tt != "-" else "direct", fs if fs != "-" else
                       mcfg["default_feature_set"], s, mcfg)
            p["target_type"], p["feature_set"] = tt, fs
            mae = regression_metrics(p["y_true"], p["y_pred"])["mae"]
            log.info("%-14s %-6s %-24s %2dm  MAE %.3f  (%.0fs)", m, tt, fs, h, mae, time.time() - t0)
            tracking.log(tracking.cv_rows(p, folds, catalog, s, mcfg, git_commit()))
            all_preds.append(p)
        save_cv(pd.concat(all_preds, ignore_index=True))  # save after each run so progress is kept
    log.info("done: %d runs x %d horizons", len(runs), len(horizons))


if __name__ == "__main__":
    main()
