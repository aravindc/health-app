"""Model comparison report from cross-validation results.

    python -m src.evaluate

Reads data/predictions/cv_predictions.parquet (written by `python -m src.train --cv`)
and writes reports/model_comparison.md with figures. Uses validation folds
only; the test period stays untouched until model selection is complete.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src import DISCLAIMER, config
from src.metrics import regression_metrics
from src.train import PRED_DIR
from src.validate_data import md_table

log = logging.getLogger("evaluate")
COMPLEXITY = ["persistence", "linear_trend", "linear", "random_forest", "xgboost", "lightgbm", "tcn", "lstm"]
LABEL = {"persistence": "Persistence", "linear_trend": "Linear trend", "linear": "Linear regression",
         "random_forest": "Random forest", "xgboost": "XGBoost", "lightgbm": "LightGBM", "lstm": "LSTM",
         "tcn": "TCN"}
COLORS = {"persistence": "#718096", "linear_trend": "#a0aec0", "linear": "#3182ce", "random_forest": "#38a169",
          "xgboost": "#dd6b20", "lightgbm": "#805ad5", "lstm": "#d53f8c", "tcn": "#319795"}
KEYS = ["model", "target_type", "feature_set"]


def run_name(model, target_type, feature_set, default_fs):
    name = LABEL.get(model, model)
    if target_type != "-":
        name += f" ({target_type})"
    if feature_set not in ("-", default_fs, "sequence"):
        name += f" [{feature_set}]"
    return name


def pooled(preds: pd.DataFrame, exclude_censored: bool = False) -> pd.DataFrame:
    p = preds[preds["target_censored"].isna()] if exclude_censored else preds
    rows = [{**dict(zip(KEYS + ["horizon"], k)), **regression_metrics(g["y_true"], g["y_pred"])}
            for k, g in p.groupby(KEYS + ["horizon"])]
    return pd.DataFrame(rows)


def per_fold_mae(preds: pd.DataFrame) -> pd.DataFrame:
    return (preds.assign(ae=(preds["y_pred"] - preds["y_true"]).abs())
            .groupby(KEYS + ["horizon", "fold"])["ae"].mean().rename("mae").reset_index())


def compare(folds: pd.DataFrame, a: tuple, b: tuple, h: int) -> tuple[float, float]:
    """(relative MAE improvement of b over a pooled across folds, share of folds where b beats a)."""
    sel = lambda k: folds[(folds["model"] == k[0]) & (folds["target_type"] == k[1]) & (folds["feature_set"] == k[2])
                          & (folds["horizon"] == h)].set_index("fold")["mae"]
    fa, fb = sel(a), sel(b)
    common = fa.index.intersection(fb.index)
    rel = 1 - fb[common].mean() / fa[common].mean()
    return float(rel), float((fb[common] < fa[common]).mean())


def select(pool: pd.DataFrame, folds: pd.DataFrame, horizon: int, default_fs: str, rule: dict) -> tuple[tuple, list]:
    """Walk up the complexity ladder; adopt a model only if it materially beats the current choice."""
    p = pool[(pool["horizon"] == horizon) & pool["feature_set"].isin(["-", default_fs, "sequence"])]
    best_variant = {}
    for m in COMPLEXITY:
        r = p[p["model"] == m]
        if len(r):
            best_variant[m] = tuple(r.sort_values("mae").iloc[0][KEYS])
    rules = [best_variant[m] for m in ("persistence", "linear_trend") if m in best_variant]
    current = min(rules, key=lambda k: p.loc[(p["model"] == k[0]) & (p["target_type"] == k[1]), "mae"].iloc[0])
    log_rows = []
    for m in ("linear", "random_forest", "xgboost", "lightgbm", "tcn", "lstm"):
        if m not in best_variant:
            continue
        cand = best_variant[m]
        rel, wins = compare(folds, current, cand, horizon)
        adopt = rel >= rule["min_relative_improvement"] and wins >= rule["min_fold_win_share"]
        # Equal complexity tier (tree ensembles): switch only if better than the current tree model.
        log_rows.append((LABEL[cand[0]] + f" ({cand[1]})", LABEL[current[0]], rel * 100, wins * 100,
                         "adopted" if adopt else "not adopted"))
        if adopt:
            current = cand
    return current, log_rows


def fig_by_horizon(pool: pd.DataFrame, metric: str, default_fs: str, path) -> None:
    p = pool[pool["feature_set"].isin(["-", default_fs, "sequence"])]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for (m, tt), g in p.groupby(["model", "target_type"]):
        g = g.sort_values("horizon")
        ax.plot(g["horizon"], g[metric], marker="o", color=COLORS.get(m), ls="--" if tt == "direct" else "-",
                label=run_name(m, tt, "-", default_fs))
    ax.set_xlabel("prediction horizon (min)")
    ax.set_ylabel(f"{metric.upper()} (mmol/L)")
    ax.set_xticks(sorted(p["horizon"].unique()))
    ax.set_title(f"{metric.upper()} by horizon — rolling-origin validation (pooled)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fig_folds(folds: pd.DataFrame, horizon: int, runs: list[tuple], default_fs: str, path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for k in runs:
        g = folds[(folds["model"] == k[0]) & (folds["target_type"] == k[1]) & (folds["feature_set"] == k[2])
                  & (folds["horizon"] == horizon)].sort_values("fold")
        ax.plot(g["fold"], g["mae"], marker="o", color=COLORS.get(k[0]), label=run_name(*k, default_fs))
    ax.set_ylabel("MAE (mmol/L)")
    ax.set_title(f"{horizon}-min MAE per validation month")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fig_ablation(pool: pd.DataFrame, sets: list[str], path) -> None:
    p = pool[(pool["model"] == "lightgbm") & (pool["target_type"] == "delta") & pool["feature_set"].isin(sets)]
    hs = sorted(p["horizon"].unique())
    fig, ax = plt.subplots(figsize=(9, 4.2))
    w = 0.8 / len(sets)
    for i, fs in enumerate(sets):
        g = p[p["feature_set"] == fs].set_index("horizon").reindex(hs)
        ax.bar(np.arange(len(hs)) + i * w, g["mae"], width=w, label=fs)
    ax.set_xticks(np.arange(len(hs)) + w * (len(sets) - 1) / 2, [f"{h} min" for h in hs])
    ax.set_ylabel("MAE (mmol/L)")
    ax.set_ylim(p["mae"].min() * 0.9, p["mae"].max() * 1.03)
    ax.set_title("Feature ablation — LightGBM (delta)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    s, mcfg = config.settings(), config.models()
    default_fs = mcfg["default_feature_set"]
    preds = pd.read_parquet(config.path(PRED_DIR) / "cv_predictions.parquet")
    pool, pool_unc = pooled(preds), pooled(preds, exclude_censored=True)
    folds = per_fold_mae(preds)
    horizons = sorted(pool["horizon"].unique())
    figs = config.path(s["report"]["figures_dir"])
    figs.mkdir(parents=True, exist_ok=True)

    main_pool = pool[pool["feature_set"].isin(["-", default_fs, "sequence"])].copy()
    main_pool["name"] = [run_name(m, t, f, default_fs) for m, t, f in main_pool[KEYS].itertuples(index=False)]
    order = {m: i for i, m in enumerate(COMPLEXITY)}
    main_pool = main_pool.sort_values(["model", "target_type"], key=lambda c: c.map(order) if c.name == "model" else c)

    # ---- headline table (MAE by horizon) + improvement over persistence
    mae = main_pool.pivot_table(index="name", columns="horizon", values="mae", sort=False)
    pers = mae.loc["Persistence"]
    head = mae.copy()
    head.columns = [f"{h}m MAE" for h in head.columns]
    for h in horizons:
        head[f"{h}m vs persistence"] = [f"{(1 - v / pers[h]) * 100:+.1f}%" for v in mae[h]]
    head = head.reset_index().rename(columns={"name": "model"})

    # ---- full metrics per horizon
    detail = []
    for h in horizons:
        t = main_pool[main_pool["horizon"] == h][["name", "mae", "rmse", "mape_pct", "median_ae", "r2", "bias", "p95_ae", "n"]]
        unc = pool_unc[pool_unc["horizon"] == h].set_index(KEYS)["mae"]
        t = t.assign(**{"MAE excl. censored": [unc.get(tuple(k), np.nan) for k in
                                                main_pool[main_pool["horizon"] == h][KEYS].itertuples(index=False)]})
        t = t.rename(columns={"name": "model", "mae": "MAE", "rmse": "RMSE", "mape_pct": "MAPE %",
                              "median_ae": "median AE", "r2": "R²", "bias": "bias", "p95_ae": "P95 AE"})
        detail.append(f"### {h} min\n\n" + md_table(t, "{:.3f}"))

    # ---- direct vs delta
    dd = []
    for m in ("linear", "random_forest", "xgboost", "lightgbm"):
        for h in horizons:
            a, b = (m, "direct", default_fs), (m, "delta", default_fs)
            if not ((pool[KEYS] == list(a)).all(axis=1).any() and (pool[KEYS] == list(b)).all(axis=1).any()):
                continue
            rel, wins = compare(folds, a, b, h)
            ma = pool[(pool[KEYS] == list(a)).all(axis=1) & (pool["horizon"] == h)]["mae"].iloc[0]
            mb = pool[(pool[KEYS] == list(b)).all(axis=1) & (pool["horizon"] == h)]["mae"].iloc[0]
            dd.append((LABEL[m], f"{h} min", ma, mb, f"{rel * 100:+.1f}%", f"{wins * 100:.0f}%"))
    dd = pd.DataFrame(dd, columns=["model", "horizon", "MAE direct", "MAE delta", "delta vs direct",
                                   "folds where delta wins"])

    # ---- ablation
    ab_sets = mcfg["experiments"]["ablation"]["feature_sets"]
    ab = pool[(pool["model"] == "lightgbm") & (pool["target_type"] == "delta") & pool["feature_set"].isin(ab_sets)]
    ab_tbl = ab.pivot_table(index="feature_set", columns="horizon", values="mae").reindex(ab_sets)
    ab_tbl.columns = [f"{h}m MAE" for h in ab_tbl.columns]
    ab_rows = []
    for a, b, label in [("glucose_only", "glucose_insulin_no_iob", "adding insulin features"),
                        ("glucose_insulin_no_iob", "glucose_insulin", "adding estimated IOB"),
                        ("glucose_insulin", "glucose_insulin_carbs", "adding carbs")]:
        if a in ab_tbl.index and b in ab_tbl.index:
            for h in horizons:
                rel, wins = compare(folds, ("lightgbm", "delta", a), ("lightgbm", "delta", b), h)
                ab_rows.append((label, f"{h} min", f"{rel * 100:+.1f}%", f"{wins * 100:.0f}%"))
    ab_eff = pd.DataFrame(ab_rows, columns=["change", "horizon", "MAE change (− = worse)", "folds improved"])

    # ---- selection
    sel_rows, sel_logs = [], []
    for h in horizons:
        chosen, logs = select(pool, folds, h, default_fs, mcfg["selection"])
        r = pool[(pool[KEYS] == list(chosen)).all(axis=1) & (pool["horizon"] == h)].iloc[0]
        sel_rows.append((f"{h} min", run_name(*chosen, default_fs), r["mae"], f"{(1 - r['mae'] / pers[h]) * 100:.1f}%"))
        sel_logs += [(f"{h} min", *row) for row in logs]
    sel = pd.DataFrame(sel_rows, columns=["horizon", "selected (validation only)", "MAE", "better than persistence by"])
    sel_log = pd.DataFrame(sel_logs, columns=["horizon", "candidate", "vs current choice", "MAE improvement %",
                                              "folds won %", "decision"])

    # ---- sequence models vs the best tabular model
    seq_rows = []
    for m in ("lstm", "tcn"):
        k = (m, "delta", "sequence")
        if not (pool[KEYS] == list(k)).all(axis=1).any():
            continue
        for h in horizons:
            ref = ("random_forest", "delta", default_fs)
            rel, wins = compare(folds, ref, k, h)
            mk = pool[(pool[KEYS] == list(k)).all(axis=1) & (pool["horizon"] == h)]["mae"].iloc[0]
            mr = pool[(pool[KEYS] == list(ref)).all(axis=1) & (pool["horizon"] == h)]["mae"].iloc[0]
            seq_rows.append((LABEL[m], f"{h} min", mk, mr, f"{rel * 100:+.1f}%", f"{wins * 100:.0f}%"))
    seq_tbl = pd.DataFrame(seq_rows, columns=["model", "horizon", "MAE", "Random forest MAE",
                                              "vs random forest (− = worse)", "months where it wins"])

    # ---- tuning (Phase 7c)
    tune_rows = []
    for m, base_fs in (("random_forest", default_fs), ("lightgbm", default_fs), ("tcn", "sequence")):
        a, b = (m, "delta", base_fs), (m, "delta", f"{base_fs}+tuned")
        if not (pool[KEYS] == list(b)).all(axis=1).any():
            continue
        for h in horizons:
            rel, wins = compare(folds, a, b, h)
            rel_rf, wins_rf = compare(folds, ("random_forest", "delta", default_fs), b, h)
            mb = pool[(pool[KEYS] == list(b)).all(axis=1) & (pool["horizon"] == h)]["mae"].iloc[0]
            tune_rows.append((LABEL[m], f"{h} min", mb, f"{rel * 100:+.1f}%", f"{wins * 100:.0f}%",
                              f"{rel_rf * 100:+.1f}%", f"{wins_rf * 100:.0f}%"))
    tune_tbl = pd.DataFrame(tune_rows, columns=["model (tuned)", "horizon", "MAE", "vs same model, defaults",
                                                "months improved", "vs random forest (defaults)", "months better"])
    choices_path = config.path(PRED_DIR) / "tuning_choices.csv"
    tune_choice = ""
    if choices_path.exists():
        ch = pd.read_csv(choices_path)
        tune_choice = ", ".join(f"{LABEL.get(m, m)} {int(g['default chosen'].sum())}/{len(g)}"
                                for m, g in ch.groupby("model"))

    # ---- folds
    fold_runs = [("persistence", "-", "-"), ("linear", "delta", default_fs), ("random_forest", "delta", default_fs),
                 ("xgboost", "delta", default_fs), ("lightgbm", "delta", default_fs), ("lstm", "delta", "sequence"),
                 ("tcn", "delta", "sequence")]
    fold_runs = [k for k in fold_runs if ((folds[KEYS] == list(k)).all(axis=1)).any()]
    fstab = folds[(folds["horizon"] == 30)].copy()
    fstab = fstab[[tuple(k) in set(fold_runs) for k in fstab[KEYS].itertuples(index=False)]]
    fstab["name"] = [run_name(*k, default_fs) for k in fstab[KEYS].itertuples(index=False)]
    ftab = fstab.pivot_table(index="fold", columns="name", values="mae").reset_index()

    fig_by_horizon(pool, "mae", default_fs, figs / "model_mae_by_horizon.png")
    fig_by_horizon(pool, "rmse", default_fs, figs / "model_rmse_by_horizon.png")
    fig_folds(folds, 30, fold_runs, default_fs, figs / "model_mae_by_fold_30m.png")
    if len(ab):
        fig_ablation(pool, ab_sets, figs / "model_ablation.png")

    n_folds = preds["fold"].nunique()
    text = f"""# Model comparison — Phases 3 and 4

