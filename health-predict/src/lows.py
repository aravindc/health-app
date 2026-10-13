"""Predicting lows: "will glucose become low within 30 minutes?"

    python -m src.lows                # rolling-origin comparison (validation months only)
    python -m src.lows --test         # score the pre-declared choice once on the fixed test period

This is research analysis of predictive behaviour, NOT an alarm system and not a
treatment tool. This system is not a medical device and must not be used to
determine insulin doses or make treatment decisions.

Question asked at every prediction time T where current glucose is >= the low
threshold (3.9) and not a sensor-limit reading:
    event = an actual reading < 3.9 at T+15 or T+30 min.

Each method gives a risk score. For validation month k the alert threshold is
chosen on months BEFORE k to reach a target sensitivity, then applied to month
k — so reported sensitivity/precision are what you would really have got.
Months cv01–cv02 only serve as history; results are pooled over cv03–cv08.

Selection rule (declared before looking at results): the method with the highest
precision at 80% target sensitivity on validation; ties (within 1 point) go to the
simpler method.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

from src import DISCLAIMER, config, db, splits, train
from src import intervals as iv
from src.validate_data import md_table

log = logging.getLogger("lows")
METHODS = ["trend extrapolation", "RF point prediction", "RF conformal lower bound (80%)",
           "LightGBM quantile 10%", "LightGBM classifier"]
SIMPLICITY = {m: i for i, m in enumerate(METHODS)}
TARGETS = (0.8, 0.9)


def sample_frame(df: pd.DataFrame, low: float, mcfg: dict) -> pd.DataFrame:
    ok = (train.usable(df, 15, mcfg) & train.usable(df, 30, mcfg) & (df["current_glucose"] >= low)
          & (df["current_is_low_limit"] == 0))
    s = df.loc[ok, ["current_glucose", "velocity_15m_mmol_per_h", "glucose_15m", "glucose_30m"]].copy()
    s["y"] = (s[["glucose_15m", "glucose_30m"]].min(axis=1) < low).astype(int)
    return s


def low_onsets(cgm: pd.DataFrame, low: float, quiet_minutes: int = 30) -> pd.DatetimeIndex:
    """Start of each low episode: first reading < low after at least `quiet_minutes` without one."""
    c = cgm.sort_values("measured_at")
    lows = c.loc[c["glucose_mmol"] < low, "measured_at"].reset_index(drop=True)
    d = lows.diff()
    start = d.isna() | (d > pd.Timedelta(minutes=quiet_minutes))   # the first low always starts an episode
    return pd.DatetimeIndex(lows[start].to_numpy())


def threshold_for(scores: np.ndarray, y: np.ndarray, target: float) -> float:
    """Highest threshold whose sensitivity on (scores, y) reaches the target."""
    pos = np.sort(scores[y == 1])[::-1]
    if len(pos) == 0:
        return np.inf
    k = int(np.ceil(target * len(pos))) - 1
    return float(pos[min(max(k, 0), len(pos) - 1)])


def alert_episodes(times: pd.DatetimeIndex, alert: np.ndarray, merge_minutes: int = 15) -> list[pd.DatetimeIndex]:
    t = times[alert]
    if len(t) == 0:
        return []
    breaks = np.where(np.diff(t.as_unit("ns").asi8) > merge_minutes * 60e9)[0] + 1
    return [pd.DatetimeIndex(x) for x in np.split(t, breaks)]


def operating_point(frame: pd.DataFrame, onsets: pd.DatetimeIndex, days: float) -> dict:
    """frame: ts index, y, alert (bool). Slot-level and episode-level outcomes."""
    a, y = frame["alert"].to_numpy(), frame["y"].to_numpy().astype(bool)
    tp, fp, fn = (a & y).sum(), (a & ~y).sum(), (~a & y).sum()
    eps = alert_episodes(frame.index, a)
    ylab = frame["y"]
    false_eps = sum(1 for e in eps if ylab.loc[e].sum() == 0)
    alerted = frame.index[a]
    detected, leads = 0, []
    for o in onsets:
        w = alerted[(alerted >= o - pd.Timedelta(minutes=30)) & (alerted < o)]
        if len(w):
            detected += 1
            leads.append((o - w.min()).total_seconds() / 60)
    return {"sensitivity": tp / (tp + fn) if tp + fn else np.nan, "precision": tp / (tp + fp) if tp + fp else np.nan,
            "alert episodes / day": len(eps) / days, "false alert episodes / day": false_eps / days,
            "low episodes caught": detected / len(onsets) if len(onsets) else np.nan,
            "median warning (min)": float(np.median(leads)) if leads else np.nan, "episodes": len(onsets)}


# ------------------------------------------------------------------ scores

def fold_scores(df, samples, folds, fold, cv_rf, conf_calib, catalog, settings, mcfg) -> pd.DataFrame:
    """Risk scores for one validation month (higher = more likely to become low)."""
    from lightgbm import LGBMClassifier, LGBMRegressor

    seed = settings["project"]["random_seed"]
    va = samples.index[splits.mask(samples.index, folds, fold, "validation").to_numpy()]
    tr = samples.index[splits.mask(samples.index, folds, fold, "train").to_numpy()]
    out = pd.DataFrame(index=va)
    out["y"] = samples.loc[va, "y"]
    out["fold"] = fold
    out["trend extrapolation"] = -(samples.loc[va, "current_glucose"]
                                   + samples.loc[va, "velocity_15m_mmol_per_h"].fillna(0) * 0.5)

    rf = cv_rf[cv_rf["fold"] == fold].pivot_table(index="ts", columns="horizon", values="y_pred").reindex(va)
    out["RF point prediction"] = -rf[[15, 30]].min(axis=1)
    if conf_calib is not None:
        lows_ = []
        for h in (15, 30):
            mc, cats = conf_calib[h]
            g, d = cats(va)
            lo, _ = mc.interval(rf[h].to_numpy(), g, d, 0.8)
            lows_.append(lo)
        out["RF conformal lower bound (80%)"] = -np.minimum(*lows_)

    cols = train.feature_columns(catalog, "glucose_insulin", mcfg)
    params = dict(mcfg["models"]["lightgbm"])
    qs = []
    for h in (15, 30):
        trh = splits.mask(df.index, folds, fold, "train") & train.usable(df, h, mcfg)
        m = LGBMRegressor(objective="quantile", alpha=0.1, random_state=seed, **params)
        m.fit(df.loc[trh, cols], df.loc[trh, f"delta_{h}m"])
        qs.append(m.predict(df.loc[va, cols]) + df.loc[va, "current_glucose"].to_numpy())
    out["LightGBM quantile 10%"] = -np.minimum(*qs)

    clf = LGBMClassifier(random_state=seed, **params)
    clf.fit(df.loc[tr, cols], samples.loc[tr, "y"])
    out["LightGBM classifier"] = clf.predict_proba(df.loc[va, cols])[:, 1]
    return out


def conformal_for_fold(cv_rf, df, fold, acfg):
    """Conformal calibration from months BEFORE `fold` (None if fewer than two)."""
    earlier = cv_rf[cv_rf["fold"] < fold]
    if earlier["fold"].nunique() < 2:
        return None
    c = acfg["categories"]

    def cats(ts):
        f = df.loc[ts]
        return (pd.Series(iv.glucose_range(f["current_glucose"], c["glucose_ranges_mmol"]).to_numpy()),
                pd.Series(iv.direction(f[c["rate_feature"]], c["stable_rate_mmol_per_min"],
                                       c["rapid_rate_mmol_per_min"]).to_numpy()))
    out = {}
    for h in (15, 30):
        e = earlier[earlier["horizon"] == h]
        g, d = cats(pd.to_datetime(e["ts"], utc=True).to_numpy())
        mc = iv.MondrianConformal([0.8], acfg["intervals"]["min_bin_size"]).fit(
            pd.Series((e["y_true"] - e["y_pred"]).to_numpy()), g, d)
        out[h] = (mc, cats)
    return out


# -------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true", help="score the pre-declared choice once on the test period")
    ap.add_argument("--reuse-scores", action="store_true", help="re-evaluate saved validation scores (no refitting)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    s, mcfg = config.settings(), config.models()
    low = s["glucose_thresholds_mmol"]["low"]
    df, catalog = train.load_dataset(s)
    folds = splits.make_folds(s)
    samples = sample_frame(df, low, mcfg)
    with db.duck(read_only=True) as d:
        cgm = d.execute("SELECT measured_at, glucose_mmol FROM cgm_merged").df()
    cgm["measured_at"] = pd.to_datetime(cgm["measured_at"], utc=True)
    onsets_all = low_onsets(cgm, low)
    if args.test:
        return test_run(df, samples, folds, catalog, onsets_all, s, mcfg)

    cv = pd.read_parquet(config.path(train.PRED_DIR) / "cv_predictions.parquet")
    cv_rf = cv[(cv["model"] == "random_forest") & (cv["target_type"] == "delta") & (cv["feature_set"] == "glucose_insulin")
               & cv["horizon"].isin([15, 30])].copy()
    cv_rf["ts"] = pd.to_datetime(cv_rf["ts"], utc=True)
    score_path = config.path(train.PRED_DIR) / "lows_cv_scores.parquet"
    if args.reuse_scores:
        scores = pd.read_parquet(score_path)
    else:
        parts = []
        for fold in sorted(f for f in folds["fold"].unique() if f.startswith("cv")):
            parts.append(fold_scores(df, samples, folds, fold, cv_rf,
                                     conformal_for_fold(cv_rf, df, fold, mcfg["analysis"]), catalog, s, mcfg))
            log.info("scored %s", fold)
        scores = pd.concat(parts)
        scores.to_parquet(score_path)
    eval_folds = sorted(scores["fold"].unique())[2:]
    ev = scores[scores["fold"].isin(eval_folds)]

    rows, curves = [], {}
    for m in METHODS:
        e = ev.dropna(subset=[m])
        curves[m] = precision_recall_curve(e["y"], e[m])
        base = {"method": m, "ROC-AUC": roc_auc_score(e["y"], e[m]), "PR-AUC": average_precision_score(e["y"], e[m])}
        for target in TARGETS:
            frames = []
            for k in eval_folds:
                hist = scores[(scores["fold"] < k)].dropna(subset=[m])
                thr = threshold_for(hist[m].to_numpy(), hist["y"].to_numpy(), target)
                cur = scores[scores["fold"] == k]
                frames.append(cur.assign(alert=cur[m] >= thr)[["y", "alert"]])
            fr = pd.concat(frames).sort_index()
            vmask = np.zeros(len(onsets_all), bool)
            for k in eval_folds:
                r = folds[(folds["fold"] == k) & (folds["role"] == "validation")].iloc[0]
                vmask |= (onsets_all >= r["start"] + pd.Timedelta(minutes=30)) & (onsets_all < r["end"])
            days = sum((folds[(folds["fold"] == k) & (folds["role"] == "validation")].iloc[0][["start", "end"]]
                        .diff().iloc[1]).total_seconds() / 86400 for k in eval_folds)
            op = operating_point(fr, onsets_all[vmask], days)
            rows.append({**base, "target sensitivity": f"{target:.0%}", **op})
    res = pd.DataFrame(rows)

    sel = res[res["target sensitivity"] == "80%"].copy()
    best_p = sel["precision"].max()
    sel = sel[sel["precision"] >= best_p - 0.01].sort_values("method", key=lambda c: c.map(SIMPLICITY))
    chosen = sel.iloc[0]["method"]

    figs = config.path(s["report"]["figures_dir"])
    fig, ax = plt.subplots(figsize=(6.5, 5))
    for m, (p, r, _) in curves.items():
        ax.plot(r, p, label=m, lw=1.4)
    ax.axhline(ev["y"].mean(), color="#a0aec0", ls="--", lw=0.8, label="base rate")
    ax.set_xlabel("sensitivity (share of upcoming lows flagged)")
    ax.set_ylabel("precision (share of flags that are real)")
    ax.set_title("Becoming low within 30 min — validation cv03–cv08")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(figs / "lows_pr_curves.png", dpi=130)
    plt.close(fig)

    fmt = res.copy()
    for c in ("sensitivity", "precision", "low episodes caught"):
        fmt[c] = fmt[c].map(lambda x: "" if pd.isna(x) else f"{x:.0%}")
    text = f"""# Predicting lows — validation

