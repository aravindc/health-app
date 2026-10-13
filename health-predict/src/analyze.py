"""Phase 5 analysis of the selected model on the held-out test period.

    python -m src.analyze

Needs `python -m src.holdout` to have run. Uses the recorded test predictions;
no model choice is made here. Writes reports/test_report.md and figures.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src import DISCLAIMER, config, explain, splits, train
from src import error_analysis as ea
from src import intervals as iv
from src.evaluate import LABEL
from src.holdout import SELECTED, TEST_PRED
from src.metrics import regression_metrics
from src.validate_data import md_table

log = logging.getLogger("analyze")
RF = SELECTED[0]


def fmt_pct(x):
    return "" if pd.isna(x) else f"{x * 100:.1f}%"


# ------------------------------------------------------------------ inputs

def load(settings, mcfg):
    df, catalog = train.load_dataset(settings)
    test = pd.read_parquet(config.path(TEST_PRED))
    test["ts"] = pd.to_datetime(test["ts"], utc=True)
    cv = pd.read_parquet(config.path(train.PRED_DIR) / "cv_predictions.parquet")
    cv = cv[(cv["model"] == SELECTED[0]) & (cv["target_type"] == SELECTED[1]) & (cv["feature_set"] == SELECTED[2])]
    cv["ts"] = pd.to_datetime(cv["ts"], utc=True)
    return df, catalog, test, cv


def categorise(df: pd.DataFrame, ts: pd.Series, acfg: dict) -> tuple[pd.Series, pd.Series]:
    c = acfg["categories"]
    f = df.loc[ts.to_numpy()]
    g = iv.glucose_range(f["current_glucose"], c["glucose_ranges_mmol"])
    d = iv.direction(f[c["rate_feature"]], c["stable_rate_mmol_per_min"], c["rapid_rate_mmol_per_min"])
    return pd.Series(g.to_numpy(), index=ts.index), pd.Series(d.to_numpy(), index=ts.index)


# --------------------------------------------------------------- intervals

def conformal_intervals(df, cv, test, acfg):
    icfg = acfg["intervals"]
    levels = icfg["levels"]
    cv = cv.assign(residual=cv["y_true"] - cv["y_pred"])
    cv["grange"], cv["direction"] = categorise(df, cv["ts"], acfg)
    seq = iv.sequential_check(cv, levels, icfg["min_bin_size"])
    rows, fitted = [], {}
    t = test[test["model"] == RF].copy()
    t["grange"], t["direction"] = categorise(df, t["ts"], acfg)
    for h, ch in cv.groupby("horizon"):
        mc = iv.MondrianConformal(levels, icfg["min_bin_size"]).fit(ch["residual"], ch["grange"], ch["direction"])
        fitted[h] = mc
        th = t[t["horizon"] == h]
        for L in levels:
            lo, hi = mc.interval(th["y_pred"].to_numpy(), th["grange"], th["direction"], L)
            t.loc[th.index, f"lo_{L}"], t.loc[th.index, f"hi_{L}"] = lo, hi
    for (h, g) in t.groupby("horizon"):
        for L in levels:
            rows.append({"method": "conformal (Mondrian)", "horizon": h, "level": L,
                         **iv.interval_metrics(g["y_true"], g[f"lo_{L}"], g[f"hi_{L}"])})
    return t, pd.DataFrame(rows), seq, fitted


def quantile_intervals(df, catalog, test, settings, mcfg):
    """LightGBM quantile regression on the delta target, fitted on development data."""
    from lightgbm import LGBMRegressor

    qs = mcfg["analysis"]["intervals"]["quantile_regression"]
    folds = splits.make_folds(settings)
    cols = train.feature_columns(catalog, SELECTED[2], mcfg)
    params = {k: v for k, v in mcfg["models"]["lightgbm"].items()}
    t = test[test["model"] == RF].copy()
    rows = []
    for h in settings["targets"]["horizons_minutes"]:
        tr = splits.mask(df.index, folds, "final", "train") & train.usable(df, h, mcfg)
        th = t[t["horizon"] == h]
        X_te = df.loc[th["ts"].to_numpy(), cols]
        preds = {}
        for q in qs:
            m = LGBMRegressor(objective="quantile", alpha=q, random_state=settings["project"]["random_seed"], **params)
            m.fit(df.loc[tr, cols], df.loc[tr, f"delta_{h}m"])
            preds[q] = m.predict(X_te) + th["current_glucose"].to_numpy()
        for L, (a, b) in {0.8: (0.1, 0.9), 0.95: (0.025, 0.975)}.items():
            lo, hi = np.minimum(preds[a], preds[b]), np.maximum(preds[a], preds[b])
            rows.append({"method": "LightGBM quantile regression", "horizon": h, "level": L,
                         **iv.interval_metrics(th["y_true"], lo, hi)})
    return pd.DataFrame(rows)


def coverage_curve(cv, t, df, acfg):
    """Nominal vs empirical coverage on test for several levels (conformal, calibrated on development data)."""
    levels = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
    cv = cv.assign(residual=cv["y_true"] - cv["y_pred"])
    cv["grange"], cv["direction"] = categorise(df, cv["ts"], acfg)
    out = []
    for h, ch in cv.groupby("horizon"):
        mc = iv.MondrianConformal(levels, acfg["intervals"]["min_bin_size"]).fit(ch["residual"], ch["grange"], ch["direction"])
        th = t[t["horizon"] == h]
        for L in levels:
            lo, hi = mc.interval(th["y_pred"].to_numpy(), th["grange"], th["direction"], L)
            out.append({"horizon": h, "level": L, **iv.interval_metrics(th["y_true"], lo, hi)})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ figures

def fig_calibration(curve, path):
    fig, ax = plt.subplots(figsize=(5.5, 5))
    ax.plot([0.45, 1], [0.45, 1], color="#a0aec0", ls="--", label="perfect calibration")
    for h, g in curve.groupby("horizon"):
        ax.plot(g["level"], g["coverage"], marker="o", label=f"{h} min")
    ax.set_xlabel("nominal coverage")
    ax.set_ylabel("observed coverage on test period")
    ax.set_title("Conformal interval calibration (test)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fig_segments(seg_tbl, path, title):
    show = seg_tbl[seg_tbl["segmentation"].isin(["glucose range (current)", "direction", "time since bolus",
                                                  "time of day"]) & (seg_tbl["n"] >= 100)]
    fig, ax = plt.subplots(figsize=(9, 0.32 * len(show) + 1.5))
    y = np.arange(len(show))
    ax.barh(y + 0.2, show["persistence MAE"], height=0.4, color="#a0aec0", label="persistence")
    ax.barh(y - 0.2, show["MAE"], height=0.4, color="#38a169", label="random forest")
    ax.set_yticks(y, [f"{a}: {b}" for a, b in zip(show["segmentation"], show["segment"])], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("MAE (mmol/L)")
    ax.set_title(title)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def pick_examples(df, t30, tz, complete: pd.Series):
    """Representative 4-hour windows in the test period, chosen by rule (not by how well the model did).

    Candidates need a complete last hour of CGM and a non-censored current reading, so sensor artifacts around
    gaps are not mistaken for real changes. Windows do not overlap.
    """
    t = t30.set_index("ts")
    f = df.loc[t.index]
    ok = complete.reindex(t.index).fillna(False).to_numpy() & (f["current_is_low_limit"] == 0).to_numpy()
    rate = f["velocity_15m_mmol_per_h"].where(ok)
    local_hour = f.index.tz_convert(tz).hour
    carbs = f["carbs_last_30m"].where(ok, 0)
    roll_std = f["current_glucose"].where(ok).rolling(48, min_periods=44).std()
    actual = t["y_true"].where(ok)
    rankings = {
        "stable": (roll_std.sort_values().index - pd.Timedelta(hours=2)),
        "rapid rise": rate.sort_values(ascending=False).dropna().index,
        "rapid fall": rate.sort_values().dropna().index,
        "post-meal / bolus": carbs[carbs >= 40].index + pd.Timedelta(minutes=60),
        "overnight": f.index[(local_hour == 3) & ok],
        "hypoglycaemia": actual.sort_values().dropna().index,
        "hyperglycaemia": actual.sort_values(ascending=False).dropna().index,
    }
    picked = {}
    for name, cands in rankings.items():
        if name in ("post-meal / bolus", "overnight"):     # middle of the test period, not an extreme
            cands = list(cands[len(cands) // 2:]) + list(cands[: len(cands) // 2])
        for c in cands:
            if all(abs(c - p) > pd.Timedelta(hours=4) for p in picked.values()):
                picked[name] = c
                break
    return {k: (v - pd.Timedelta(hours=2), v + pd.Timedelta(hours=2)) for k, v in picked.items()}


def fig_examples(t, cgm, windows, thr, tz, path, horizon=30):
    n = len(windows)
    fig, axes = plt.subplots((n + 1) // 2, 2, figsize=(13, 3.2 * ((n + 1) // 2)))
    axes = axes.ravel()
    th = t[t["horizon"] == horizon].copy()
    th["target_time"] = th["ts"] + pd.Timedelta(minutes=horizon)
    for ax, (name, (a, b)) in zip(axes, windows.items()):
        c = cgm[(cgm["measured_at"] >= a) & (cgm["measured_at"] <= b)]
        p = th[(th["target_time"] >= a) & (th["target_time"] <= b)].sort_values("target_time").copy()
        gap = p["target_time"].diff() > pd.Timedelta(minutes=10)   # do not draw across missing predictions
        p.loc[gap, ["y_pred", "lo_0.8", "hi_0.8", "lo_0.95", "hi_0.95"]] = np.nan
        ax.axhspan(thr["low"], thr["high"], color="#c6f6d5", alpha=0.5, lw=0)
        ax.fill_between(p["target_time"], p["lo_0.95"], p["hi_0.95"], color="#9ae6b4", alpha=0.35, lw=0, label="95% interval")
        ax.fill_between(p["target_time"], p["lo_0.8"], p["hi_0.8"], color="#68d391", alpha=0.45, lw=0, label="80% interval")
        ax.plot(p["target_time"], p["y_pred"], color="#276749", lw=1.4, label=f"predicted {horizon} min ahead")
        ax.plot(c["measured_at"], c["glucose_mmol"], "o", ms=2.5, color="#2b6cb0", label="actual CGM")
        ax.set_title(f"{name} — {a.tz_convert(tz):%d %b %H:%M}", fontsize=10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
        ax.set_ylabel("mmol/L")
    for ax in axes[n:]:
        ax.axis("off")
    axes[0].legend(fontsize=7, loc="upper left")
    fig.text(0.99, 0.005, "Research analysis only. Not a medical device; not for treatment decisions.", ha="right",
             fontsize=7, color="#718096")
    fig.tight_layout(rect=(0, 0.015, 1, 1))
    fig.savefig(path, dpi=120)
    plt.close(fig)


def fig_shap(values, X, imp, base, row, path_prefix, horizon):
    import shap
    top = imp["feature"].head(20).tolist()
    idx = [list(X.columns).index(c) for c in top]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(range(len(top))[::-1], imp["mean |SHAP|"].head(20), color="#38a169")
    ax.set_yticks(range(len(top))[::-1], top, fontsize=8)
    ax.set_xlabel("mean |SHAP| (mmol/L of predicted change)")
    ax.set_title(f"Global feature importance — {horizon} min")
    fig.tight_layout()
    fig.savefig(f"{path_prefix}_importance.png", dpi=130)
    plt.close(fig)
    plt.figure()
    shap.summary_plot(values[:, idx], X.iloc[:, idx], show=False, max_display=20, plot_size=(9, 7))
    plt.title(f"SHAP summary — {horizon} min (predicted change)")
    plt.tight_layout()
    plt.savefig(f"{path_prefix}_summary.png", dpi=120)
    plt.close("all")
    exp = shap.Explanation(values=values[row], base_values=base, data=X.iloc[row].to_numpy(), feature_names=list(X.columns))
    plt.figure()
    shap.plots.waterfall(exp, max_display=12, show=False)
    plt.title(f"One prediction explained — {horizon} min (predicted change)", fontsize=10)
    plt.tight_layout()
    plt.savefig(f"{path_prefix}_example.png", dpi=120)
    plt.close("all")


# ------------------------------------------------------------------- main

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only-examples", action="store_true", help="redraw only the prediction-example figure")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("matplotlib", "shap", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    s, mcfg = config.settings(), config.models()
    acfg = mcfg["analysis"]
    tz = s["project"]["timezone"]
    thr = s["glucose_thresholds_mmol"]
    horizons = s["targets"]["horizons_minutes"]
    figs = config.path(s["report"]["figures_dir"])
    df, catalog, test, cv = load(s, mcfg)
    folds = splits.make_folds(s)

    def complete_last_hour():
        X_seq, names = train.build_sequence_inputs(s, mcfg, df.index)
        obs = X_seq[:, -12:, names.index("glucose_observed")].sum(axis=1)
        return obs, pd.Series(obs >= 12, index=df.index)

    def draw_examples(t):
        with __import__("src.db", fromlist=["duck"]).duck(read_only=True) as d:
            cgm = d.execute("SELECT measured_at, glucose_mmol FROM cgm_merged").df()
        cgm["measured_at"] = pd.to_datetime(cgm["measured_at"], utc=True)
        windows = pick_examples(df, t[t["horizon"] == 30], tz, complete_last_hour()[1])
        fig_examples(t, cgm, windows, thr, tz, figs / "test_prediction_examples.png")

    if args.only_examples:
        t, *_ = conformal_intervals(df, cv, test, acfg)
        draw_examples(t)
        log.info("redrew %s", figs / "test_prediction_examples.png")
        return

    # 1. headline metrics on test (and validation for comparison)
    head = []
    for (m, h), g in test.groupby(["model", "horizon"]):
        r = regression_metrics(g["y_true"], g["y_pred"])
        head.append({"model": LABEL.get(m, m), "key": m, "horizon": h, **r})
    head = pd.DataFrame(head)
    pers = head[head["key"] == "persistence"].set_index("horizon")["mae"]
    cv_mae = cv.groupby("horizon").apply(lambda g: regression_metrics(g["y_true"], g["y_pred"])["mae"], include_groups=False)
    mae_tbl = head.pivot_table(index="model", columns="horizon", values="mae")
    order = ["Persistence", "Linear regression", "LightGBM", "TCN", "Random forest"]
    mae_tbl = mae_tbl.reindex([o for o in order if o in mae_tbl.index])
    mae_tbl.columns = [f"{h}m MAE" for h in mae_tbl.columns]
    mae_tbl = mae_tbl.reset_index()
    rf_rows = head[head["key"] == RF].set_index("horizon")
    sel = pd.DataFrame({
        "horizon": [f"{h} min" for h in horizons],
        "test MAE": [rf_rows.loc[h, "mae"] for h in horizons],
        "validation MAE": [cv_mae[h] for h in horizons],
        "test vs persistence": [fmt_pct(1 - rf_rows.loc[h, "mae"] / pers[h]) for h in horizons],
        "RMSE": [rf_rows.loc[h, "rmse"] for h in horizons], "MAPE %": [rf_rows.loc[h, "mape_pct"] for h in horizons],
        "median AE": [rf_rows.loc[h, "median_ae"] for h in horizons], "R²": [rf_rows.loc[h, "r2"] for h in horizons],
        "bias": [rf_rows.loc[h, "bias"] for h in horizons], "P95 AE": [rf_rows.loc[h, "p95_ae"] for h in horizons],
    })
    months = test[test["model"].isin([RF, "persistence"])].assign(
        month=lambda d: d["ts"].dt.tz_convert(tz).dt.strftime("%Y-%m"))
    by_month = months.groupby(["month", "model", "horizon"]).apply(
        lambda g: np.mean(np.abs(g["y_pred"] - g["y_true"])), include_groups=False).unstack("model")
    by_month = by_month.assign(improvement=lambda d: [fmt_pct(1 - a / b) for a, b in zip(d[RF], d["persistence"])])
    by_month = by_month.reset_index().rename(columns={RF: "random forest MAE", "persistence": "persistence MAE"})
    by_month = by_month[by_month["horizon"].isin([30, 60])]

    # 2. intervals
    log.info("intervals")
    t, conf_tbl, seq_tbl, _ = conformal_intervals(df, cv, test, acfg)
    qr_tbl = quantile_intervals(df, catalog, test, s, mcfg)
    curve = coverage_curve(cv, t, df, acfg)
    fig_calibration(curve, figs / "test_interval_calibration.png")
    int_tbl = pd.concat([conf_tbl, qr_tbl]).assign(
        level=lambda d: (d["level"] * 100).astype(int).astype(str) + "%", horizon=lambda d: d["horizon"].astype(str) + " min",
        coverage=lambda d: d["coverage"].map(fmt_pct), below=lambda d: d["below"].map(fmt_pct),
        above=lambda d: d["above"].map(fmt_pct))[["method", "horizon", "level", "coverage", "mean_width", "below", "above", "n"]]
    seq_sum = seq_tbl.groupby(["horizon", "level"]).agg(mean_coverage=("coverage", "mean"), min_coverage=("coverage", "min"),
                                                        max_coverage=("coverage", "max"), months=("fold", "nunique")).reset_index()
    for c in ("mean_coverage", "min_coverage", "max_coverage"):
        seq_sum[c] = seq_sum[c].map(fmt_pct)
    t["grange"] = t["grange"].astype(str)
    cov_g = []
    for (h, g), d in t[t["horizon"].isin([30, 60])].groupby(["horizon", "grange"]):
        m80 = iv.interval_metrics(d["y_true"], d["lo_0.8"], d["hi_0.8"])
        m95 = iv.interval_metrics(d["y_true"], d["lo_0.95"], d["hi_0.95"])
        cov_g.append({"horizon": f"{h} min", "current glucose": g, "n": m80["n"], "80% coverage": fmt_pct(m80["coverage"]),
                      "80% width": m80["mean_width"], "95% coverage": fmt_pct(m95["coverage"]), "95% width": m95["mean_width"]})
    cov_g = pd.DataFrame(cov_g)
    ex = t[(t["horizon"] == 30) & (t["current_glucose"].between(7.5, 8.5))].iloc[len(t) // 50]
    example_txt = (f"Predicted at {ex['ts'].tz_convert(tz):%Y-%m-%d %H:%M} (current {ex['current_glucose']:.1f} mmol/L): "
                   f"**{ex['y_pred']:.1f} mmol/L**, 80% interval {ex['lo_0.8']:.1f}–{ex['hi_0.8']:.1f}, "
                   f"95% interval {ex['lo_0.95']:.1f}–{ex['hi_0.95']:.1f}. Actual: {ex['y_true']:.1f}.")

    # 3. SHAP
    log.info("SHAP")
    shap_sections, top_rows = [], {}
    rng = np.random.default_rng(s["project"]["random_seed"])
    for h in acfg["shap"]["horizons"]:
        from src import registry
        est, meta, _, _ = registry.load_production(h)
        th = t[t["horizon"] == h]
        pick = np.sort(rng.choice(len(th), size=min(acfg["shap"]["sample_size"], len(th)), replace=False))
        X = df.loc[th["ts"].to_numpy()[pick], meta["features"]]
        vals, Xm, base = explain.shap_values(est, X, acfg["shap"]["n_jobs"])
        imp = explain.importance(vals, list(X.columns), catalog)
        row = int(np.argmax(np.abs(vals).sum(axis=1) * (X["current_glucose"].between(5, 12)).to_numpy()))
        fig_shap(vals, Xm, imp, base, row, str(figs / f"shap_{h}m"), h)
        top_rows[h] = (X.index[row], th.iloc[pick[row]])
        shap_sections.append(
            f"### {h} min\n\nBy feature group:\n\n{md_table(explain.group_importance(imp), '{:.3f}')}\n\n"
            f"Top 15 features:\n\n{md_table(imp.head(15).reset_index(names='rank'), '{:.3f}')}\n\n"
            f"![importance](figures/shap_{h}m_importance.png)\n\n![summary](figures/shap_{h}m_summary.png)\n\n"
            f"Individual prediction ({X.index[row].tz_convert(tz):%Y-%m-%d %H:%M}, current glucose "
            f"{X['current_glucose'].iloc[row]:.1f}, actual change {th['y_true'].iloc[pick[row]] - th['current_glucose'].iloc[pick[row]]:+.1f}, "
            f"predicted change {th['y_pred'].iloc[pick[row]] - th['current_glucose'].iloc[pick[row]]:+.1f} mmol/L):\n\n"
            f"![example](figures/shap_{h}m_example.png)")

    # 4. error analysis
    log.info("error analysis")
    tr_mask = splits.mask(df.index, folds, "final", "train")
    high_ins = float(df.loc[tr_mask, "insulin_last_60m"].quantile(acfg["categories"]["high_insulin_quantile"]))
    obs_last_hour, _ = complete_last_hour()
    gap_label = pd.Series(np.select([obs_last_hour >= 12, obs_last_hour >= 10], ["complete last hour",
                                                                                "1–2 readings missing in last hour"],
                                    default="≥ 3 readings missing in last hour"), index=df.index)
    segs = ea.segments(df.loc[t["ts"].unique()], acfg, tz, high_ins, gap_label)
    seg_tables = {h: ea.segment_table(test, segs, h, RF, "persistence") for h in horizons}
    fig_segments(seg_tables[30], figs / "test_error_segments_30m.png", "30-min MAE by condition (test)")
    worst = seg_tables[30][seg_tables[30]["n"] >= 200].sort_values("MAE", ascending=False).head(6)
    seg_md = []
    for col in segs.columns:
        tb = seg_tables[30][seg_tables[30]["segmentation"] == col].drop(columns="segmentation")
        tb = tb.assign(improvement=tb["improvement"].map(fmt_pct))
        seg_md.append(f"#### {col}\n\n{md_table(tb, '{:.3f}')}")
    wide = []
    for h in horizons:
        st = seg_tables[h]
        for col in ("glucose range (current)", "direction"):
            for _, r in st[st["segmentation"] == col].iterrows():
                wide.append({"segmentation": col, "segment": r["segment"], "horizon": h, "MAE": r["MAE"]})
    wide = pd.DataFrame(wide).pivot_table(index=["segmentation", "segment"], columns="horizon", values="MAE").reset_index()
    wide.columns = [c if isinstance(c, str) else f"{c}m MAE" for c in wide.columns]

    # 5. events
    cls = ea.threshold_classification(test, RF, thr)
    for c in ("sensitivity (recall)", "specificity", "precision", "F1", "false-negative rate"):
        cls[c] = cls[c].map(fmt_pct)
    lb80 = t[["ts", "horizon"]].assign(lower=t["lo_0.8"])
    lb95 = t[["ts", "horizon"]].assign(lower=t["lo_0.95"])
    low30 = pd.concat([ea.becoming_low(test, RF, thr["low"], lb80),
                       ea.becoming_low(test, RF, thr["low"], lb95).iloc[[1]]], ignore_index=True)
    low30.loc[1, "alert rule"] += " (80% interval)"
    low30.loc[2, "alert rule"] += " (95% interval)"
    low30_p = ea.becoming_low(test, "persistence", thr["low"]).assign(**{"alert rule": "persistence (current < 3.9)"})
    low30 = pd.concat([low30, low30_p], ignore_index=True)
    for c in ("sensitivity (recall)", "specificity", "precision", "F1", "false-negative rate"):
        low30[c] = low30[c].map(fmt_pct)

    # 6. examples
    draw_examples(t)

    hold = __import__("src.holdout", fromlist=["already_run"]).already_run().iloc[-1]
    test_period = folds.query("role == 'test'").iloc[0]
    fmt_t = lambda x: pd.Timestamp(x).tz_convert(tz).strftime("%Y-%m-%d")
    text = f"""# Test-period evaluation and analysis — Phase 5