> **{DISCLAIMER}**

Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from `{PRED_DIR}/cv_predictions.parquet`.

**All numbers are from {n_folds} rolling-origin validation months (Dec 2025 – Jul 2026). The test period
({s['splits']['test_start']} onward) has not been used** and stays untouched until model selection is complete
(after the sequence models in Phase 4; the test evaluation is the next step). Metrics are pooled over all validation samples on the glucose scale
(mmol/L). Every model is trained and evaluated on the same samples: a current reading, a target, and insulin known.
Hyperparameters are fixed defaults (`config/models.yaml`), not tuned on validation data.

## Findings during this phase

1. **Day of year removed from model inputs.** With about one year of history, day of year acts as a disguised date
   index: a validation month's values never appeared in training, and tree models used it to memorise
   period-specific levels. In December (cv01), LightGBM's 30-min MAE was 1.239 with these features and 0.934 without.
   They remain in the feature table (group `calendar`) for analysis only.
2. **Linear trend is worse than persistence at every horizon.** Glucose trends in this data reverse quickly
   (plausibly Control-IQ and boluses counteracting them), so extrapolating the 15-min velocity overshoots. A
   smoother slope or the pump's own rate did not fix it; halving the trend only matched persistence. The baseline
   is kept as specified. A damped variant was not added, because tuning the damping on these folds would leak into
   model selection.