> **{DISCLAIMER}** This is an analysis of predictive behaviour, not an alarm system.

Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Question: at times when glucose is ≥ {low} mmol/L, will a reading
< {low} occur within 30 minutes? {int(ev['y'].sum()):,} of {len(ev):,} prediction times in validation months
{eval_folds[0]}–{eval_folds[-1]} were followed by a low ({ev['y'].mean():.1%}); {int(vmask.sum())} low episodes started in
those months.

Each method's alert threshold for a month is chosen on the **earlier** months to reach the target sensitivity, so the
achieved numbers are honest. *Alert episodes* merge alerts less than 15 min apart; *false alert episodes* had no low
within 30 min of any alerted time; *low episodes caught* = an alert in the 30 min before the episode started;
*warning* = minutes between the first such alert and the start of the low.

![PR curves](figures/lows_pr_curves.png)

{md_table(fmt, '{:.2f}')}

**Pre-declared choice (highest precision at 80% target sensitivity; ties go to the simpler method): {chosen}.**
"""
    (config.path(s["report"]["output_dir"]) / "lows_report.md").write_text(text)
    log.info("chosen: %s; wrote reports/lows_report.md", chosen)


def test_run(df, samples, folds, catalog, onsets_all, s, mcfg) -> None:
    """Score the pre-declared choice once on the fixed test period (recorded as a candidate evaluation).

    Threshold: chosen on ALL validation months for 80% target sensitivity. The random forest point prediction
    is shown as a reference because its test predictions were already recorded in Phase 5.
    """
    import json

    from lightgbm import LGBMRegressor

    from src import holdout
    scores = pd.read_parquet(config.path(train.PRED_DIR) / "lows_cv_scores.parquet")
    report = (config.path(s["report"]["output_dir"]) / "lows_report.md").read_text()
    chosen = report.split("ties go to the simpler method): ")[1].split(".**")[0]
    if chosen != "LightGBM quantile 10%":
        raise SystemExit(f"test scoring is implemented for the declared choice 'LightGBM quantile 10%', not {chosen}")
    seed = s["project"]["random_seed"]
    te_s = samples.index[splits.mask(samples.index, folds, "final", "test").to_numpy()]
    cols = train.feature_columns(catalog, "glucose_insulin", mcfg)
    qs = []
    for h in (15, 30):
        tr = splits.mask(df.index, folds, "final", "train") & train.usable(df, h, mcfg)
        m = LGBMRegressor(objective="quantile", alpha=0.1, random_state=seed, **dict(mcfg["models"]["lightgbm"]))
        m.fit(df.loc[tr, cols], df.loc[tr, f"delta_{h}m"])
        qs.append(m.predict(df.loc[te_s, cols]) + df.loc[te_s, "current_glucose"].to_numpy())
    test_pred = pd.read_parquet(config.path(holdout.TEST_PRED))
    rf = test_pred[(test_pred["model"] == "random_forest") & test_pred["horizon"].isin([15, 30])].copy()
    rf["ts"] = pd.to_datetime(rf["ts"], utc=True)
    rf = rf.pivot_table(index="ts", columns="horizon", values="y_pred").reindex(te_s)
    test_scores = pd.DataFrame({"y": samples.loc[te_s, "y"], "LightGBM quantile 10%": -np.minimum(*qs),
                                "RF point prediction": -rf[[15, 30]].min(axis=1)}, index=te_s)
    r = folds.query("role == 'test'").iloc[0]
    days = (r["end"] - r["start"]).total_seconds() / 86400
    onsets = onsets_all[(onsets_all >= r["start"] + pd.Timedelta(minutes=30)) & (onsets_all < r["end"])]
    rows = []
    for m in ("LightGBM quantile 10%", "RF point prediction"):
        hist = scores.dropna(subset=[m])
        thr = threshold_for(hist[m].to_numpy(), hist["y"].to_numpy(), 0.8)
        e = test_scores.dropna(subset=[m])
        op = operating_point(e.assign(alert=e[m] >= thr)[["y", "alert"]], onsets, days)
        rows.append({"method": m + (" (chosen)" if m == chosen else " (reference)"),
                     "alert when predicted <": f"{-thr:.2f} mmol/L", "ROC-AUC": roc_auc_score(e["y"], e[m]),
                     "PR-AUC": average_precision_score(e["y"], e[m]), **op})
    res = pd.DataFrame(rows)
    holdout.record_run(train.git_commit(), False, "candidate-lows", chosen, ", ".join(res["method"]), folds,
                       {row["method"]: {"sensitivity": row["sensitivity"], "precision": row["precision"]}
                        for _, row in res.iterrows()})
    fmt = res.copy()
    for c in ("sensitivity", "precision", "low episodes caught"):
        fmt[c] = fmt[c].map(lambda x: "" if pd.isna(x) else f"{x:.0%}")
    text = report.split("\n## Test period")[0] + f"""
## Test period (scored once, {datetime.now(UTC):%Y-%m-%d})

Threshold fixed from all validation months (80% target sensitivity) and applied unchanged to the test period
({int(test_scores['y'].sum()):,} of {len(test_scores):,} prediction times followed by a low; {len(onsets)} low episodes).

{md_table(fmt, '{{:.2f}}')}
"""
    (config.path(s["report"]["output_dir"]) / "lows_report.md").write_text(text)
    log.info("test: %s", json.dumps({r["method"]: round(r["precision"], 3) for _, r in res.iterrows()}))
    print(fmt.to_string(index=False))


if __name__ == "__main__":
    main()
