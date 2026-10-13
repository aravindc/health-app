"""Small synthetic inputs in the shape of the clean tables (cgm_merged, boluses, basal_segments)."""

import numpy as np
import pandas as pd

T0 = pd.Timestamp("2026-06-01 00:00", tz="UTC").as_unit("ns")
MIN = pd.Timedelta(minutes=1)


def cgm(points, phase_seconds=48):
    """points: list of (minutes after T0, glucose) or (minutes, glucose, minutes_late)."""
    rows = []
    for p in points:
        m, g = p[0], p[1]
        late = p[2] if len(p) > 2 else 0
        t = T0 + pd.Timedelta(minutes=m) - pd.Timedelta(seconds=phase_seconds)
        rows.append({"measured_at": t, "available_at": t + pd.Timedelta(minutes=late), "glucose_mmol": float(g),
                     "censored": None, "trend_ordinal": 0, "rate_mmol_per_min": 0.0,
                     "source": "tandem", "reading_id": f"t:{len(rows)}"})
    df = pd.DataFrame(rows)
    for c in ("measured_at", "available_at"):
        df[c] = pd.to_datetime(df[c], utc=True).dt.as_unit("ns")
    return df


def regular_cgm(start_min, end_min, fn, step=5):
    return cgm([(m, fn(m)) for m in range(start_min, end_min, step)])


def boluses(rows=()):
    cols = ["bolus_pk", "requested_at", "completed_at", "delivery_type", "is_automated", "delivery_shape",
            "units_requested", "units_delivered", "immediate_units", "extended_units", "standard_percent",
            "extended_planned_minutes", "carbs_g", "carbs_repeat_entry"]
    out = []
    for i, r in enumerate(rows):
        req = T0 + pd.Timedelta(minutes=r["at"])
        shape = r.get("shape", "standard")
        units = r["units"]
        pct = r.get("pct", 100.0)
        planned = r.get("planned", np.nan)
        done = req + pd.Timedelta(minutes=r.get("took", 2 if shape == "standard" else planned + 1))
        delivered = r.get("delivered", units)
        out.append({"bolus_pk": i + 1, "requested_at": req, "completed_at": done,
                    "delivery_type": r.get("type", "bolus"), "is_automated": r.get("type") == "automated_correction",
                    "delivery_shape": shape, "units_requested": units, "units_delivered": delivered,
                    "immediate_units": units * pct / 100, "extended_units": delivered - units * pct / 100,
                    "standard_percent": pct, "extended_planned_minutes": planned, "carbs_g": r.get("carbs", 0.0),
                    "carbs_repeat_entry": r.get("repeat", False)})
    df = pd.DataFrame(out, columns=cols)
    for c in ("requested_at", "completed_at"):
        df[c] = pd.to_datetime(df[c], utc=True).dt.as_unit("ns")
    return df


def segments(changes, end_min=None, max_gap_h=6):
    """changes: list of (minutes after T0, rate U/h). The last one stays open unless end_min is given."""
    rows = []
    for i, (m, rate) in enumerate(changes):
        start = T0 + pd.Timedelta(minutes=m)
        nxt = changes[i + 1][0] if i + 1 < len(changes) else end_min
        end = T0 + pd.Timedelta(minutes=nxt) if nxt is not None else pd.NaT
        dur = (end - start).total_seconds() / 60 if nxt is not None else np.nan
        gap = bool(dur > max_gap_h * 60) if nxt is not None else False
        rows.append({"basal_pk": i + 1, "start_at": start, "end_at": end, "duration_minutes": dur,
                     "rate_u_per_h": float(rate), "base_rate_u_per_h": 0.5, "max_rate_u_per_h": 1.0,
                     "units": np.nan if gap or nxt is None else rate * dur / 60, "change_type": "automated",
                     "change_type_raw": "[0]", "profile_idp": 0, "is_gap": gap})
    df = pd.DataFrame(rows)
    for c in ("start_at", "end_at"):
        df[c] = pd.to_datetime(df[c], utc=True).dt.as_unit("ns")
    return df


def steady_basal(rate, start_min=-720, end_min=2000, every=120):
    """Basal records every `every` minutes (pumps log changes often; > 6 h without one is a gap)."""
    return segments([(m, rate) for m in range(start_min, end_min, every)], end_min=end_min)
