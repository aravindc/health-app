"""Error analysis by condition, and low/high-glucose event classification.

Segments use information known at prediction time (current glucose, recent
rate, insulin, time) so they describe situations the model is in, not outcomes.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd

from src.intervals import direction, glucose_range
from src.metrics import regression_metrics

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def segments(feats: pd.DataFrame, cfg: dict, tz: str, high_insulin: float, recent_gap: pd.Series) -> pd.DataFrame:
    """One column per segmentation, indexed like feats."""
    c = cfg["categories"]
    local = feats.index.tz_convert(tz)
    hour = local.hour
    pb = c["post_bolus_bins_minutes"]
    msb = feats["minutes_since_last_bolus"]
    post = pd.cut(msb, pb, right=False, labels=[f"{a}–{b} min" for a, b in pairwise(pb)]).astype(str)
    oh = c["overnight_hours"]
    return pd.DataFrame({
        "glucose range (current)": glucose_range(feats["current_glucose"], c["glucose_ranges_mmol"]),
        "direction": direction(feats[c["rate_feature"]], c["stable_rate_mmol_per_min"], c["rapid_rate_mmol_per_min"]),
        "time of day": pd.cut(hour, [0, 6, 12, 18, 24], right=False, labels=["00–06", "06–12", "12–18", "18–24"]).astype(str),
        "overnight": np.where((hour >= oh[0]) & (hour < oh[1]), "overnight 00–06", "daytime"),
        "recent insulin": np.where(feats["insulin_last_60m"] > high_insulin, "high (last 60 min)", "low (last 60 min)"),
        "recent bolus": np.where(msb <= c["recent_bolus_minutes"], f"bolus in last {c['recent_bolus_minutes']} min",
                                 f"no bolus in last {c['recent_bolus_minutes']} min"),
        "time since bolus": np.where(post == "nan", f"≥ {pb[-1]} min", post),
        "day of week": [WEEKDAYS[d] for d in local.dayofweek],
        "CGM data": recent_gap.reindex(feats.index).fillna("unknown").to_numpy(),
    }, index=feats.index)


def segment_table(preds: pd.DataFrame, segs: pd.DataFrame, horizon: int, model: str, baseline: str) -> pd.DataFrame:
    p = preds[preds["horizon"] == horizon].set_index(["model", "ts"])
    m, b = p.loc[model], p.loc[baseline].reindex(p.loc[model].index)
    rows = []
    for col in segs.columns:
        s = segs[col].reindex(m.index)
        for val, idx in s.groupby(s).groups.items():
            mm = regression_metrics(m.loc[idx, "y_true"], m.loc[idx, "y_pred"])
            bb = regression_metrics(b.loc[idx, "y_true"], b.loc[idx, "y_pred"])
            rows.append({"segmentation": col, "segment": val, "n": mm["n"], "MAE": mm["mae"],
                         "persistence MAE": bb["mae"], "improvement": 1 - mm["mae"] / bb["mae"] if bb["mae"] else np.nan,
                         "bias": mm["bias"], "P95 AE": mm["p95_ae"]})
    return pd.DataFrame(rows)


def binary_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    a, p = np.asarray(actual, bool), np.asarray(predicted, bool)
    tp, fp, fn, tn = (a & p).sum(), (~a & p).sum(), (a & ~p).sum(), (~a & ~p).sum()
    sens = tp / (tp + fn) if tp + fn else np.nan
    spec = tn / (tn + fp) if tn + fp else np.nan
    prec = tp / (tp + fp) if tp + fp else np.nan
    f1 = 2 * prec * sens / (prec + sens) if prec + sens else np.nan
    return {"events": int(a.sum()), "flagged": int(p.sum()), "sensitivity (recall)": sens, "specificity": spec,
            "precision": prec, "F1": f1, "false-negative rate": 1 - sens if not np.isnan(sens) else np.nan}


def threshold_classification(preds: pd.DataFrame, model: str, thresholds: dict) -> pd.DataFrame:
    """Per horizon: does the prediction put future glucose on the same side of each threshold as reality?"""
    rows = []
    rules = [("hypo", "< " + str(thresholds["low"]), lambda x: x < thresholds["low"]),
             ("hyper", "> " + str(thresholds["high"]), lambda x: x > thresholds["high"]),
             ("severe hyper", "> " + str(thresholds["very_high"]), lambda x: x > thresholds["very_high"])]
    for h, g in preds[preds["model"] == model].groupby("horizon"):
        for name, label, f in rules:
            rows.append({"event": f"{name} ({label})", "horizon": f"{h} min",
                         **binary_metrics(f(g["y_true"].to_numpy()), f(g["y_pred"].to_numpy()))})
    return pd.DataFrame(rows)


def becoming_low(preds: pd.DataFrame, model: str, low: float, lower_bounds: pd.DataFrame | None = None) -> pd.DataFrame:
    """'Will glucose become low within 30 minutes?' for times when glucose is currently not low.

    Event: an actual reading < low at +15 or +30 min. Alert: predicted < low at +15 or +30 min (point
    prediction), or optionally the interval's lower bound < low (a more cautious alert).
    """
    p = preds[(preds["model"] == model) & preds["horizon"].isin([15, 30])]
    w = p.pivot_table(index="ts", columns="horizon", values=["y_true", "y_pred", "current_glucose"])
    w = w.dropna()
    w = w[w[("current_glucose", 15)] >= low]
    actual = (w["y_true"] < low).any(axis=1)
    rows = [{"alert rule": "point prediction < " + str(low), **binary_metrics(actual, (w["y_pred"] < low).any(axis=1))}]
    if lower_bounds is not None:  # columns: ts, horizon, lower (one interval level)
        lb = lower_bounds.pivot_table(index="ts", columns="horizon", values="lower").reindex(w.index)
        rows.append({"alert rule": "interval lower bound < " + str(low),
                     **binary_metrics(actual, (lb[[15, 30]] < low).any(axis=1))})
    return pd.DataFrame(rows)
