"""Does older glucose-only history (Nightscout, before insulin data starts) improve prediction?

    python -m src.pretrain           # rolling-origin comparison -> reports/pretraining_report.md

History = Nightscout CGM from its first reading up to the modelling-window start
(no insulin data exists then). It ends before every validation month, so it can
be used for training in every fold without leakage. Variants (LightGBM, delta
target, same validation samples as all other models):

  glucose_only+history         glucose-only features; training = history + fold training data
  glucose_insulin+pretrained   boosting trees fitted on history (glucose features only), then more trees fitted
                               on the fold training data with all features (LightGBM init_model)
  glucose_insulin+stacked      a glucose-only model trained on history; its predicted change is an extra input
                               to the usual glucose+insulin model

Closest available analogue of spec section 25 (personalised vs generic): only one
person's data exists, so "generic" here means "same person, other period".
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from src import DISCLAIMER, config, db, splits, targets, tracking, train
from src import features as F
from src.evaluate import compare, per_fold_mae, pooled
from src.validate_data import md_table

log = logging.getLogger("pretrain")


def history_dataset(settings: dict) -> pd.DataFrame:
    """Features + targets for the period before the modelling window (glucose information only)."""
    with db.duck(read_only=True) as d:
        cgm, boluses, segments = F.load_inputs(d)
    w_start, _ = F.window_bounds(settings)
    first = cgm["measured_at"].min().ceil("5min")
    feats, catalog = F.build_features(cgm[cgm["measured_at"] < w_start], boluses.iloc[0:0], segments.iloc[0:0],
                                      settings, first + pd.Timedelta(hours=6), w_start)
    tg = targets.make_targets(cgm, feats["current_glucose"], settings)
    df = feats.join(tg.drop(columns=["current_glucose"]))
    ins_cols = catalog.loc[catalog["group"].isin(["insulin", "iob", "carbs"]), "feature"]
    df[ins_cols] = np.nan            # insulin unknown in this period: missing, never zero
    return df


def main() -> None:
    import argparse

    from lightgbm import LGBMRegressor

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report-only", action="store_true", help="rebuild the report from saved predictions")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s, mcfg = config.settings(), config.models()
    seed = s["project"]["random_seed"]
    params = dict(mcfg["models"]["lightgbm"])
    df, catalog = train.load_dataset(s)
    folds = splits.make_folds(s)
    horizons = s["targets"]["horizons_minutes"]
    t0 = time.time()
    hist = history_dataset(s)
    log.info("history: %d prediction times %s → %s (%.0fs)", len(hist), hist.index.min().date(),
             hist.index.max().date(), time.time() - t0)
    g_cols = train.feature_columns(catalog, "glucose_only", mcfg)
    gi_cols = train.feature_columns(catalog, "glucose_insulin", mcfg)
    preds = []
    for h in [] if args.report_only else horizons:
        hk = hist[hist["current_glucose"].notna() & hist[f"glucose_{h}m"].notna()]
        ok = train.usable(df, h, mcfg)
        # Pretrained models on history only (identical for all folds: history precedes every fold).
        pre_g = LGBMRegressor(random_state=seed, **params).fit(hk[g_cols], hk[f"delta_{h}m"])
        pre_gi = LGBMRegressor(random_state=seed, **params).fit(hk[gi_cols], hk[f"delta_{h}m"])
        for fold in sorted(f for f in folds["fold"].unique() if f.startswith("cv")):
            tr = splits.mask(df.index, folds, fold, "train") & ok
            va = splits.mask(df.index, folds, fold, "validation") & ok
            y_tr = df.loc[tr, f"delta_{h}m"]
            cur = df.loc[va, "current_glucose"].to_numpy()
            out = {}
            both = pd.concat([hk[g_cols + [f"delta_{h}m"]], df.loc[tr, g_cols + [f"delta_{h}m"]]])
            m = LGBMRegressor(random_state=seed, **params).fit(both[g_cols], both[f"delta_{h}m"])
            out["glucose_only+history"] = m.predict(df.loc[va, g_cols])
            m = LGBMRegressor(random_state=seed, **params).fit(df.loc[tr, gi_cols], y_tr, init_model=pre_gi.booster_)
            out["glucose_insulin+pretrained"] = m.predict(df.loc[va, gi_cols])
            X_tr = df.loc[tr, gi_cols].assign(pretrained_delta=pre_g.predict(df.loc[tr, g_cols]))
            X_va = df.loc[va, gi_cols].assign(pretrained_delta=pre_g.predict(df.loc[va, g_cols]))
            m = LGBMRegressor(random_state=seed, **params).fit(X_tr, y_tr)
            out["glucose_insulin+stacked"] = m.predict(X_va)
            for fs, p in out.items():
                preds.append(pd.DataFrame({
                    "ts": df.index[va], "fold": fold, "y_true": df.loc[va, f"glucose_{h}m"].to_numpy(),
                    "y_pred": p + cur, "current_glucose": cur,
                    "target_censored": df.loc[va, f"target_{h}m_censored"].to_numpy(), "model": "lightgbm",
                    "horizon": h, "target_type": "delta", "feature_set": fs}))
        log.info("%d min done (%.0fs)", h, time.time() - t0)
    allp = pd.read_parquet(config.path(train.PRED_DIR) / "cv_predictions.parquet")
    if preds:
        new = pd.concat(preds, ignore_index=True)
        train.save_cv(new)
    else:  # report-only: log the saved variants if they were never logged
        new = allp[allp["feature_set"].isin(["glucose_only+history", "glucose_insulin+pretrained",
                                             "glucose_insulin+stacked"])]
    tracking.log(tracking.cv_rows(new, folds, catalog, s, mcfg, train.git_commit()))

    allp = pd.read_parquet(config.path(train.PRED_DIR) / "cv_predictions.parquet")
    allp = allp[(allp["model"] == "lightgbm") & (allp["target_type"] == "delta")]
    pool, fm = pooled(allp), per_fold_mae(allp)
    order = ["glucose_only", "glucose_only+history", "glucose_insulin", "glucose_insulin+pretrained",
             "glucose_insulin+stacked"]
    tbl = pool[pool["feature_set"].isin(order)].pivot_table(index="feature_set", columns="horizon", values="mae")
    tbl = tbl.reindex(order)
    tbl.columns = [f"{h}m MAE" for h in tbl.columns]
    rows = []
    for base, var in [("glucose_only", "glucose_only+history"), ("glucose_insulin", "glucose_insulin+pretrained"),
                      ("glucose_insulin", "glucose_insulin+stacked")]:
        for h in horizons:
            rel, wins = compare(fm, ("lightgbm", "delta", base), ("lightgbm", "delta", var), h)
            rows.append((var, base, f"{h} min", f"{rel * 100:+.1f}%", f"{wins * 100:.0f}%"))
    eff = pd.DataFrame(rows, columns=["variant", "compared with", "horizon", "MAE change (− = worse)",
                                      "months improved"])
    n_hist = int((hist["current_glucose"].notna() & hist["glucose_30m"].notna()).sum())
    text = f"""# Older glucose history (Nightscout) — validation

> **{DISCLAIMER}**

Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. History: {n_hist:,} prediction times with a 30-min target,
{hist.index.min():%Y-%m-%d} → {hist.index.max():%Y-%m-%d} (glucose only — no insulin data exists then; insulin
features are missing, not zero). It precedes every validation month. LightGBM, delta target, rolling-origin
validation (8 months), same samples as all other models.

This is the closest available analogue of the spec's personalised-vs-generic question: there is only one person, so
the "other" data is the same person in an earlier period, which had lower average glucose and higher time in range
(different therapy).

{md_table(tbl.reset_index(), '{:.3f}')}

{md_table(eff)}

Rule as elsewhere: a variant is worth adopting only if it improves MAE by ≥ 5% and wins in ≥ 75% of months.
"""
    (config.path(s["report"]["output_dir"]) / "pretraining_report.md").write_text(text)
    print(md_table(tbl.reset_index(), "{:.3f}"))
    print(md_table(eff))


if __name__ == "__main__":
    main()
