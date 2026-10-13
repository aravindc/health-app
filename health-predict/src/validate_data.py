"""Validate the ingested data and write the data-quality report.

    python -m src.validate_data

Writes reports/data_quality_report.md, reports/data_quality_metrics.json and
figures in reports/figures/.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pandas as pd

from src import DISCLAIMER, config, db, plots
from src import validation as v

log = logging.getLogger("validate")

ISSUE_EXPLANATIONS = {
    "duplicate_reading": "Same sensor timestamp recorded more than once; the live reading (else the latest) is kept.",
    "backfill_time_corrected": "Backfilled reading logged at arrival time; measurement time rebuilt from the pump "
                               "clock offset of the nearest live reading.",
    "backfill_time_uncorrected": "Backfilled reading with no live reading within 2 h; arrival time kept as "
                                 "measurement time.",
    "censored_low": "Sensor reported LOW; stored at the low limit and flagged `censored='low'`.",
    "censored_high": "Sensor reported HIGH; stored at the high limit and flagged `censored='high'`.",
    "impossible_value": "Value outside the physically possible range and not explained by a LOW/HIGH flag.",
    "suspicious_rate_of_change": "Change from the previous reading faster than the configured limit "
                                 "(likely sensor noise or compression); flagged, kept.",
    "conflicting_basal_entry": "Two basal changes at the same instant; the later sequence number wins.",
    "basal_gap": "No basal record for longer than the configured limit; delivery treated as unknown, not zero.",
    "suspicious_basal_rate": "Basal rate above the configured limit.",
    "unmapped_basal_change_type": "Basal changeType not in config/mappings.yaml.",
    "zero_delivery": "Bolus that delivered 0 U (usually cancelled immediately).",
    "incomplete_bolus": "Bolus cancelled or stopped early; actual delivered units are used.",
    "suspicious_bolus_size": "Single bolus above the configured limit.",
    "delivered_exceeds_requested": "Delivered more than requested.",
    "unmapped_bolus_type": "Bolus type/options not matched by config/mappings.yaml.",
    "missing_completion_time": "Bolus without a completion time.",
    "repeated_carb_entry": "Same carbs re-entered within 30 min (bolus cancelled and entered again); the meal is "
                           "counted once.",
}


def md_table(df: pd.DataFrame, floatfmt: str = "{:.2f}") -> str:
    def fmt(x):
        if isinstance(x, float):
            return "" if pd.isna(x) else floatfmt.format(x)
        return str(x)
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    lines += ["| " + " | ".join(fmt(x) for x in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def metrics_table(metrics: dict[str, dict], rows: list[tuple[str, str, str]]) -> str:
    out = []
    for label, key, fmt in rows:
        out.append([label] + [("—" if key not in m or pd.isna(m[key]) else fmt.format(m[key]))
                              for m in metrics.values()])
    return md_table(pd.DataFrame(out, columns=["metric", *metrics.keys()]))


def pick_example_days(daily_cov: pd.DataFrame, daily_ins: pd.DataFrame, boluses: pd.DataFrame, tz: str) -> list:
    nb = boluses.groupby(boluses["requested_at"].dt.tz_convert(tz).dt.date).size()
    cand = daily_cov.join(daily_ins[["complete"]], how="inner").join(nb.rename("n_bolus"))
    cand = cand[(cand["coverage_pct"] >= 97) & cand["complete"] & (cand["n_bolus"] >= 3)]
    if cand.empty:
        return []
    idx = sorted(cand.index)
    return sorted({idx[0], idx[len(idx) // 2], idx[-1]})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    s = config.settings()
    tz = s["project"]["timezone"]
    thr = s["glucose_thresholds_mmol"]
    win = s["modelling_window"]
    w_start = pd.Timestamp(win["start"], tz=tz).tz_convert("UTC")
    w_end = (pd.Timestamp(win["end"], tz=tz) + pd.Timedelta(days=1)).tz_convert("UTC")
    out_dir = config.path(s["report"]["output_dir"])
    fig_dir = config.path(s["report"]["figures_dir"])
    fig_dir.mkdir(parents=True, exist_ok=True)

    with db.duck(read_only=True) as d:
        q = lambda sql: d.execute(sql).df()
        readings = q("SELECT * FROM cgm_readings WHERE exclude_reason IS NULL")
        merged = q("SELECT * FROM cgm_merged")
        boluses = q("SELECT * FROM boluses")
        segments = q("SELECT * FROM basal_segments")
        deliveries = q("SELECT * FROM insulin_deliveries")
        issues = q("SELECT source_table, issue_type, severity, count(*) AS n FROM dq_issues GROUP BY ALL "
                   "ORDER BY source_table, n DESC")
        raw_counts = q("""SELECT 'tandem_cgm' t, count(*) n, min(reading_at) AS "first", max(reading_at) AS "last" FROM raw_tandem_cgm
            UNION ALL SELECT 'tandem_basal', count(*), min(changed_at), max(changed_at) FROM raw_tandem_basal
            UNION ALL SELECT 'tandem_bolus', count(*), min(requested_at), max(requested_at) FROM raw_tandem_bolus
            UNION ALL SELECT 'nightscoutdb', count(*), min(ns_datetime), max(ns_datetime) FROM raw_nightscout""")
        last_ingest = d.execute("SELECT max(finished_at) FROM ingest_runs").fetchone()[0]
    for df, cols in ((readings, ["measured_at", "available_at"]), (merged, ["measured_at", "available_at"]),
                     (boluses, ["requested_at", "completed_at"]), (segments, ["start_at", "end_at"]),
                     (deliveries, ["start_at", "end_at", "available_at"])):
        for c in cols:
            df[c] = pd.to_datetime(df[c], utc=True)

    tandem = readings[readings["source"] == "tandem"]
    ns = readings[readings["source"] == "nightscout"]
    in_win = lambda df: df[(df["measured_at"] >= w_start) & (df["measured_at"] < w_end)]

    # ------------------------------------------------------------------ CGM
    cgm = {
        "Primary (window)": v.cgm_metrics(merged, s, w_start, w_end),
        "Tandem only (window)": v.cgm_metrics(tandem, s, w_start, w_end),
        "Nightscout (all history)": v.cgm_metrics(ns, s),
    }
    merged_win = in_win(merged)
    tir = {k: cgm["Primary (window)"][f"pct_{k}"] for k in v.RANGE_ORDER}
    daily_cov = v.daily_coverage(merged, s, tz)
    daily_cov_tandem = v.daily_coverage(tandem, s, tz)
    gaps_win = v.gaps(merged_win["measured_at"], s["cgm"]["gap_minutes"]).sort_values("minutes", ascending=False)

    # -------------------------------------------------------------- insulin
    hourly = v.hourly_insulin(deliveries, tz)
    daily_ins = v.insulin_daily(hourly, segments, tz, win["start"], win["end"])
    ins = v.insulin_metrics(daily_ins, hourly, boluses[(boluses["requested_at"] >= w_start)
                                                       & (boluses["requested_at"] < w_end)], s)
    by_hour = v.insulin_by_hour(hourly, daily_ins.index[daily_ins["complete"]])
    by_weekday = v.insulin_by_weekday(daily_ins)
    basal_gaps = segments[segments["is_gap"]].sort_values("duration_minutes", ascending=False)

    # -------------------------------------------------------------- figures
    figs = {
        "cgm_overview": plots.cgm_overview(daily_cov, daily_cov_tandem, thr, (win["start"], win["end"]),
                                           fig_dir / "cgm_overview.png"),
        "agp": plots.agp(merged_win, tz, thr, fig_dir / "agp.png"),
        "distribution": plots.glucose_distribution(merged_win, tir, thr, fig_dir / "glucose_distribution.png",
                                                    s["units"]["mgdl_per_mmol"]),
        "insulin_daily": plots.insulin_daily(daily_ins, fig_dir / "insulin_daily.png"),
        "insulin_patterns": plots.insulin_patterns(by_hour, by_weekday, fig_dir / "insulin_patterns.png"),
    }
    examples = pick_example_days(daily_cov[daily_cov.index >= pd.Timestamp(win["start"]).date()], daily_ins,
                                 boluses, tz)
    for day in examples:
        start = pd.Timestamp(day, tz=tz).tz_convert("UTC")
        figs[f"day_{day}"] = plots.aligned_day(merged, segments, boluses, start, start + pd.Timedelta(days=1), tz,
                                               thr, fig_dir / f"aligned_{day}.png")

    # --------------------------------------------------------------- output
    metrics = {"generated_at": datetime.now(UTC).isoformat(), "cgm": cgm, "insulin": ins,
               "dq_issues": issues.to_dict(orient="records")}
    (out_dir / "data_quality_metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    report = render(s, raw_counts, last_ingest, cgm, ins, daily_ins, by_weekday, issues, gaps_win, basal_gaps, figs,
                    tz)
    (out_dir / "data_quality_report.md").write_text(report)
    log.info("wrote %s and %d figures", out_dir / "data_quality_report.md", len(figs))


def render(s, raw_counts, last_ingest, cgm, ins, daily_ins, by_weekday, issues, gaps_win, basal_gaps, figs, tz) -> str:
    thr = s["glucose_thresholds_mmol"]
    win = s["modelling_window"]
    rel = lambda p: p.relative_to(config.path(s["report"]["output_dir"])).as_posix()
    pct = "{:.1f}%"
    cgm_rows = [
        ("readings", "n_readings", "{:,}"), ("first (UTC)", "first", "{:.16}"), ("last (UTC)", "last", "{:.16}"),
        ("days covered", "days", "{:.1f}"),
        ("median sampling interval (min)", "interval_median_min", "{:.1f}"),
        ("intervals within ±20% of 5 min", "interval_pct_regular", pct),
        ("intervals shorter than expected", "interval_pct_short", pct),
        ("5-min slots expected", "expected_slots", "{:,}"), ("5-min slots with a reading", "filled_slots", "{:,}"),
        ("coverage", "coverage_pct", pct), ("missing 5-min slots", "missing_slots", "{:,}"),
        (f"gaps > {s['cgm']['gap_minutes']} min", "n_gaps", "{:,}"),
        (f"gaps > {s['cgm']['long_gap_minutes']} min", "n_long_gaps", "{:,}"),
        ("longest gap (h)", "longest_gap_hours", "{:.1f}"), ("total gap time (h)", "gap_hours_total", "{:.0f}"),
        ("sensor LOW readings", "censored_low", "{:,}"), ("sensor HIGH readings", "censored_high", "{:,}"),
        ("min (mmol/L)", "min_mmol", "{:.1f}"), ("max (mmol/L)", "max_mmol", "{:.1f}"),
        ("mean (mmol/L)", "mean_mmol", "{:.2f}"), ("median (mmol/L)", "median_mmol", "{:.2f}"),
        ("standard deviation (mmol/L)", "std_mmol", "{:.2f}"), ("coefficient of variation", "cv_pct", pct),
        ("GMI (estimated HbA1c %)", "gmi_pct", "{:.1f}"),
        (f"very low (< {thr['very_low']})", "pct_very_low", pct),
        (f"low ({thr['very_low']}–{thr['low']})", "pct_low", pct),
        (f"**in range ({thr['low']}–{thr['high']})**", "pct_target", pct),
        (f"high ({thr['high']}–{thr['very_high']})", "pct_high", pct),
        (f"very high (> {thr['very_high']})", "pct_very_high", pct),
    ]
    ins_rows = [
        ("days in window", ins["days"]), ("complete days (no basal gap)", ins["complete_days"]),
        ("days with basal gaps", ins["days_with_basal_gaps"]), ("days without any bolus", ins["days_without_bolus"]),
        ("total insulin (U)", f"{ins['total_units']:,.0f}"), ("basal (U)", f"{ins['basal_units']:,.0f}"),
        ("boluses, all types (U)", f"{ins['bolus_units_all']:,.0f}"),
        ("— manual bolus (U)", f"{ins['units_bolus']:,.0f}"),
        ("— automated correction (U)", f"{ins['units_automated_correction']:,.0f}"),
        ("— manual correction (U)", f"{ins['units_manual_correction']:,.0f}"),
        ("average daily total, complete days (U)", f"{ins['avg_daily_total_complete_days']:.1f}"),
        ("median daily total, complete days (U)", f"{ins['median_daily_total_complete_days']:.1f}"),
        ("average daily basal / bolus (U)",
         f"{ins['avg_daily_basal_complete_days']:.1f} / {ins['avg_daily_bolus_complete_days']:.1f}"),
        ("basal share of total", f"{ins['basal_pct_complete_days']:.0f}%"),
        ("boluses", f"{ins['n_boluses']:,} ({ins['boluses_per_day']:.1f}/day)"),
        ("boluses by type", ", ".join(f"{k}: {n}" for k, n in ins["boluses_by_type"].items())),
        (f"days above {s['insulin']['suspicious_daily_total_units']} U", ins["days_above_suspicious_total"]),
    ]
    issues = issues.assign(meaning=issues["issue_type"].map(ISSUE_EXPLANATIONS).fillna(""))
    monthly = daily_ins.groupby(pd.to_datetime(pd.Series(daily_ins.index, index=daily_ins.index)).dt.to_period("M")) \
        .agg(days=("total", "size"), complete=("complete", "sum"), avg_total=("total", "mean"),
             avg_basal=("basal", "mean"), avg_bolus=("bolus_total", "mean")).reset_index(names="month")
    monthly["month"] = monthly["month"].astype(str)
    weekday = by_weekday.reset_index()
    weekday["weekday"] = weekday["weekday"].map(dict(enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])))
    fmt_t = lambda x: pd.Timestamp(x).tz_convert(tz).strftime("%Y-%m-%d %H:%M")
    top_gaps = gaps_win.head(10).assign(gap_start=lambda g: g["gap_start"].map(fmt_t),
                                        gap_end=lambda g: g["gap_end"].map(fmt_t),
                                        hours=lambda g: g["minutes"] / 60)[["gap_start", "gap_end", "hours"]]
    top_basal = basal_gaps.head(10).assign(start=lambda g: g["start_at"].map(fmt_t),
                                           end=lambda g: g["end_at"].map(fmt_t),
                                           hours=lambda g: g["duration_minutes"] / 60)[["start", "end", "hours"]]
    raw_counts = raw_counts.assign(first=raw_counts["first"].map(fmt_t), last=raw_counts["last"].map(fmt_t))
    day_figs = [k for k in figs if k.startswith("day_")]

    return f"""# Data quality report