3. **Gradient boosting does not beat random forest.** The three tree ensembles are within ~1–3% of each other.
   Random forest is slightly better in most months, possibly because its averaging copes better with the drift in
   insulin needs over 2026. Hyperparameters are untuned defaults.
4. **Sequence models (LSTM, TCN) match but do not beat random forest.** Both are within 0.5–1.7% of it, and slightly
   worse in most months. The TCN is marginally better than the LSTM. Following the spec's principle (prefer the
   simpler model unless the complex one materially improves), neither is adopted. Their raw 2-hour inputs carry
   about the same information as the engineered features.

## Headline: MAE by horizon (feature set `{default_fs}`)

{md_table(head, '{:.3f}')}

![MAE by horizon](figures/model_mae_by_horizon.png)

![RMSE by horizon](figures/model_rmse_by_horizon.png)

## Model selection

Rule: start from the better rule-based baseline, then adopt a more complex model only if it lowers MAE by
≥ {mcfg['selection']['min_relative_improvement']:.0%} versus the current choice **and** wins in
≥ {mcfg['selection']['min_fold_win_share']:.0%} of validation months. Each learned model enters with its better
target type (direct or delta).

{md_table(sel, '{:.3f}')}

Decisions:

{md_table(sel_log, '{:.1f}')}

