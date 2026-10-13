"""reports/feature_report.md: samples, splits, leakage audit and the feature catalog."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src import DISCLAIMER, config
from src.validate_data import md_table


def write(settings: dict, feats: pd.DataFrame, tg: pd.DataFrame, catalog: pd.DataFrame, folds: pd.DataFrame,
          assignment: pd.DataFrame, audit: pd.DataFrame | None) -> None:
    tz = settings["project"]["timezone"]
    horizons = settings["targets"]["horizons_minutes"]
    hmax = max(horizons)
    df = feats.join(tg.drop(columns=["current_glucose"])).join(assignment)
    has_cur = df["current_glucose"].notna()
    usable = has_cur & df[f"glucose_{hmax}m"].notna()
    usable_ins = usable & df["insulin_known"]

    overview = pd.DataFrame([
        ("prediction times (5-min slots)", len(df)),
        ("with a current reading (≤ 15 min old)", int(has_cur.sum())),
        (f"… and a {hmax}-min target", int(usable.sum())),
        ("… and insulin known for the last 4 h", int(usable_ins.sum())),
    ], columns=["", "samples"])
    overview["%"] = overview["samples"] / len(df) * 100

    tgt = []
    for h in horizons:
        ok = has_cur & df[f"glucose_{h}m"].notna()
        d = df.loc[ok, f"delta_{h}m"]
        tgt.append((f"{h} min", int(ok.sum()), d.abs().mean(), d.std(), int(df.loc[ok, f"target_{h}m_censored"].notna().sum())))
    tgt = pd.DataFrame(tgt, columns=["horizon", "samples with target", "mean |delta| (mmol/L)",
                                     "std of delta", "censored targets"])

    fmt = lambda t: pd.Timestamp(t).tz_convert(tz).strftime("%Y-%m-%d %H:%M")
    fold_rows = []
    for _, r in folds.iterrows():
        m = (df.index >= r["start"]) & (df.index < r["end"])
        fold_rows.append((r["fold"], r["role"], fmt(r["start"]), fmt(r["end"]), int((m & usable).sum()),
                          int((m & usable_ins).sum())))
    fold_tbl = pd.DataFrame(fold_rows, columns=["fold", "role", "from (local)", "to (local)", "usable samples",
                                                "usable, insulin known"])

    nan_pct = feats.isna().mean() * 100
    cat = catalog.assign(**{"missing %": catalog["feature"].map(nan_pct)})
    cat_has = has_cur.to_numpy()
    cat["missing % (when current glucose exists)"] = catalog["feature"].map(feats[cat_has].isna().mean() * 100)

    if audit is None:
        audit_md = "Not run this time (`python -m src.features --audit N`)."
    else:
        bad = audit[audit["mismatches"] > 0]
        cases = ", ".join(f"{k} ({v})" for k, v in audit["case"].value_counts().items())
        audit_md = (f"Features were rebuilt from only the data known at **{len(audit)} prediction times** ({cases}) "
                    f"and compared with the full build: **{len(bad)} times with mismatches**"
                    + (" — investigate before training." if len(bad) else " — no future information reaches the features."))
        if len(bad):
            audit_md += "\n\n" + md_table(bad.assign(ts=bad["ts"].map(fmt)))

    icfg = settings["iob"]
    text = f"""# Feature report — {settings['features']['version']}

> **{DISCLAIMER}**

Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Built by `python -m src.features`; stored in DuckDB tables
`features_{settings['features']['version']}`, `targets`, `splits`, `split_assignment`, `grid_5min` and in
`data/features/features_{settings['features']['version']}.parquet`.

## Samples

Prediction times are a regular 5-minute grid over the modelling window. A sample is usable when a current reading
exists and the target exists. Models that use insulin features also need *insulin known*: the pump recorded basal
throughout the previous {settings['features']['insulin_lookback_minutes']} min. This excludes pump-off periods
such as 25–28 Oct 2025.

{md_table(overview, '{:.1f}')}

### Targets

{md_table(tgt, '{:.2f}')}

Target = reading nearest to T + h within ±{settings['targets']['tolerance_minutes']} min. Censored targets come from
sensor LOW/HIGH readings, so their true value lies beyond the sensor limit.

## Splits

{md_table(fold_tbl)}

- **Test** starts on the fixed date {settings['splits']['test_start']} and is not used until model selection is
  complete. The date stays fixed as new data arrives.
- **Rolling-origin folds** (cv01 …): validate on one month and train on everything before it (expanding window).
- **Embargo** of {settings['splits']['embargo_minutes']} min (= longest horizon) before every validation/test start,
  so no training target falls inside the evaluated period.
- Feature lookback may cross a boundary; that history was known at prediction time.
- No random splitting anywhere.

## Leakage audit

{audit_md}

## Leakage-prevention decisions

1. **Availability:** a CGM reading is usable at T only when `available_at <= T`. Late-arriving Tandem backfills
   (about 22 min late on average) are not used before they arrived.
2. **No interpolation:** missing readings stay missing. A lag may use an older reading (up to
   {settings['features']['lag_tolerance_minutes']} min earlier), never a newer one.
3. **Trailing windows only** — every rolling statistic covers (T − W, T].
4. **Rates use actual reading times**, so a lag that fell back to an older reading gives a correct slope.
5. **Insulin is a real-time stream:** standard boluses count from completion with the delivered dose. Extended
   boluses run at their programmed rate from the request and stop early only once a cancellation has happened.
   Basal counts from each rate change until the next. Only insulin delivered by T counts.
6. **Time since last bolus** uses the delivered dose for standard boluses (known at completion) and the
   programmed dose for extended boluses (known at request).
7. **Basal rate at T** is the last commanded rate; whether it later turned out to be a gap is not used as a feature.
8. **`insulin_known`** looks at the pump record in hindsight. It only filters samples for training/evaluation and
   is never a model input.
9. **Targets** are the measured truth at T + h. They are never inputs, and they are not limited by availability.
10. **No fitted transforms here.** Scaling/imputation (Phase 3) must be fitted on the training fold only.
11. **Estimated IOB** ({icfg['model']} curve, duration {icfg['insulin_duration_hours']} h, peak
    {icfg['peak_minutes']} min) only uses insulin delivered by T. It is an approximation, not a physiological
    measurement.
12. **Carbs** are an optional group, known at the bolus request where they were entered.

The `as_of` mechanism in `build_features` reproduces exactly what was known at any moment. The audit above and
`tests/test_leakage.py` compare it against the full build.

## Feature catalog

`missing %` is over all slots; the last column only counts slots that have a current reading.

{md_table(cat, '{:.1f}')}
"""
    path = config.path(settings["report"]["output_dir"]) / "feature_report.md"
    path.write_text(text)
