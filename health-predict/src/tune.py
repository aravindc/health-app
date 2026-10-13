"""Nested hyperparameter tuning.

    python -m src.tune                       # random forest, LightGBM, TCN
    python -m src.tune --model lightgbm

For every outer validation month k:
  1. inner split of month k's TRAINING data: the last `inner_days` days (after an
     embargo gap) are inner validation, everything before is inner training;
  2. each candidate configuration is fitted on inner training and scored (30-min MAE)
     on inner validation;
  3. the best configuration is refitted on all of month k's training data and scored
     on month k (all horizons).
The outer month is never used to choose hyperparameters, so the resulting CV MAE is
an honest estimate. Candidates: the current defaults plus random draws from the
search space below.
"""

from __future__ import annotations

import argparse
import json
import logging
import time

import numpy as np
import pandas as pd

from src import config, models, splits, tracking, train

log = logging.getLogger("tune")

SPACES = {
    "lightgbm": {"num_leaves": [15, 31, 63, 127], "learning_rate": [0.03, 0.05, 0.1], "n_estimators": [300, 600, 1000],
                 "min_child_samples": [20, 50, 100, 200], "colsample_bytree": [0.6, 0.8, 1.0],
                 "subsample": [0.7, 0.8, 1.0], "reg_lambda": [0.0, 1.0, 5.0]},
    "random_forest": {"max_features": [0.2, 0.33, 0.5], "min_samples_leaf": [5, 10, 20, 50],
                      "n_estimators": [200, 400], "max_samples": [0.3, 0.5, 0.8]},
    "tcn": {"channels": [32, 64], "levels": [3, 4, 5], "dropout": [0.0, 0.1, 0.2], "learning_rate": [0.001, 0.003]},
}
TUNE_HORIZON = 30