> **{DISCLAIMER}**

Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from data ingested {fmt_t(last_ingest)} ({tz}).
Modelling window: **{win['start']} → {win['end']}** (local dates; the period with both CGM and insulin data).
Thresholds and limits come from `config/settings.yaml`; code mappings from `config/mappings.yaml`.

## 1. Sources

Raw rows are copied unchanged into `raw_*` tables in DuckDB and exported to `data/raw/*.parquet`.

{md_table(raw_counts.rename(columns={'t': 'source table', 'n': 'rows'}))}

`ns_part` (an exact copy of `nightscoutdb`) and the empty tables `insulin`, `mysugr`, `sugarmate` are not ingested.

## 2. CGM

The **primary series** is Tandem CGM, with Nightscout readings filling times that have no Tandem reading within
{s['cgm']['nightscout_fill_tolerance_seconds']} s. Glucose is converted from mg/dL at
{s['units']['mgdl_per_mmol']} mg/dL per mmol/L. Time in ranges is reading-weighted.

{metrics_table(cgm, cgm_rows)}

![CGM overview]({rel(figs['cgm_overview'])})

![AGP]({rel(figs['agp'])})

![Distribution]({rel(figs['distribution'])})

### Largest CGM gaps in the modelling window (primary series)

{md_table(top_gaps, '{:.1f}')}