## Sequence models (Phase 4)

One network per fold predicts the glucose change at all four horizons from the last
{mcfg['sequence']['lookback_minutes']} minutes (5-min steps: glucose as known at T, observed flag, velocity, insulin,
bolus insulin, basal rate, estimated IOB, time of day). Early stopping uses the last
{mcfg['sequence']['inner_validation_fraction']:.0%} of each training fold, never the validation month. Inputs and
targets are standardised with training-fold statistics. Evaluated on exactly the same samples as the other models.

{md_table(seq_tbl, '{:.3f}') if len(seq_tbl) else '_not run_'}

## Hyperparameter tuning (Phase 7)

Nested: for each validation month, 8 configurations (the defaults plus 7 random draws) were compared on the last 60
days of that month's *training* data (separated by the embargo), at the 30-min horizon. The winner was refitted on the
whole training period and scored on the validation month, so these numbers are honest. Months where the inner check
kept the defaults: {tune_choice or 'n/a'}.

{md_table(tune_tbl, '{:.3f}') if len(tune_tbl) else '_not run_'}

Tuning helps LightGBM by 1–2%, which brings it level with the random forest. The random forest and TCN were already
near their best. No tuned model beats the selected random forest by the 5% the selection rule requires, so the
selection is unchanged.

## Direct glucose vs glucose-delta targets

{md_table(dd, '{:.3f}')}

## Feature ablation (LightGBM, delta target)

{md_table(ab_tbl.reset_index(), '{:.3f}') if len(ab_tbl) else '_not run_'}

{md_table(ab_eff) if len(ab_eff) else ''}

{'![Ablation](figures/model_ablation.png)' if len(ab) else ''}

## Stability across validation months (30 min)

{md_table(ftab, '{:.3f}')}

![Per-fold MAE](figures/model_mae_by_fold_30m.png)

## All metrics

`MAE excl. censored` drops samples whose target is a sensor LOW/HIGH reading (true value beyond the limit).
Bias is mean(prediction − actual).

{chr(10).join(detail)}
"""
    out = config.path(s["report"]["output_dir"]) / "model_comparison.md"
    out.write_text(text)
    log.info("wrote %s", out)


if __name__ == "__main__":
    main()
