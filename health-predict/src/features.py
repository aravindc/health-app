"""Leakage-safe feature engineering on a regular 5-minute grid.

    python -m src.features              # build grid, features, targets, splits
    python -m src.features --audit 50   # also run the leakage audit at 50 random times

Prediction times T are the grid slot times. Slot i covers (t_i - step, t_i].

Leakage rules (enforced here, tested in tests/test_leakage.py):
  * A CGM reading is usable at T only if available_at <= T (late backfills are
    not used before they arrived).
  * Lags may fall back to an OLDER reading (lag tolerance), never a newer one.
  * Rolling windows are trailing only: (T - W, T].
  * Insulin is a causal delivery stream: a standard bolus counts from its
    completion, an extended bolus at its programmed rate from its request
    (cut short only once a cancellation has happened), a basal rate from its
    start until the next change. Only insulin delivered by T is counted.
  * No interpolation, no centred windows, no statistics fitted on future data.
  * `build_features(..., as_of=X)` drops everything not yet known at X; the
    audit checks that features at X are identical to the full-data build.

`insulin_known` uses hindsight (whether the pump record later shows a gap). It
is a sample filter for training/evaluation only and is never a model input.
"""

from __future__ import annotations

import argparse
import logging
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src import config, db, iob
from src import preprocessing as pp

log = logging.getLogger("features")
NS_PER_MIN = 60_000_000_000
NEVER = np.iinfo(np.int64).max

# Columns that describe the sample rather than being model inputs.
META_COLUMNS = ["minutes_since_last_reading", "insulin_known", "current_censored"]


@dataclass
class Catalog:
    """Feature name -> (group, description); filled as features are built."""
    entries: dict[str, tuple[str, str]] = field(default_factory=dict)

    def add(self, name: str, group: str, desc: str) -> None:
        self.entries[name] = (group, desc)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame([(k, g, d) for k, (g, d) in self.entries.items()],
                            columns=["feature", "group", "description"])


# --------------------------------------------------------------------- grid

def make_slots(start: pd.Timestamp, end: pd.Timestamp, step_min: int) -> pd.DatetimeIndex:
    """Slot end times in [start, end), aligned to multiples of step from start."""
    return pd.date_range(start, end, freq=f"{step_min}min", inclusive="left", tz="UTC").as_unit("ns")


def assign_to_slots(cgm: pd.DataFrame, slots: pd.DatetimeIndex, step_min: int) -> pd.DataFrame:
    """One reading per slot (the latest measured in (t - step, t]) — for the grid_5min table only.

    Features use Grid, which keeps every reading and respects availability.
    """
    step = pd.Timedelta(minutes=step_min)
    t0 = slots[0]
    c = cgm[(cgm["measured_at"] > t0 - step) & (cgm["measured_at"] <= slots[-1])].copy()
    c["slot"] = np.ceil((c["measured_at"] - t0) / step).astype(int)
    c = c.sort_values("measured_at").drop_duplicates("slot", keep="last").set_index("slot")
    g = pd.DataFrame(index=pd.RangeIndex(len(slots)))
    for col in ("glucose_mmol", "measured_at", "available_at", "censored", "trend_ordinal", "rate_mmol_per_min",
                "source"):
        g[col] = c[col].reindex(g.index)
    g.insert(0, "ts", slots)
    return g


