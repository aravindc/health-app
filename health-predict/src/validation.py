"""Data-quality metrics for CGM and insulin data.

Functions take clean tables (DataFrames) and return plain dicts / DataFrames
so they can be tested and reused by the report and notebooks.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src import preprocessing as pp

RANGE_ORDER = ["very_low", "low", "target", "high", "very_high"]


def classify_ranges(glucose: pd.Series, thresholds: dict) -> pd.Series:
    """Label each value: very_low (<very_low), low (<low), target, high (>high), very_high (>very_high)."""
    g = glucose.to_numpy()
    labels = np.select(
        [g < thresholds["very_low"], g < thresholds["low"], g > thresholds["very_high"], g > thresholds["high"]],
        ["very_low", "low", "very_high", "high"], default="target")
    return pd.Series(labels, index=glucose.index)


def time_in_ranges(glucose: pd.Series, thresholds: dict) -> dict[str, float]:
    """Percentage of readings in each range (reading-weighted, the standard CGM convention)."""
    if glucose.empty:
        return {k: float("nan") for k in RANGE_ORDER}
    counts = classify_ranges(glucose, thresholds).value_counts(normalize=True) * 100
    return {k: float(counts.get(k, 0.0)) for k in RANGE_ORDER}


def gaps(times: pd.Series, gap_minutes: float) -> pd.DataFrame:
    """Intervals between consecutive readings longer than gap_minutes."""
    t = times.sort_values().reset_index(drop=True)
    dt = t.diff()
    mask = dt > pd.Timedelta(minutes=gap_minutes)
    return pd.DataFrame({"gap_start": t.shift(1)[mask].to_numpy(), "gap_end": t[mask].to_numpy(),
                         "minutes": (dt[mask].dt.total_seconds() / 60).to_numpy()})


def cgm_metrics(readings: pd.DataFrame, settings: dict, start: pd.Timestamp | None = None,
                end: pd.Timestamp | None = None) -> dict:
    """Summary metrics for usable readings in [start, end).

    `readings` needs measured_at, glucose_mmol, censored.
    """
    cfg, thr = settings["cgm"], settings["glucose_thresholds_mmol"]
    r = readings
    if start is not None:
        r = r[r["measured_at"] >= start]
    if end is not None:
        r = r[r["measured_at"] < end]
    r = r.sort_values("measured_at")
    if r.empty:
        return {"n_readings": 0}
    step = cfg["expected_interval_minutes"]
    t0 = start if start is not None else r["measured_at"].iloc[0]
    t1 = end if end is not None else r["measured_at"].iloc[-1]
    expected = int((t1 - t0).total_seconds() // (step * 60))
    slots = r["measured_at"].dt.floor(f"{step}min").nunique()
    dt = r["measured_at"].diff().dt.total_seconds().div(60).dropna()
    g = gaps(r["measured_at"], cfg["gap_minutes"])
    long_g = g[g["minutes"] > cfg["long_gap_minutes"]]
    glucose = r["glucose_mmol"]
    mean = float(glucose.mean())
    std = float(glucose.std())
    tir = time_in_ranges(glucose, thr)
    return {
        "n_readings": len(r),
        "first": r["measured_at"].iloc[0].isoformat(),
        "last": r["measured_at"].iloc[-1].isoformat(),
        "days": round((t1 - t0).total_seconds() / 86400, 1),
        "interval_median_min": float(dt.median()),
        "interval_pct_regular": float(((dt >= step * 0.8) & (dt <= step * 1.2)).mean() * 100),
        "interval_pct_short": float((dt < step * 0.8).mean() * 100),
        "expected_slots": expected,
        "filled_slots": int(slots),
        "coverage_pct": float(slots / expected * 100) if expected else float("nan"),
        "missing_slots": int(max(expected - slots, 0)),
        "n_gaps": len(g),
        "n_long_gaps": len(long_g),
        "longest_gap_hours": float(g["minutes"].max() / 60) if len(g) else 0.0,
        "gap_hours_total": float(g["minutes"].sum() / 60),
        "censored_low": int(r["censored"].eq("low").sum()),
        "censored_high": int(r["censored"].eq("high").sum()),
        "min_mmol": float(glucose.min()),
        "max_mmol": float(glucose.max()),
        "mean_mmol": mean,
        "median_mmol": float(glucose.median()),
        "std_mmol": std,
        "cv_pct": std / mean * 100 if mean else float("nan"),
        "gmi_pct": 3.31 + 0.02392 * mean * 18.016,  # glucose management indicator (Bergenstal 2018)
        **{f"pct_{k}": v for k, v in tir.items()},
        "pct_below_range": tir["very_low"] + tir["low"],
        "pct_above_range": tir["high"] + tir["very_high"],
    }


def daily_coverage(readings: pd.DataFrame, settings: dict, tz: str) -> pd.DataFrame:
    """Per local date: readings, % of expected 5-min slots filled, mean glucose."""
    step = settings["cgm"]["expected_interval_minutes"]
    r = readings.assign(date=readings["measured_at"].dt.tz_convert(tz).dt.date,
                        slot=readings["measured_at"].dt.floor(f"{step}min"))
    d = r.groupby("date").agg(n=("glucose_mmol", "size"), slots=("slot", "nunique"),
                              mean=("glucose_mmol", "mean"), median=("glucose_mmol", "median"),
                              p05=("glucose_mmol", lambda x: x.quantile(0.05)),
                              p25=("glucose_mmol", lambda x: x.quantile(0.25)),
                              p75=("glucose_mmol", lambda x: x.quantile(0.75)),
                              p95=("glucose_mmol", lambda x: x.quantile(0.95)))
    d["coverage_pct"] = d["slots"] / (24 * 60 / step) * 100
    return d


def hourly_insulin(deliveries: pd.DataFrame, tz: str) -> pd.DataFrame:
    """Insulin per local hour and delivery type (intervals split proportionally)."""
    b = pp.spread_to_bins(deliveries, "start_at", "end_at", "units", "1h")
    local = b["bin_start"].dt.tz_convert(tz)
    return pd.DataFrame({"bin_start": b["bin_start"], "date": local.dt.date, "hour": local.dt.hour,
                         "weekday": local.dt.dayofweek, "delivery_type": b["delivery_type"],
                         "units": b["units"]})


def basal_unknown_hours(segments: pd.DataFrame, tz: str) -> pd.Series:
    """Hours per local date where basal delivery is unknown (basal gaps)."""
    gapseg = segments[segments["is_gap"]].assign(hours=lambda s: s["duration_minutes"] / 60)
    if gapseg.empty:
        return pd.Series(dtype=float)
    b = pp.spread_to_bins(gapseg, "start_at", "end_at", "hours", "1h")
    return b.groupby(b["bin_start"].dt.tz_convert(tz).dt.date)["hours"].sum()


def insulin_daily(hourly: pd.DataFrame, segments: pd.DataFrame, tz: str, start, end) -> pd.DataFrame:
    """Daily insulin by delivery type, for local dates in [start, end]."""
    d = hourly.pivot_table(index="date", columns="delivery_type", values="units", aggfunc="sum", fill_value=0.0)
    dates = pd.date_range(start, end, freq="D").date
    d = d.reindex(dates, fill_value=0.0)
    d.index.name = "date"
    for col in ("basal", "bolus", "automated_correction", "manual_correction"):
        if col not in d:
            d[col] = 0.0
    d["bolus_total"] = d[[c for c in d.columns if c != "basal"]].sum(axis=1)
    d["total"] = d["basal"] + d["bolus_total"]
    d["basal_unknown_hours"] = basal_unknown_hours(segments, tz).reindex(d.index).fillna(0.0)
    d["complete"] = d["basal_unknown_hours"] == 0
    return d


def insulin_metrics(daily: pd.DataFrame, hourly: pd.DataFrame, boluses: pd.DataFrame, settings: dict) -> dict:
    complete = daily[daily["complete"]]
    by_type = {k: float(daily[k].sum()) for k in ("basal", "bolus", "automated_correction", "manual_correction")}
    return {
        "days": len(daily),
        "complete_days": len(complete),
        "days_with_basal_gaps": int((~daily["complete"]).sum()),
        "days_without_bolus": int((daily["bolus_total"] == 0).sum()),
        "total_units": float(daily["total"].sum()),
        "basal_units": by_type["basal"],
        "bolus_units_all": float(daily["bolus_total"].sum()),
        **{f"units_{k}": v for k, v in by_type.items()},
        "avg_daily_total_complete_days": float(complete["total"].mean()),
        "median_daily_total_complete_days": float(complete["total"].median()),
        "avg_daily_basal_complete_days": float(complete["basal"].mean()),
        "avg_daily_bolus_complete_days": float(complete["bolus_total"].mean()),
        "basal_pct_complete_days": float(complete["basal"].sum() / complete["total"].sum() * 100),
        "days_above_suspicious_total": int((daily["total"] > settings["insulin"]["suspicious_daily_total_units"]).sum()),
        "n_boluses": len(boluses),
        "boluses_by_type": boluses["delivery_type"].value_counts().to_dict(),
        "boluses_per_day": float(len(boluses) / max(len(daily), 1)),
    }


def insulin_by_hour(hourly: pd.DataFrame, complete_dates) -> pd.DataFrame:
    """Mean units per local hour of day, by delivery type, over complete days."""
    h = hourly[hourly["date"].isin(set(complete_dates))]
    n_days = max(len(set(complete_dates)), 1)
    return h.pivot_table(index="hour", columns="delivery_type", values="units", aggfunc="sum",
                         fill_value=0.0).reindex(range(24), fill_value=0.0) / n_days


def insulin_by_weekday(daily: pd.DataFrame) -> pd.DataFrame:
    c = daily[daily["complete"]].copy()
    c["weekday"] = pd.to_datetime(pd.Series(c.index, index=c.index)).dt.dayofweek
    return c.groupby("weekday")[["basal", "bolus_total", "total"]].mean()