## 3. Insulin

All insulin is rapid-acting, delivered by the pump. Basal units are the commanded rate integrated over time until
the next rate change. Days with a basal gap (no record for more than {s['insulin']['max_basal_gap_hours']} h)
understate the true total and are excluded from averages.

{md_table(pd.DataFrame(ins_rows, columns=['metric', 'value']))}

![Daily insulin]({rel(figs['insulin_daily'])})

![Insulin patterns]({rel(figs['insulin_patterns'])})

### By month (averages over all days in the month, U/day)

{md_table(monthly, '{:.1f}')}

### By weekday (complete days, U/day)

{md_table(weekday, '{:.1f}')}

### Largest basal gaps

{md_table(top_basal, '{:.1f}')}

## 4. Aligned CGM and insulin examples

Days chosen automatically: ≥ 97% CGM coverage, no basal gap, at least 3 boluses (first, middle and last such day).

{chr(10).join(f'![{k}]({rel(figs[k])})' for k in day_figs)}

## 5. Data-quality issues

Every flag is stored in the `dq_issues` table with the source row id. `excluded` rows stay in the clean tables with
an `exclude_reason`; nothing is deleted.

{md_table(issues[['source_table', 'issue_type', 'severity', 'n', 'meaning']])}

## 6. Assumptions and handling decisions