> **{DISCLAIMER}**
>
> Nothing here is a dosing, correction, basal or carbohydrate recommendation, and predictions are not medically
> accurate. Feature importance is not evidence of cause and effect.

Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Test period **{fmt_t(test_period['start'])} → {fmt_t(test_period['end'])}**
({len(test[(test['model'] == RF) & (test['horizon'] == 30)]):,} prediction times at 30 min), evaluated once on
{pd.Timestamp(hold['run_at']):%Y-%m-%d %H:%M} UTC (git `{(hold['git_commit'] or 'none')[:7]}`). The model was chosen on
validation months only (reports/model_comparison.md) and not changed after this evaluation.

## 1. Held-out test results

Selected model: **random forest predicting the glucose change** (one model per horizon, trained on all data before
the test period). Reference models were fixed before the test run.

{md_table(mae_tbl, '{:.3f}')}

### Selected model in detail

{md_table(sel, '{:.3f}')}

Test MAE is close to (slightly better than) validation MAE, so the validation estimate was not optimistic.

### By month (test period)

{md_table(by_month, '{:.3f}')}

## 2. Prediction intervals

**Primary method — Mondrian split conformal** (declared before the test run). Residuals of out-of-fold validation
predictions (development period only) are grouped by current glucose range × direction of change; each bin's
residual quantiles give the interval. Comparison: LightGBM quantile regression (raw, not conformalised).

