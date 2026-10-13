"""Prediction from recent CGM and insulin history — shared by the API and `python -m src.predict`.

Requests are converted into the same tables the pipeline uses (CGM readings,
boluses, basal segments) and features are built with `features.build_features(...,
as_of=T)`, the exact code used for training. There is no separate serving
feature path that could drift from training.

This system predicts glucose values for research and analysis. It is not a
medical device and must not be used to determine insulin doses or make treatment
decisions. It makes no dosing, correction, basal or carbohydrate recommendations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

from src import DISCLAIMER, config, registry
from src import features as F
from src import intervals as iv

BOLUS_TYPES = ("bolus", "automated_correction", "manual_correction")


class InputError(ValueError):
    """The request cannot be predicted from (reported to the caller)."""


@dataclass
class History:
    """Recent data up to and including the prediction time T (all timestamps UTC)."""
    at: pd.Timestamp
    glucose: list[tuple[pd.Timestamp, float]]                       # (time, mmol/L)
    boluses: list[dict] = field(default_factory=list)               # time, units, type, duration_minutes, ...
    basal: list[tuple[pd.Timestamp, float, float | None]] = field(default_factory=list)  # (time, rate, profile)
    glucose_trend: dict[pd.Timestamp, float] = field(default_factory=dict)
    glucose_rate: dict[pd.Timestamp, float] = field(default_factory=dict)  # device rate, mmol/L/min


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    if t.tzinfo is None:
        raise InputError(f"timestamp {t} has no timezone; send ISO 8601 with an offset, e.g. 2026-10-07T09:00:00Z")
    return t.tz_convert("UTC").as_unit("ns")


def to_tables(h: History, settings: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cgm = pd.DataFrame([{"measured_at": t, "available_at": t, "glucose_mmol": float(g), "censored": None,
                         "trend_ordinal": h.glucose_trend.get(t, np.nan),
                         "rate_mmol_per_min": h.glucose_rate.get(t, np.nan),
                         "source": "request", "reading_id": f"r{i}"} for i, (t, g) in enumerate(h.glucose)])
    low = settings["cgm"]["sensor_low_limit_mgdl"] / settings["units"]["mgdl_per_mmol"]
    high = settings["cgm"]["sensor_high_limit_mgdl"] / settings["units"]["mgdl_per_mmol"]
    cgm.loc[cgm["glucose_mmol"] <= low + 1e-9, "censored"] = "low"
    cgm.loc[cgm["glucose_mmol"] >= high - 1e-9, "censored"] = "high"

    rows = []
    for i, b in enumerate(h.boluses):
        dur = float(b.get("duration_minutes") or 0)
        ext = dur > 0
        pct = float(b.get("standard_percent") if b.get("standard_percent") is not None else (0 if ext else 100))
        rows.append({"bolus_pk": i, "requested_at": b["time"], "completed_at": b["time"] + pd.Timedelta(minutes=dur),
                     "delivery_type": b["type"], "is_automated": b["type"] == "automated_correction",
                     "delivery_shape": "extended" if ext else "standard", "units_requested": float(b["units"]),
                     "units_delivered": float(b["units"]), "immediate_units": float(b["units"]) * pct / 100,
                     "extended_units": float(b["units"]) * (1 - pct / 100), "standard_percent": pct,
                     "extended_planned_minutes": dur if ext else np.nan, "carbs_g": float(b.get("carbs_g") or 0),
                     "carbs_repeat_entry": False})
    boluses = pd.DataFrame(rows, columns=["bolus_pk", "requested_at", "completed_at", "delivery_type", "is_automated",
                                          "delivery_shape", "units_requested", "units_delivered", "immediate_units",
                                          "extended_units", "standard_percent", "extended_planned_minutes",
                                          "carbs_g", "carbs_repeat_entry"])

    basal = sorted(h.basal)
    max_gap = settings["insulin"]["max_basal_gap_hours"] * 60
    seg = []
    for i, (t, rate, prof) in enumerate(basal):
        end = basal[i + 1][0] if i + 1 < len(basal) else pd.NaT
        dur = (end - t).total_seconds() / 60 if end is not pd.NaT else np.nan
        seg.append({"basal_pk": i, "start_at": t, "end_at": end, "duration_minutes": dur, "rate_u_per_h": float(rate),
                    "base_rate_u_per_h": float(prof if prof is not None else rate), "max_rate_u_per_h": np.nan,
                    "units": rate * dur / 60 if not np.isnan(dur) else np.nan, "change_type": "request",
                    "change_type_raw": "", "profile_idp": 0, "is_gap": bool(dur > max_gap) if not np.isnan(dur) else False})
    segments = pd.DataFrame(seg, columns=["basal_pk", "start_at", "end_at", "duration_minutes", "rate_u_per_h",
                                          "base_rate_u_per_h", "max_rate_u_per_h", "units", "change_type",
                                          "change_type_raw", "profile_idp", "is_gap"])
    for df, cols in ((cgm, ["measured_at", "available_at"]), (boluses, ["requested_at", "completed_at"]),
                     (segments, ["start_at", "end_at"])):
        for c in cols:
            df[c] = pd.to_datetime(df[c], utc=True).dt.as_unit("ns")
    return cgm, boluses, segments


def validate(h: History) -> None:
    if not h.glucose:
        raise InputError("no glucose readings")
    for t, g in h.glucose:
        if t > h.at:
            raise InputError(f"glucose reading at {t} is after the prediction time {h.at}")
        if not 1.0 <= g <= 35.0:
            raise InputError(f"glucose {g} mmol/L is outside 1–35 mmol/L (send mmol/L, or set glucose_unit)")
    for b in h.boluses:
        if b["time"] > h.at:
            raise InputError(f"insulin event at {b['time']} is after the prediction time")
        if b["type"] not in BOLUS_TYPES or not 0 <= float(b["units"]) <= 50:
            raise InputError(f"invalid bolus {b}")
    for t, rate, _ in h.basal:
        if t > h.at or not 0 <= rate <= 15:
            raise InputError(f"invalid basal rate {rate} at {t}")


@lru_cache(maxsize=1)
def production_models() -> dict:
    s = config.settings()
    return {h: registry.load_production(h) for h in s["targets"]["horizons_minutes"]}


def predict(h: History) -> dict:
    s = config.settings()
    validate(h)
    cgm, boluses, segments = to_tables(h, s)
    feats, _ = F.build_features(cgm, boluses, segments, s, h.at, h.at + pd.Timedelta(minutes=1), as_of=h.at)
    row = feats.iloc[[0]]
    cur = float(row["current_glucose"].iloc[0])
    if np.isnan(cur):
        raise InputError(f"no glucose reading in the {s['features']['max_current_staleness_minutes']} minutes "
                         "before the prediction time")
    warnings = []
    span = (h.at - min(t for t, _ in h.glucose)).total_seconds() / 60
    need = max(s["features"]["lags_minutes"])
    if span < need:
        warnings.append(f"glucose history covers {span:.0f} min; the model uses up to {need} min (missing inputs are "
                        "imputed, accuracy may be lower)")
    if not bool(row["insulin_known"].iloc[0]):
        warnings.append(f"basal history does not cover the last {s['features']['insulin_lookback_minutes']} min "
                        "without gaps; the model was only trained and evaluated when it did, so treat this "
                        "prediction as outside its validated conditions")
    user_types = s["features"]["bolus_feature_types"]
    if not any(b["type"] in user_types for b in h.boluses):
        warnings.append("no user bolus in recent_insulin: time since last bolus is treated as 24 h or more. Include "
                        "the most recent bolus even if it was many hours ago")
    if np.isnan(row["cgm_rate_mmol_per_h"].iloc[0]) or np.isnan(row["cgm_trend_ordinal"].iloc[0]):
        warnings.append("current reading has no device trend/rate; the model was trained with them (they are "
                        "imputed), so accuracy may be slightly lower")
    if row["current_censored"].iloc[0]:
        warnings.append(f"current reading is at the sensor {row['current_censored'].iloc[0].upper()} limit")

    out, versions = {}, {}
    for hz, (est, meta, conf, prod) in production_models().items():
        delta = float(est.predict(row[meta["features"]])[0])
        value = cur + delta if meta["target_type"] == "delta" else delta
        entry = {"value": round(value, 2)}
        if conf is not None:
            cats = conf_categories(meta, row)
            for L in sorted(conf.levels):
                lo, hi = conf.interval(np.array([value]), *cats, L)
                key = "" if L == 0.8 else f"_{int(L * 100)}"
                entry[f"lower{key}"], entry[f"upper{key}"] = round(float(lo[0]), 2), round(float(hi[0]), 2)
            entry["confidence_level"] = 0.8
        out[f"{hz}m"] = entry
        versions[f"{hz}m"] = f"{prod['model']}/{prod['version']}"
    return {"timestamp": h.at.isoformat().replace("+00:00", "Z"), "current_glucose": round(cur, 2),
            "unit": "mmol/L", "predictions": out, "model_version": versions, "warnings": warnings,
            "disclaimer": DISCLAIMER}


def conf_categories(meta: dict, row: pd.DataFrame):
    c = config.models()["analysis"]["categories"]
    g = iv.glucose_range(row["current_glucose"], c["glucose_ranges_mmol"])
    d = iv.direction(row[c["rate_feature"]], c["stable_rate_mmol_per_min"], c["rapid_rate_mmol_per_min"])
    return pd.Series(g.to_numpy()), pd.Series(d.to_numpy())


def history_from_db(at: pd.Timestamp, hours: float = 6, bolus_hours: float = 24, inputs=None) -> History:
    """What the system knew at `at`, from the DuckDB clean tables (for `python -m src.predict` and checks).

    Boluses go back further (24 h) because time-since-last-bolus is an input, capped at 24 h.
    `inputs` = (cgm, boluses, segments) already loaded, to avoid reloading for many calls.
    """
    from src import db
    lo = at - pd.Timedelta(hours=hours)
    lo_bolus = at - pd.Timedelta(hours=bolus_hours)
    if inputs is None:
        with db.duck(read_only=True) as d:
            inputs = F.load_inputs(d)
    cgm, boluses, segments = inputs
    c = cgm[(cgm["available_at"] <= at) & (cgm["measured_at"] > lo)]
    b = boluses[(boluses["requested_at"] > lo_bolus) & (boluses["units_delivered"] > 0)]
    std = b[(b["delivery_shape"] == "standard") & (b["completed_at"] <= at)]
    ext = b[(b["delivery_shape"] == "extended") & (b["requested_at"] <= at)]
    bol = [{"time": t, "units": u, "type": k, "carbs_g": cg} for t, u, k, cg in
           zip(std["completed_at"], std["units_delivered"], std["delivery_type"], std["carbs_g"])]
    bol += [{"time": t, "units": u, "type": k, "duration_minutes": dm, "standard_percent": p, "carbs_g": cg}
            for t, u, k, dm, p, cg in zip(ext["requested_at"], ext["units_requested"], ext["delivery_type"],
                                          ext["extended_planned_minutes"], ext["standard_percent"], ext["carbs_g"])]
    sg = segments[(segments["start_at"] <= at) & (segments["end_at"].isna() | (segments["end_at"] > lo))]
    return History(at=at, glucose=list(zip(c["measured_at"], c["glucose_mmol"])), boluses=bol,
                   basal=list(zip(sg["start_at"], sg["rate_u_per_h"], sg["base_rate_u_per_h"])),
                   glucose_trend=dict(zip(c["measured_at"], c["trend_ordinal"].astype(float))),
                   glucose_rate=dict(zip(c["measured_at"], c["rate_mmol_per_min"].astype(float))))
