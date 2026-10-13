"""Prediction targets.

For prediction time T and horizon h:
    glucose_{h}m = the CGM reading measured nearest to T + h (within the
                   configured tolerance), regardless of when it arrived — it is
                   the ground truth, never a model input.
    delta_{h}m   = glucose_{h}m − current_glucose (current_glucose is the
                   latest reading known at T, from features).
Targets are NaN when no reading is close enough to T + h.
"""

from __future__ import annotations

import pandas as pd


def make_targets(cgm: pd.DataFrame, current_glucose: pd.Series, settings: dict) -> pd.DataFrame:
    tcfg = settings["targets"]
    tol = pd.Timedelta(minutes=tcfg["tolerance_minutes"])
    readings = cgm[["measured_at", "glucose_mmol", "censored"]].dropna(subset=["glucose_mmol"]).copy()
    readings["measured_at"] = pd.to_datetime(readings["measured_at"], utc=True).dt.as_unit("ns")
    readings = readings.sort_values("measured_at")
    ts = pd.DatetimeIndex(current_glucose.index).as_unit("ns")
    out = pd.DataFrame({"current_glucose": current_glucose.to_numpy()}, index=pd.Index(ts, name="ts"))
    for h in tcfg["horizons_minutes"]:
        q = pd.DataFrame({"when": ts + pd.Timedelta(minutes=h)})
        m = pd.merge_asof(q, readings, left_on="when", right_on="measured_at", direction="nearest", tolerance=tol)
        out[f"glucose_{h}m"] = m["glucose_mmol"].to_numpy()
        out[f"delta_{h}m"] = out[f"glucose_{h}m"] - out["current_glucose"]
        out[f"target_{h}m_censored"] = m["censored"].to_numpy()
    return out