Example (30 min): {example_txt}

### Calibration check inside the development period

Calibrate on earlier validation months, check the next month (rolling, {int(seq_sum['months'].max())} months per row):

{md_table(seq_sum, '{:.3f}')}

### Test period

{md_table(int_tbl, '{:.2f}')}

![Calibration](figures/test_interval_calibration.png)

### Conformal coverage by current glucose range (test)

{md_table(cov_g, '{:.2f}')}

## 3. Explainability (SHAP, random forest)

SHAP values explain the model's **predicted change** in glucose. Importance shows what the model relies on, not
what causes glucose to change; correlated inputs (e.g. the various insulin windows) share credit.
{acfg['shap']['sample_size']} random test predictions per horizon.

{chr(10).join(shap_sections)}

## 4. Error analysis (30 min unless stated)

Segments use information known at prediction time. *Improvement* is versus persistence on the same samples.

### Conditions with the highest error (n ≥ 200)

{md_table(worst.drop(columns=[]).assign(improvement=worst['improvement'].map(fmt_pct)), '{:.3f}')}

![Segments](figures/test_error_segments_30m.png)

### MAE by glucose range and direction, all horizons

{md_table(wide, '{:.3f}')}

### All segmentations

High recent insulin = more than {high_ins:.2f} U in the last 60 min (the {acfg['categories']['high_insulin_quantile']:.0%}
quantile of the training period). Rapid change = |rate| ≥ {acfg['categories']['rapid_rate_mmol_per_min']} mmol/L/min
(15-min rate); stable = |rate| < {acfg['categories']['stable_rate_mmol_per_min']}. CGM data = readings available at T in
the last hour (missing-data robustness). Exercise data is not available.

{chr(10).join(seg_md)}

## 5. Low and high glucose events

Classification of whether predicted future glucose falls on the same side of each threshold as the actual value.
This is an analysis of model behaviour, **not** an alarm system.

{md_table(cls)}

### Becoming low within 30 minutes

Prediction times where glucose is currently ≥ {thr['low']} mmol/L. Event: an actual reading < {thr['low']} at +15 or
+30 min. Compared rules: the point prediction, the lower edge of the conformal interval (more cautious), and
persistence (which can never flag a *new* low, so it is a floor, not a competitor).

{md_table(low30)}

## 6. Prediction examples

Windows chosen by rule, not by model performance: the most stable 4 h, the fastest rise and fall, a meal bolus, a
night, and the lowest and highest readings. Candidates need a complete last hour of CGM data, and windows do not
overlap. Gaps in the predictions are left as gaps. The green line is the prediction made 30 minutes
earlier, drawn at the time it predicts.

![Examples](figures/test_prediction_examples.png)
"""
    out = config.path(s["report"]["output_dir"]) / "test_report.md"
    out.write_text(text)
    log.info("wrote %s", out)


if __name__ == "__main__":
    main()