def candidates(model: str, mcfg: dict, n: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    space = SPACES[model]
    default = ({k: mcfg["sequence"]["tcn"].get(k, mcfg["sequence"]["training"].get(k)) for k in space}
               if model == "tcn" else {k: mcfg["models"][model][k] for k in space if k in mcfg["models"][model]})
    out, seen = [default], {json.dumps(default, sort_keys=True)}
    while len(out) < n:
        c = {k: v[rng.integers(len(v))] for k, v in space.items()}
        key = json.dumps(c, sort_keys=True, default=float)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def tabular_params(model: str, mcfg: dict, cand: dict) -> dict:
    return {**mcfg["models"][model], **cand}


def seq_cfg(mcfg: dict, cand: dict) -> dict:
    cfg = json.loads(json.dumps(mcfg["sequence"]))
    for k, v in cand.items():
        (cfg["training"] if k == "learning_rate" else cfg["tcn"])[k] = v
    return cfg


def inner_split(index: pd.DatetimeIndex, tr: np.ndarray, inner_days: int, emb: pd.Timedelta):
    end = index[tr].max()
    cut = end - pd.Timedelta(days=inner_days)
    return tr & (index < cut - emb), tr & (index >= cut)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", nargs="*", default=["lightgbm", "random_forest", "tcn"])
    ap.add_argument("--n-candidates", type=int, default=8)
    ap.add_argument("--inner-days", type=int, default=60)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s, mcfg = config.settings(), config.models()
    seed = s["project"]["random_seed"]
    df, catalog = train.load_dataset(s)
    folds = splits.make_folds(s)
    horizons = s["targets"]["horizons_minutes"]
    emb = pd.Timedelta(minutes=s["splits"]["embargo_minutes"])
    cols = train.feature_columns(catalog, "glucose_insulin", mcfg)
    seq_X = None
    chosen_log = []
    for model in a.model:
        cands = candidates(model, mcfg, a.n_candidates, seed)
        preds = []
        for fold in sorted(f for f in folds["fold"].unique() if f.startswith("cv")):
            t0 = time.time()
            outer_tr = splits.mask(df.index, folds, fold, "train").to_numpy()
            outer_va = splits.mask(df.index, folds, fold, "validation").to_numpy()
            scores = []
            if model in models.SEQUENCE:
                from src.seq_models import SequenceRegressor
                if seq_X is None:
                    seq_X, _ = train.build_sequence_inputs(s, mcfg, df.index)
                Y = df[[f"delta_{h}m" for h in horizons]].to_numpy(dtype=np.float32)
                base = (df["current_glucose"].notna() & ~np.all(np.isnan(Y), axis=1) & df["insulin_known"]).to_numpy()
                itr, iva = inner_split(df.index, outer_tr & base, a.inner_days, emb)
                # early stopping inside inner training: its own last 15%
                j = horizons.index(TUNE_HORIZON)
                for c in cands:
                    cfg = seq_cfg(mcfg, c)
                    t_idx = np.where(itr)[0]
                    cut = t_idx[int(len(t_idx) * 0.85)]
                    es_tr, es_va = itr.copy(), itr.copy()
                    es_tr[cut:] = False
                    es_va[:cut] = False
                    reg = SequenceRegressor(model, cfg, seed).fit(seq_X[es_tr], Y[es_tr], seq_X[es_va], Y[es_va])
                    p = reg.predict(seq_X[iva])[:, j] + df["current_glucose"].to_numpy()[iva]
                    ok = ~np.isnan(df[f"glucose_{TUNE_HORIZON}m"].to_numpy()[iva])
                    scores.append(np.mean(np.abs(p[ok] - df[f"glucose_{TUNE_HORIZON}m"].to_numpy()[iva][ok])))
                best = cands[int(np.argmin(scores))]
                cfg = seq_cfg(mcfg, best)
                t_idx = np.where(outer_tr & base)[0]
                cut_t = df.index[t_idx[int(len(t_idx) * (1 - cfg["inner_validation_fraction"]))]]
                ftr = outer_tr & base & (df.index < cut_t - emb)
                fva = outer_tr & base & (df.index >= cut_t)
                reg = SequenceRegressor(model, cfg, seed).fit(seq_X[ftr], Y[ftr], seq_X[fva], Y[fva])
                pred_all = reg.predict(seq_X[outer_va])
                sub = df.loc[outer_va]
                for jj, h in enumerate(horizons):
                    ok = train.usable(sub, h, mcfg).to_numpy()
                    preds.append(pd.DataFrame({"ts": sub.index[ok], "fold": fold,
                                               "y_true": sub.loc[ok, f"glucose_{h}m"].to_numpy(),
                                               "y_pred": pred_all[ok, jj] + sub.loc[ok, "current_glucose"].to_numpy(),
                                               "current_glucose": sub.loc[ok, "current_glucose"].to_numpy(),
                                               "target_censored": sub.loc[ok, f"target_{h}m_censored"].to_numpy(),
                                               "model": model, "horizon": h, "target_type": "delta",
                                               "feature_set": "sequence+tuned"}))
            else:
                ok30 = train.usable(df, TUNE_HORIZON, mcfg).to_numpy()
                itr, iva = inner_split(df.index, outer_tr & ok30, a.inner_days, emb)
                for c in cands:
                    est = models.make_model(model, tabular_params(model, mcfg, c), seed)
                    est.fit(df.loc[itr, cols], df.loc[itr, f"delta_{TUNE_HORIZON}m"])
                    p = est.predict(df.loc[iva, cols]) + df.loc[iva, "current_glucose"].to_numpy()
                    scores.append(np.mean(np.abs(p - df.loc[iva, f"glucose_{TUNE_HORIZON}m"].to_numpy())))
                best = cands[int(np.argmin(scores))]
                for h in horizons:
                    okh = train.usable(df, h, mcfg).to_numpy()
                    tr, va = outer_tr & okh, outer_va & okh
                    est = models.make_model(model, tabular_params(model, mcfg, best), seed)
                    est.fit(df.loc[tr, cols], df.loc[tr, f"delta_{h}m"])
                    cur = df.loc[va, "current_glucose"].to_numpy()
                    preds.append(pd.DataFrame({"ts": df.index[va], "fold": fold,
                                               "y_true": df.loc[va, f"glucose_{h}m"].to_numpy(),
                                               "y_pred": est.predict(df.loc[va, cols]) + cur, "current_glucose": cur,
                                               "target_censored": df.loc[va, f"target_{h}m_censored"].to_numpy(),
                                               "model": model, "horizon": h, "target_type": "delta",
                                               "feature_set": "glucose_insulin+tuned"}))
            default_score = scores[0]
            chosen_log.append({"model": model, "fold": fold, "chosen": json.dumps(best, default=float),
                               "inner MAE default": default_score, "inner MAE chosen": min(scores),
                               "default chosen": int(np.argmin(scores)) == 0})
            log.info("%s %s: inner MAE default %.3f, best %.3f (%s) — %.0fs", model, fold, default_score,
                     min(scores), json.dumps(best, default=float), time.time() - t0)
        p = pd.concat(preds, ignore_index=True)
        train.save_cv(p)
        tracking.log(tracking.cv_rows(p, folds, catalog, s, mcfg, train.git_commit()))
        pd.DataFrame(chosen_log).to_csv(config.path("data/predictions/tuning_choices.csv"), index=False)
    log.info("done")


if __name__ == "__main__":
    main()