1. **Timestamps** are stored in UTC. Local time ({tz}) is used only for time-of-day analysis and daily totals.
2. **Backfilled Tandem readings** (`cgmDataType` contains 1) are logged at arrival time. Measurement time is
   rebuilt from `egvTimeStamp` plus the pump-clock offset of the nearest live reading (validated against
   Nightscout: exact value matches rose from 6% to 63%). Arrival time is kept as `available_at`; features must use
   `available_at <= T` so that late-arriving backfills cannot leak into earlier predictions.
3. **Duplicates** share `egvTimeStamp`; the live reading is kept over a backfill, then the latest sequence number.
4. **LOW/HIGH sensor readings** are kept at the sensor limits ({s['cgm']['sensor_low_limit_mgdl']} /
   {s['cgm']['sensor_high_limit_mgdl']} mg/dL) and flagged as censored; their true value is beyond the limit.
5. **Nightscout** readings are assumed to have been uploaded live (`available_at = measured_at`).
6. **Trend codes** are mapped to an ordinal direction (+3 … −3), verified against observed rates of change.
   Tandem's thresholds are tighter than standard Dexcom arrows.
7. **Basal** rates apply until the next change. Segments longer than {s['insulin']['max_basal_gap_hours']} h are
   gaps with unknown delivery (not zero). The pump's `maxBasalRate` is not a hard cap and is not used as a check.
8. **Boluses** are classified per `config/mappings.yaml`. JSON `bolusType` 3 / `options` 4 is a manual bolus when
   it has carbs and a *manual correction* when it does not (user decision). Delivered units are used, so cancelled
   boluses count only what was given.
9. **Extended boluses** deliver `standardPercent` of the requested dose at once; the remainder is spread evenly
   until completion.
10. **Standard boluses** are placed at `completed_at` (≤ 6 min after the request) so they are only used once known.
11. **Carbs** come from bolus entries (`carb_amount`) and are timestamped at the bolus request.
12. **Basal gaps while CGM continues** mean insulin was given in a way that is not recorded (e.g. 25–28 Oct 2025:
    full CGM, no pump boluses or basal — most likely pump off and injections). Injections are not in the data.
    These periods must be treated as *insulin unknown*, never as *no insulin*, and excluded from training of any
    model that uses insulin features.
"""


if __name__ == "__main__":
    main()
