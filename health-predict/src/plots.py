"""Figures for the data-quality report. All functions save a PNG and return its path."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.validation import RANGE_ORDER

COLORS = {
    "glucose": "#2b6cb0", "band": "#90cdf4", "target": "#c6f6d5",
    "basal": "#805ad5", "bolus": "#dd6b20", "automated_correction": "#d69e2e", "manual_correction": "#e53e3e",
    "carbs": "#38a169", "low": "#e53e3e", "high": "#d69e2e",
    "very_low": "#9b2c2c", "target_range": "#38a169", "very_high": "#975a16",
}
RANGE_COLORS = {"very_low": "#9b2c2c", "low": "#e53e3e", "target": "#38a169", "high": "#ecc94b",
                "very_high": "#d69e2e"}
INSULIN_TYPES = ["basal", "bolus", "automated_correction", "manual_correction"]
DISCLAIMER = "Research analysis only. Not a medical device; not for treatment decisions."


def _finish(fig, path: Path) -> Path:
    fig.text(0.99, 0.005, DISCLAIMER, ha="right", va="bottom", fontsize=7, color="#718096")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _target_band(ax, thr):
    ax.axhspan(thr["low"], thr["high"], color=COLORS["target"], alpha=0.5, lw=0, label="target range")
    for k in ("very_low", "low", "high", "very_high"):
        ax.axhline(thr[k], color="#a0aec0", lw=0.6, ls="--")


def cgm_overview(daily: pd.DataFrame, daily_tandem: pd.DataFrame, thr: dict, window: tuple, path: Path) -> Path:
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 6.5), sharex=True, height_ratios=[3, 1.2])
    # Reindex to every calendar day so missing days break the lines instead of being bridged.
    full = pd.date_range(min(daily.index), max(daily.index), freq="D").date
    daily = daily.reindex(full)
    daily_tandem = daily_tandem.reindex(pd.date_range(min(daily_tandem.index), max(daily_tandem.index), freq="D").date)
    x = pd.to_datetime(pd.Series(daily.index))
    _target_band(a1, thr)
    a1.fill_between(x, daily["p05"], daily["p95"], color=COLORS["band"], alpha=0.35, lw=0, label="5–95th pct")
    a1.fill_between(x, daily["p25"], daily["p75"], color=COLORS["band"], alpha=0.8, lw=0, label="25–75th pct")
    a1.plot(x, daily["median"], color=COLORS["glucose"], lw=0.8, label="daily median")
    a1.set_ylabel("glucose (mmol/L)")
    a1.set_title("CGM glucose by day (primary series: Tandem, gaps filled from Nightscout)")
    a1.legend(loc="upper left", fontsize=8, ncol=4)
    for a in (a1, a2):
        a.axvspan(pd.Timestamp(window[0]), pd.Timestamp(window[1]), color="#edf2f7", zorder=-1)
    a2.bar(x, daily["coverage_pct"], width=1, color="#a0aec0", label="primary series")
    xt = pd.to_datetime(pd.Series(daily_tandem.index))
    a2.plot(xt, daily_tandem["coverage_pct"], color=COLORS["glucose"], lw=0.6, label="Tandem only")
    a2.set_ylabel("coverage %")
    a2.set_ylim(0, 105)
    a2.legend(loc="lower left", fontsize=8, ncol=2)
    a1.text(pd.Timestamp(window[0]), a1.get_ylim()[1], " modelling window", fontsize=8, va="top", color="#4a5568")
    return _finish(fig, path)


def agp(readings: pd.DataFrame, tz: str, thr: dict, path: Path) -> Path:
    local = readings["measured_at"].dt.tz_convert(tz)
    tod = (local.dt.hour * 60 + local.dt.minute) // 15 * 15
    q = readings.groupby(tod)["glucose_mmol"].quantile([0.05, 0.25, 0.5, 0.75, 0.95]).unstack()
    x = q.index / 60
    fig, ax = plt.subplots(figsize=(11, 4.5))
    _target_band(ax, thr)
    ax.fill_between(x, q[0.05], q[0.95], color=COLORS["band"], alpha=0.35, lw=0, label="5–95th pct")
    ax.fill_between(x, q[0.25], q[0.75], color=COLORS["band"], alpha=0.8, lw=0, label="25–75th pct")
    ax.plot(x, q[0.5], color=COLORS["glucose"], lw=1.5, label="median")
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xlabel(f"local time of day ({tz})")
    ax.set_ylabel("glucose (mmol/L)")
    ax.set_title("Ambulatory glucose profile — modelling window")
    ax.legend(loc="upper left", fontsize=8, ncol=4)
    return _finish(fig, path)


def glucose_distribution(readings: pd.DataFrame, tir: dict, thr: dict, path: Path, mgdl_per_mmol: float) -> Path:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.2), width_ratios=[3, 1])
    # Sources store whole mg/dL values; bins must hold a whole number of them or the histogram aliases.
    bins = (np.arange(38, 406, 4) - 0.5) / mgdl_per_mmol
    a1.hist(readings["glucose_mmol"], bins=bins, color=COLORS["glucose"], alpha=0.85)
    for k in ("very_low", "low", "high", "very_high"):
        a1.axvline(thr[k], color="#4a5568", lw=0.8, ls="--")
        a1.text(thr[k], a1.get_ylim()[1] * 0.97, f" {thr[k]}", fontsize=8, va="top")
    a1.set_xlabel("glucose (mmol/L) — censored LOW/HIGH readings sit at the sensor limits")
    a1.set_ylabel("readings")
    a1.set_title("Glucose distribution — modelling window")
    bottom, mids = 0.0, []
    for k in RANGE_ORDER:
        a2.bar(0, tir[k], bottom=bottom, color=RANGE_COLORS[k], width=0.6)
        mids.append(bottom + tir[k] / 2)
        bottom += tir[k]
    # Labels to the right of the bar, pushed apart so small segments stay readable.
    ys = list(mids)
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + 9)
    for i in range(len(ys) - 2, -1, -1):
        ys[i] = min(ys[i], ys[i + 1] - 9)
    for k, mid, y in zip(RANGE_ORDER, mids, ys):
        a2.annotate(f"{k.replace('_', ' ')} {tir[k]:.1f}%", xy=(0.3, mid), xytext=(0.42, y), fontsize=8,
                    va="center", arrowprops={"arrowstyle": "-", "color": "#a0aec0", "lw": 0.6})
    a2.set_xlim(-0.4, 1.3)
    a2.set_ylim(0, 100)
    a2.set_xticks([])
    a2.set_ylabel("% of readings")
    a2.set_title("Time in ranges")
    return _finish(fig, path)


def insulin_daily(daily: pd.DataFrame, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(13, 4.5))
    x = pd.to_datetime(pd.Series(daily.index))
    bottom = np.zeros(len(daily))
    for k in INSULIN_TYPES:
        ax.bar(x, daily[k], bottom=bottom, width=1, color=COLORS[k], label=k.replace("_", " "), lw=0)
        bottom += daily[k].to_numpy()
    roll = daily["total"].where(daily["complete"]).rolling(7, min_periods=4).mean()
    ax.plot(x, roll, color="black", lw=1.2, label="7-day mean total (complete days)")
    inc = x[~daily["complete"].to_numpy()]
    for d in inc:
        ax.axvspan(d - pd.Timedelta(hours=12), d + pd.Timedelta(hours=12), color="#fed7d7", alpha=0.6, lw=0,
                   zorder=-1)
    ax.set_ylabel("units / day")
    ax.set_title("Daily insulin by delivery type (pink = day with a basal gap, totals understated)")
    ax.legend(loc="upper left", fontsize=8, ncol=5)
    return _finish(fig, path)


def insulin_patterns(by_hour: pd.DataFrame, by_weekday: pd.DataFrame, path: Path) -> Path:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.2), width_ratios=[2.2, 1])
    bottom = np.zeros(24)
    for k in INSULIN_TYPES:
        if k in by_hour:
            a1.bar(by_hour.index, by_hour[k], bottom=bottom, color=COLORS[k], label=k.replace("_", " "))
            bottom += by_hour[k].to_numpy()
    a1.set_xticks(range(0, 24, 2))
    a1.set_xlabel("local hour of day")
    a1.set_ylabel("mean units per hour")
    a1.set_title("Insulin by hour of day (complete days)")
    a1.legend(fontsize=8)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    a2.bar(names, by_weekday["basal"].reindex(range(7)), color=COLORS["basal"], label="basal")
    a2.bar(names, by_weekday["bolus_total"].reindex(range(7)), bottom=by_weekday["basal"].reindex(range(7)),
           color=COLORS["bolus"], label="all boluses")
    a2.set_ylabel("mean units / day")
    a2.set_ylim(0, by_weekday["total"].max() * 1.3)
    a2.set_title("By weekday")
    a2.legend(fontsize=8, ncol=2, loc="upper center")
    return _finish(fig, path)


def aligned_day(cgm: pd.DataFrame, segments: pd.DataFrame, boluses: pd.DataFrame, start: pd.Timestamp,
                end: pd.Timestamp, tz: str, thr: dict, path: Path) -> Path:
    """CGM, basal rate and boluses/carbs on one aligned timeline."""
    c = cgm[(cgm["measured_at"] >= start) & (cgm["measured_at"] < end)]
    s = segments[(segments["start_at"] < end) & (segments["end_at"].fillna(end) > start)]
    b = boluses[(boluses["requested_at"] >= start) & (boluses["requested_at"] < end)]
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(13, 7.5), sharex=True, height_ratios=[3, 1.2, 1.4])
    _target_band(a1, thr)
    for src, mk in (("tandem", "o"), ("nightscout", "s")):
        cc = c[c["source"] == src]
        if len(cc):
            a1.plot(cc["measured_at"], cc["glucose_mmol"], mk, ms=2.5, color=COLORS["glucose"] if src == "tandem"
                    else "#4fd1c5", label=f"CGM ({src})")
    cens = c[c["censored"].notna()]
    if len(cens):
        a1.plot(cens["measured_at"], cens["glucose_mmol"], "x", color="red", label="sensor LOW/HIGH")
    a1.set_ylabel("glucose (mmol/L)")
    a1.legend(loc="upper left", fontsize=8, ncol=4)
    a1.set_title(f"CGM and insulin, {start.tz_convert(tz):%a %d %b %Y} (local time)")
    if len(s):
        st = s["start_at"].clip(lower=start)
        a2.step(st, s["rate_u_per_h"], where="post", color=COLORS["basal"], label="commanded basal")
        a2.step(st, s["base_rate_u_per_h"], where="post", color="#a0aec0", ls="--", label="profile basal")
    a2.set_ylabel("U/h")
    a2.legend(loc="upper left", fontsize=8, ncol=2)
    for k in ("bolus", "automated_correction", "manual_correction"):
        bb = b[b["delivery_type"] == k]
        if len(bb):
            a3.stem(bb["requested_at"], bb["units_delivered"], linefmt=COLORS[k], markerfmt="o", basefmt=" ",
                    label=k.replace("_", " "))
    a3.set_ylabel("bolus (U)")
    a4 = a3.twinx()
    cb = b[b["carbs_g"] > 0]
    if len(cb):
        a4.bar(cb["requested_at"], cb["carbs_g"], width=pd.Timedelta(minutes=8), color=COLORS["carbs"], alpha=0.4,
               label="carbs (g)")
        a4.set_ylabel("carbs (g)")
    h1, l1 = a3.get_legend_handles_labels()
    h2, l2 = a4.get_legend_handles_labels()
    a3.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8, ncol=4)
    a3.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
    a3.set_xlim(start, end)
    return _finish(fig, path)