@dataclass
class Grid:
    """CGM readings on slots, in layers so a slot can hold several readings.

    Layer 0 is the latest-measured reading in the slot, layer 1 the next, etc.
    At time t the slot's value is the latest-measured reading that is already
    available — e.g. a live reading when a backfill in the same slot has not
    arrived yet. Arrays are shaped (layers, slots); times are ns.
    """
    t: np.ndarray
    value: np.ndarray
    meas: np.ndarray
    avail: np.ndarray
    trend: np.ndarray
    rate: np.ndarray
    censored: np.ndarray
    step: int

    @classmethod
    def from_cgm(cls, cgm: pd.DataFrame, slots: pd.DatetimeIndex, step: int, max_layers: int = 4) -> Grid:
        td = pd.Timedelta(minutes=step)
        t0 = slots[0]
        c = cgm[(cgm["measured_at"] > t0 - td) & (cgm["measured_at"] <= slots[-1])].copy()
        c["slot"] = np.ceil((c["measured_at"] - t0) / td).astype(int)
        c = c.sort_values(["slot", "measured_at"], ascending=[True, False])
        c["layer"] = c.groupby("slot").cumcount()
        c = c[c["layer"] < max_layers]
        n_layers = max(int(c["layer"].max()) + 1, 1) if len(c) else 1
        shape = (n_layers, len(slots))
        L, S = c["layer"].to_numpy(), c["slot"].to_numpy()

        def fill(col, default, dtype):
            arr = np.full(shape, default, dtype=dtype)
            arr[L, S] = col
            return arr

        ns = lambda col: pd.to_datetime(c[col], utc=True).dt.as_unit("ns").astype("int64").to_numpy()
        return cls(
            t=slots.as_unit("ns").asi8,
            value=fill(c["glucose_mmol"].to_numpy(dtype=float), np.nan, float),
            meas=fill(ns("measured_at"), NEVER, np.int64),
            avail=fill(ns("available_at"), NEVER, np.int64),
            trend=fill(pd.to_numeric(c["trend_ordinal"], errors="coerce").to_numpy(dtype=float), np.nan, float),
            rate=fill(pd.to_numeric(c["rate_mmol_per_min"], errors="coerce").to_numpy(dtype=float), np.nan, float),
            censored=fill(c["censored"].fillna("").to_numpy(dtype=object), "", object),
            step=step,
        )

    def known(self, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Slot i-k as known at t_i: (value, measured_ns, slot index, layer); slot index -1 if none."""
        n = len(self.t)
        idx = np.arange(n) - k
        src = np.where(idx >= 0, idx, 0)
        val, meas = np.full(n, np.nan), np.full(n, NEVER, dtype=np.int64)
        slot, layer = np.full(n, -1), np.full(n, -1)
        for li in range(self.value.shape[0]):
            usable = (idx >= 0) & (slot < 0) & (self.avail[li, src] <= self.t) & ~np.isnan(self.value[li, src])
            val = np.where(usable, self.value[li, src], val)
            meas = np.where(usable, self.meas[li, src], meas)
            slot = np.where(usable, src, slot)
            layer = np.where(usable, li, layer)
        return val, meas, slot, layer

    def asof(self, k: int, tries: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """First known value among slots i-k, i-k-1, ... (tries slots): falls back to OLDER data only."""
        val, meas, slot, layer = self.known(k)
        for j in range(1, tries):
            v2, m2, s2, l2 = self.known(k + j)
            f = np.isnan(val) & ~np.isnan(v2)
            val, meas = np.where(f, v2, val), np.where(f, m2, meas)
            slot, layer = np.where(f, s2, slot), np.where(f, l2, layer)
        return val, meas, slot, layer


# ------------------------------------------------------------ glucose block

def glucose_features(grid: Grid, settings: dict, cat: Catalog) -> dict[str, np.ndarray]:
    fcfg = settings["features"]
    step = grid.step
    f = settings["units"]["mgdl_per_mmol"]
    out: dict[str, np.ndarray] = {}

    stale_tries = fcfg["max_current_staleness_minutes"] // step + 1
    cur, cur_meas, cur_src, cur_layer = grid.asof(0, stale_tries)
    age = np.where(cur_meas == NEVER, np.nan, (grid.t - cur_meas) / NS_PER_MIN)
    stale = ~(age <= fcfg["max_current_staleness_minutes"])
    cur = np.where(stale, np.nan, cur)
    cur_meas = np.where(stale, NEVER, cur_meas)
    out["current_glucose"] = cur
    out["minutes_since_last_reading"] = age
    cat.add("current_glucose", "current", "Latest CGM reading known at T (mmol/L), at most "
            f"{fcfg['max_current_staleness_minutes']} min old")
    src_ok = (cur_src >= 0) & ~stale
    s, ly = np.where(src_ok, cur_src, 0), np.where(src_ok, cur_layer, 0)
    out["cgm_trend_ordinal"] = np.where(src_ok, grid.trend[ly, s], np.nan)
    out["cgm_rate_mmol_per_h"] = np.where(src_ok, grid.rate[ly, s] * 60, np.nan)
    out["current_censored"] = np.where(src_ok, grid.censored[ly, s], "")
    out["current_is_low_limit"] = (out["current_censored"] == "low").astype(float)
    cat.add("cgm_trend_ordinal", "current", "Device trend arrow of the current reading (+3 fastest rise … -3)")
    cat.add("cgm_rate_mmol_per_h", "current", "Device-reported rate of change of the current reading (Tandem only)")
    cat.add("current_is_low_limit", "current", "1 if the current reading is a sensor LOW (true value below limit)")

    tol_tries = fcfg["lag_tolerance_minutes"] // step + 1
    lag_vals, lag_meas = {}, {}
    for k in sorted(set(fcfg["lags_minutes"]) | {10, 20}):
        v, m, _, _ = grid.asof(k // step, tol_tries)
        lag_vals[k], lag_meas[k] = v, m
        if k in fcfg["lags_minutes"]:
            out[f"glucose_lag_{k}m"] = v
            cat.add(f"glucose_lag_{k}m", "history", f"Glucose ~{k} min before T (older reading up to "
                    f"{fcfg['lag_tolerance_minutes']} min earlier if missing)")

    min_frac = fcfg["rate_min_spacing_fraction"]

    def rate_between(v1, m1, v0, m0, nominal_min):
        dt = (m1 - m0) / NS_PER_MIN
        ok = (m1 != NEVER) & (m0 != NEVER) & (dt >= min_frac * nominal_min)
        return np.where(ok, (v1 - v0) / np.where(ok, dt, 1), np.nan)  # mmol/L per min

    for k in fcfg["change_minutes"]:
        out[f"glucose_change_{k}m"] = cur - lag_vals[k]
        r = rate_between(cur, cur_meas, lag_vals[k], lag_meas[k], k)
        out[f"velocity_{k}m_mmol_per_h"] = r * 60
        out[f"velocity_{k}m_mgdl_per_min"] = r * f
        cat.add(f"glucose_change_{k}m", "velocity", f"current_glucose − glucose_lag_{k}m (mmol/L)")
        cat.add(f"velocity_{k}m_mmol_per_h", "velocity", f"Rate over the last ~{k} min, using actual reading times "
                f"(missing if readings < {min_frac:.0%} of {k} min apart)")
        cat.add(f"velocity_{k}m_mgdl_per_min", "velocity", "Same rate in mg/dL/min")

    v_recent = rate_between(cur, cur_meas, lag_vals[10], lag_meas[10], 10)
    v_prev = rate_between(lag_vals[10], lag_meas[10], lag_vals[20], lag_meas[20], 10)
    mid_gap = ((cur_meas.astype(float) + lag_meas[10]) / 2 - (lag_meas[10].astype(float) + lag_meas[20]) / 2) / NS_PER_MIN
    ok = (cur_meas != NEVER) & (lag_meas[10] != NEVER) & (lag_meas[20] != NEVER) & (mid_gap > 0)
    out["acceleration_mmol_per_h2"] = np.where(ok, (v_recent - v_prev) * 60 / np.where(ok, mid_gap, 1) * 60, np.nan)
    cat.add("acceleration_mmol_per_h2", "velocity", "Change in rate: last 10 min vs the 10 min before (mmol/L/h²)")

    for w in fcfg["rolling_windows_minutes"]:
        m = w // step
        vals = np.column_stack([grid.known(j)[0] for j in range(m)])
        times = np.column_stack([grid.known(j)[1] for j in range(m)]).astype(float)
        times[times == NEVER] = np.nan
        count = np.sum(~np.isnan(vals), axis=1)
        enough = count >= max(2, int(np.ceil(fcfg["rolling_min_fraction"] * m)))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mean = np.nanmean(vals, axis=1)
            stats = {
                "mean": mean, "median": np.nanmedian(vals, axis=1), "std": np.nanstd(vals, axis=1, ddof=1),
                "min": np.nanmin(vals, axis=1), "max": np.nanmax(vals, axis=1),
            }
            stats["range"] = stats["max"] - stats["min"]
            x = (times - np.nanmean(times, axis=1, keepdims=True)) / (NS_PER_MIN * 60)  # hours
            y = vals - mean[:, None]
            stats["slope"] = np.nansum(x * y, axis=1) / np.nansum(x * x, axis=1)  # mmol/L per hour
        for name, arr in stats.items():
            col = f"glucose_{name}_{w}m"
            out[col] = np.where(enough, arr, np.nan)
            unit = "mmol/L/h" if name == "slope" else "mmol/L"
            cat.add(col, "rolling", f"Trailing {w}-min {name} of glucose ({unit}), window (T−{w}m, T]")
    return out


# ------------------------------------------------------------- time block

def time_features(slots: pd.DatetimeIndex, tz: str, cat: Catalog) -> dict[str, np.ndarray]:
    local = slots.tz_convert(tz)
    hour = local.hour + local.minute / 60
    dow = local.dayofweek.to_numpy()
    doy = local.dayofyear.to_numpy()
    out = {
        "hour": local.hour.to_numpy(), "minute": local.minute.to_numpy(), "day_of_week": dow, "day_of_year": doy,
        "is_weekend": (dow >= 5).astype(int),
        "sin_hour": np.sin(2 * np.pi * hour / 24), "cos_hour": np.cos(2 * np.pi * hour / 24),
        "sin_day_of_week": np.sin(2 * np.pi * dow / 7), "cos_day_of_week": np.cos(2 * np.pi * dow / 7),
        "sin_day_of_year": np.sin(2 * np.pi * doy / 365.25), "cos_day_of_year": np.cos(2 * np.pi * doy / 365.25),
    }
    for k in out:
        if "day_of_year" in k:
            # Kept for analysis but NOT a model input: with ~1 year of data, day of year acts as a disguised date
            # index (a validation month's values were never seen in training) and tree models memorise
            # period-specific levels. Measured on cv01: LightGBM 30-min MAE 1.239 with, 0.934 without.
            cat.add(k, "calendar", f"{k.replace('_', ' ')} at T ({tz}) — analysis only, excluded from models")
        else:
            cat.add(k, "time", f"{k.replace('_', ' ')} at T in local time ({tz})")
    return out


# ---------------------------------------------------------- insulin block

def delivery_stream(boluses: pd.DataFrame, segments: pd.DataFrame, settings: dict,
                    as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """Insulin deliveries as known in real time (see module docstring).

    Returns start_at, end_at, units, kind, is_basal_coverage (basal intervals
    used to decide whether basal delivery is known).
    """
    max_gap = pd.Timedelta(hours=settings["insulin"]["max_basal_gap_hours"])
    parts = []

    s = segments.copy()
    if as_of is not None:
        s = s[s["start_at"] <= as_of]
        s["end_at"] = s["end_at"].where(s["end_at"].notna() & (s["end_at"] <= as_of), as_of)
    s = s[s["end_at"].notna() & ((s["end_at"] - s["start_at"]) <= max_gap)]
    hours = (s["end_at"] - s["start_at"]).dt.total_seconds() / 3600
    parts.append(pd.DataFrame({"start_at": s["start_at"], "end_at": s["end_at"],
                               "units": s["rate_u_per_h"] * hours, "kind": "basal",
                               "coverage_min": hours * 60}))

    b = boluses
    std = b[b["delivery_shape"] == "standard"]
    if as_of is not None:
        std = std[std["completed_at"].notna() & (std["completed_at"] <= as_of)]
    std = std[std["units_delivered"] > 0]
    parts.append(pd.DataFrame({"start_at": std["completed_at"], "end_at": std["completed_at"],
                               "units": std["units_delivered"], "kind": std["delivery_type"], "coverage_min": 0.0}))

    ext = b[b["delivery_shape"] == "extended"]
    if as_of is not None:
        ext = ext[ext["requested_at"] <= as_of]
    imm_units = ext["units_requested"] * ext["standard_percent"] / 100  # programmed, known at request
    parts.append(pd.DataFrame({"start_at": ext["requested_at"], "end_at": ext["requested_at"], "units": imm_units,
                               "kind": ext["delivery_type"], "coverage_min": 0.0}))
    planned_min = ext["extended_planned_minutes"].fillna(
        (ext["completed_at"] - ext["requested_at"]).dt.total_seconds() / 60)
    planned_end = ext["requested_at"] + pd.to_timedelta(planned_min, unit="m")
    cancelled_end = ext["completed_at"].where(ext["completed_at"] < planned_end)
    if as_of is not None:
        cancelled_end = cancelled_end.where(cancelled_end <= as_of)
    end = cancelled_end.fillna(planned_end)
    if as_of is not None:
        end = end.where(end <= as_of, as_of)
    rate = (ext["units_requested"] - imm_units) / planned_min.where(planned_min > 0)
    units = rate * (end - ext["requested_at"]).dt.total_seconds() / 60
    parts.append(pd.DataFrame({"start_at": ext["requested_at"], "end_at": end, "units": units,
                               "kind": ext["delivery_type"], "coverage_min": 0.0}))

    out = pd.concat(parts, ignore_index=True)
    out = out[(out["units"] > 0) | (out["coverage_min"] > 0)]
    out["start_at"] = pd.to_datetime(out["start_at"], utc=True)
    out["end_at"] = pd.to_datetime(out["end_at"], utc=True)
    return out.reset_index(drop=True)


def bin_to_slots(stream: pd.DataFrame, value_col: str, slots: pd.DatetimeIndex, step_min: int) -> np.ndarray:
    """Sum of value_col delivered during each slot (t - step, t]; intervals split by overlap.

    A delivery in [b, b+step) is counted in the slot ending at b+step, so it is
    always complete by the slot time.
    """
    out = np.zeros(len(slots))
    d = stream[stream[value_col] > 0]
    if d.empty:
        return out
    b = pp.spread_to_bins(d, "start_at", "end_at", value_col, f"{step_min}min")
    idx = ((b["bin_start"] - slots[0]) / pd.Timedelta(minutes=step_min)).astype(int).to_numpy() + 1
    ok = (idx >= 0) & (idx < len(slots))
    np.add.at(out, idx[ok], b[value_col].to_numpy()[ok])
    return out


def trailing_sum(per_slot: np.ndarray, n: int) -> np.ndarray:
    cs = np.concatenate([[0.0], np.cumsum(per_slot)])
    i = np.arange(1, len(per_slot) + 1)
    return cs[i] - cs[np.maximum(i - n, 0)]


def last_event(slots: pd.DatetimeIndex, times: pd.Series, amounts: pd.Series, cap: float) -> tuple[np.ndarray, np.ndarray]:
    """Minutes since, and amount of, the latest event at or before each slot time (capped)."""
    ev = pd.DataFrame({"t": pd.to_datetime(times, utc=True).dt.as_unit("ns"), "amount": amounts.to_numpy()})
    ev = ev.dropna().sort_values("t")
    q = pd.DataFrame({"ts": slots.as_unit("ns")})
    m = pd.merge_asof(q, ev, left_on="ts", right_on="t", direction="backward")
    minutes = ((m["ts"] - m["t"]).dt.total_seconds() / 60).to_numpy()
    none = ~(minutes <= cap)
    return np.where(none, cap, minutes), np.where(none, 0.0, m["amount"].to_numpy(dtype=float))


def insulin_features(slots: pd.DatetimeIndex, boluses: pd.DataFrame, segments: pd.DataFrame, settings: dict,
                     cat: Catalog, as_of: pd.Timestamp | None = None) -> dict[str, np.ndarray]:
    fcfg, step = settings["features"], settings["cgm"]["expected_interval_minutes"]
    stream = delivery_stream(boluses, segments, settings, as_of)
    bolus_kinds = ["bolus", "automated_correction", "manual_correction", "unknown"]
    per = {
        "total": bin_to_slots(stream, "units", slots, step),
        "basal": bin_to_slots(stream[stream["kind"] == "basal"], "units", slots, step),
        "bolus": bin_to_slots(stream[stream["kind"].isin(bolus_kinds)], "units", slots, step),
        "auto": bin_to_slots(stream[stream["kind"] == "automated_correction"], "units", slots, step),
    }
    coverage = bin_to_slots(stream, "coverage_min", slots, step)
    out: dict[str, np.ndarray] = {}
    for w in fcfg["insulin_windows_minutes"]:
        out[f"insulin_last_{w}m"] = trailing_sum(per["total"], w // step)
        cat.add(f"insulin_last_{w}m", "insulin", f"All insulin delivered in (T−{w}m, T] (U)")
    for w in fcfg["basal_windows_minutes"]:
        out[f"basal_insulin_last_{w}m"] = trailing_sum(per["basal"], w // step)
        cat.add(f"basal_insulin_last_{w}m", "insulin", f"Basal insulin delivered in (T−{w}m, T] (U)")
    for w in fcfg["bolus_windows_minutes"]:
        out[f"bolus_insulin_last_{w}m"] = trailing_sum(per["bolus"], w // step)
        cat.add(f"bolus_insulin_last_{w}m", "insulin", f"Bolus insulin (all bolus types) delivered in (T−{w}m, T] (U)")
    out["automated_correction_last_60m"] = trailing_sum(per["auto"], 60 // step)
    cat.add("automated_correction_last_60m", "insulin", "Control-IQ automatic correction insulin in (T−60m, T] (U)")

    # Time since last user bolus. Standard: known at completion (delivered units). Extended: known at request
    # (programmed units, because the delivered total is only known at the end).
    b = boluses[boluses["delivery_type"].isin(fcfg["bolus_feature_types"]) & (boluses["units_delivered"] > 0)]
    ext = b["delivery_shape"].eq("extended")
    known_at = b["completed_at"].where(~ext, b["requested_at"])
    amount = b["units_delivered"].where(~ext, b["units_requested"])
    if as_of is not None:
        keep = known_at <= as_of
        known_at, amount = known_at[keep], amount[keep]
    cap = fcfg["minutes_since_cap"]
    out["minutes_since_last_bolus"], out["last_bolus_amount"] = last_event(slots, known_at, amount, cap)
    cat.add("minutes_since_last_bolus", "insulin", f"Minutes since the last user bolus ({', '.join(fcfg['bolus_feature_types'])}); capped at {cap}")
    cat.add("last_bolus_amount", "insulin", "Units of that bolus (0 if none within the cap)")

    seg = segments if as_of is None else segments[segments["start_at"] <= as_of]
    q = pd.DataFrame({"ts": slots.as_unit("ns")})
    sg = seg[["start_at", "rate_u_per_h", "base_rate_u_per_h"]].assign(
        start_at=lambda d: pd.to_datetime(d["start_at"], utc=True).dt.as_unit("ns")).sort_values("start_at")
    m = pd.merge_asof(q, sg, left_on="ts", right_on="start_at", direction="backward")
    out["basal_rate_now"] = m["rate_u_per_h"].to_numpy(dtype=float)
    out["basal_profile_rate_now"] = m["base_rate_u_per_h"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["basal_ratio_now"] = np.where(out["basal_profile_rate_now"] > 0,
                                          out["basal_rate_now"] / out["basal_profile_rate_now"], np.nan)
    out["basal_suspended_now"] = (out["basal_rate_now"] == 0).astype(float)
    cat.add("basal_rate_now", "insulin", "Commanded basal rate at T (U/h) — reflects Control-IQ's response")
    cat.add("basal_profile_rate_now", "insulin", "Scheduled (profile) basal rate at T (U/h)")
    cat.add("basal_ratio_now", "insulin", "basal_rate_now / basal_profile_rate_now")
    cat.add("basal_suspended_now", "insulin", "1 if basal is suspended at T")

    icfg = settings["iob"]
    out["estimated_iob"] = iob.iob_from_slots(per["total"], step, icfg)
    out["estimated_iob_bolus"] = iob.iob_from_slots(per["bolus"], step, icfg)
    curve = f"{icfg['model']} curve, duration {icfg['insulin_duration_hours']} h, peak {icfg['peak_minutes']} min"
    cat.add("estimated_iob", "iob", f"ESTIMATED insulin on board, all insulin ({curve}); not a physiological measurement")
    cat.add("estimated_iob_bolus", "iob", "ESTIMATED insulin on board from boluses only")

    lookback = fcfg["insulin_lookback_minutes"] // step
    missing = (coverage < step - 0.01).astype(float)
    out["insulin_known"] = trailing_sum(missing, lookback) == 0
    return out


def carb_features(slots: pd.DatetimeIndex, boluses: pd.DataFrame, settings: dict, cat: Catalog,
                  as_of: pd.Timestamp | None = None) -> dict[str, np.ndarray]:
    """Optional group: carbs entered with boluses, known at the bolus request."""
    fcfg, step = settings["features"], settings["cgm"]["expected_interval_minutes"]
    c = boluses[(boluses["carbs_g"].fillna(0) > 0) & ~boluses["carbs_repeat_entry"].fillna(False).astype(bool)]
    if as_of is not None:
        c = c[c["requested_at"] <= as_of]
    ev = pd.DataFrame({"start_at": c["requested_at"], "end_at": c["requested_at"], "carbs": c["carbs_g"]})
    per = bin_to_slots(ev, "carbs", slots, step)
    out = {}
    for w in fcfg["carbs_windows_minutes"]:
        out[f"carbs_last_{w}m"] = trailing_sum(per, w // step)
        cat.add(f"carbs_last_{w}m", "carbs", f"Carbs entered in (T−{w}m, T] (g) — optional group")
    out["minutes_since_carbs"], out["last_carbs_g"] = last_event(slots, c["requested_at"], c["carbs_g"],
                                                                 fcfg["minutes_since_cap"])
    cat.add("minutes_since_carbs", "carbs", "Minutes since the last carb entry (capped) — optional group")
    cat.add("last_carbs_g", "carbs", "Grams in that entry — optional group")
    return out


# ------------------------------------------------------------- assemble

def lookback_minutes(settings: dict) -> int:
    f = settings["features"]
    step = settings["cgm"]["expected_interval_minutes"]
    need = max(max(f["lags_minutes"]) + f["lag_tolerance_minutes"], max(f["rolling_windows_minutes"]),
               f["insulin_lookback_minutes"], settings["iob"]["insulin_duration_hours"] * 60, 20 + 10) + 60
    return int(np.ceil(need / step) * step)  # whole slots, so grids built from any slot time stay aligned


def build_features(cgm: pd.DataFrame, boluses: pd.DataFrame, segments: pd.DataFrame, settings: dict,
                   start: pd.Timestamp, end: pd.Timestamp, as_of: pd.Timestamp | None = None
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Features for slot times in [start, end). With as_of, only data known at as_of is used.

    Returns (features indexed by ts, catalog).
    """
    step = settings["cgm"]["expected_interval_minutes"]
    if as_of is not None:
        cgm = cgm[cgm["available_at"] <= as_of]
    pad = pd.Timedelta(minutes=lookback_minutes(settings))
    slots = make_slots(start - pad, end, step)
    grid = Grid.from_cgm(cgm, slots, step)
    cat = Catalog()
    cols = glucose_features(grid, settings, cat)
    cols |= time_features(slots, settings["project"]["timezone"], cat)
    cols |= insulin_features(slots, boluses, segments, settings, cat, as_of)
    cols |= carb_features(slots, boluses, settings, cat, as_of)
    df = pd.DataFrame(cols, index=pd.Index(slots, name="ts"))
    return df[df.index >= start], cat.frame()


# ---------------------------------------------------------------- load

def load_inputs(duck) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cgm = duck.execute("SELECT * FROM cgm_merged").df()
    boluses = duck.execute("SELECT * FROM boluses").df()
    segments = duck.execute("SELECT * FROM basal_segments").df()
    for df, cols in ((cgm, ["measured_at", "available_at"]), (boluses, ["requested_at", "completed_at"]),
                     (segments, ["start_at", "end_at"])):
        for c in cols:
            df[c] = pd.to_datetime(df[c], utc=True).dt.as_unit("ns")
    return cgm, boluses, segments


def window_bounds(settings: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    tz = settings["project"]["timezone"]
    w = settings["modelling_window"]
    return (pd.Timestamp(w["start"], tz=tz).tz_convert("UTC"),
            (pd.Timestamp(w["end"], tz=tz) + pd.Timedelta(days=1)).tz_convert("UTC"))


def leakage_audit(cgm, boluses, segments, full: pd.DataFrame, settings: dict, n: int, seed: int) -> pd.DataFrame:
    """Rebuild features using only data known at chosen times T; compare with the full build at T.

    Times are drawn equally from: random times, times when a backfilled reading had been measured but not yet
    received, times during an extended bolus, and times shortly after a standard bolus.
    """
    rng = np.random.default_rng(seed)
    step = pd.Timedelta(minutes=settings["cgm"]["expected_interval_minutes"])
    ok = full.index[full["insulin_known"] & full["current_glucose"].notna()]

    def slot_after(times):
        return pd.DatetimeIndex(times.dt.ceil(step)).intersection(ok)

    bf = cgm[cgm["available_at"] - cgm["measured_at"] > 2 * step]
    pools = {
        "random": ok,
        "backfill pending": slot_after(bf["measured_at"] + step),
        "extended bolus running": slot_after(boluses.loc[boluses["delivery_shape"] == "extended", "requested_at"]
                                             + pd.Timedelta(minutes=20)),
        "after bolus": slot_after(boluses["completed_at"].dropna() + pd.Timedelta(minutes=3)),
    }
    picks = []
    for name, pool in pools.items():
        k = min(max(n // len(pools), 1), len(pool))
        picks += [(name, pd.Timestamp(t)) for t in rng.choice(pool, size=k, replace=False)]
    feature_cols = [c for c in full.columns if c != "insulin_known"]
    rows = []
    for name, t in sorted(picks, key=lambda x: x[1]):
        part, _ = build_features(cgm, boluses, segments, settings, t, t + pd.Timedelta(minutes=1), as_of=t)
        a, b = full.loc[t, feature_cols], part.loc[t, feature_cols]
        diff = [c for c in feature_cols if not _same(a[c], b[c])]
        rows.append({"case": name, "ts": t, "n_features": len(feature_cols), "mismatches": len(diff),
                     "columns": ", ".join(diff)})
    return pd.DataFrame(rows)


def _same(x, y) -> bool:
    if isinstance(x, str) or isinstance(y, str):
        return x == y
    if pd.isna(x) and pd.isna(y):
        return True
    return bool(np.isclose(float(x), float(y), rtol=1e-9, atol=1e-9))


def main() -> None:
    from src import splits, targets  # local import keeps module import light for tests

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", type=int, default=0, help="run the leakage audit at N random prediction times")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = config.settings()
    version = s["features"]["version"]
    start, end = window_bounds(s)

    with db.duck() as duck:
        cgm, boluses, segments = load_inputs(duck)
        feats, catalog = build_features(cgm, boluses, segments, s, start, end)
        log.info("features: %d rows x %d columns", *feats.shape)
        tg = targets.make_targets(cgm, feats["current_glucose"], s)
        folds = splits.make_folds(s)
        assignment = splits.assign_all(feats.index, folds)
        grid = assign_to_slots(cgm, make_slots(start, end, s["cgm"]["expected_interval_minutes"]),
                               s["cgm"]["expected_interval_minutes"])

        tables = {"grid_5min": grid, f"features_{version}": feats.reset_index(), "targets": tg.reset_index(),
                  "splits": folds, "split_assignment": assignment.reset_index(),
                  f"feature_catalog_{version}": catalog}
        for name, df in tables.items():
            duck.register("df", df)
            duck.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")
            duck.unregister("df")
        out = config.path("data/features")
        out.mkdir(parents=True, exist_ok=True)
        feats.join(tg.drop(columns=["current_glucose"])).join(assignment).reset_index().to_parquet(
            out / f"features_{version}.parquet", index=False)
        log.info("wrote tables %s and %s", ", ".join(tables), out / f"features_{version}.parquet")

        audit = None
        if args.audit:
            audit = leakage_audit(cgm, boluses, segments, feats, s, args.audit, s["project"]["random_seed"])
            bad = audit[audit["mismatches"] > 0]
            log.info("leakage audit: %d times checked, %d with mismatches", len(audit), len(bad))
            if len(bad):
                log.warning("mismatching columns: %s", sorted(set(", ".join(bad["columns"]).split(", "))))

    from src import feature_report
    feature_report.write(s, feats, tg, catalog, folds, assignment, audit)


if __name__ == "__main__":
    main()
