"""Input sequences for sequence models (LSTM / TCN).

For prediction time T the input is the last `lookback_minutes` as a sequence
of 5-minute steps (oldest first). Channels:

  glucose            CGM value of each step's slot AS KNOWN AT T (late backfills absent until they arrive)
  glucose_observed   1 if that slot had a known reading, 0 if the value was filled
  velocity           step-to-step change of the filled glucose (mmol/L/h)
  insulin            all insulin delivered during each step (U)
  bolus_insulin      bolus insulin delivered during each step (U)
  basal_rate         commanded basal rate at each step (U/h)
  estimated_iob      estimated insulin on board at each step (approximation, not physiological)
  sin_hour, cos_hour local time of each step
  carbs              (optional) carbs entered during each step (g)

Missing glucose inside the window is filled from the most recent earlier value in
the window (leading gaps from the first observed value — still at or before T).
Nothing after T is ever used. Built from the same Grid and delivery stream as the
tabular features, so the leakage rules and the as_of audit carry over.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src import features as F
from src import iob

BASE_CHANNELS = ["glucose", "glucose_observed", "velocity", "insulin", "bolus_insulin", "basal_rate",
                 "estimated_iob", "sin_hour", "cos_hour"]


def _windows(per_slot: np.ndarray, L: int) -> np.ndarray:
    """(n_slots,) -> (n_slots, L): row i holds slots i-L+1 .. i (NaN before the first slot)."""
    padded = np.concatenate([np.full(L - 1, np.nan), per_slot])
    return np.lib.stride_tricks.sliding_window_view(padded, L)


def build_sequences(cgm: pd.DataFrame, boluses: pd.DataFrame, segments: pd.DataFrame, settings: dict,
                    start: pd.Timestamp, end: pd.Timestamp, lookback_minutes: int, include_carbs: bool = False,
                    as_of: pd.Timestamp | None = None) -> tuple[pd.DatetimeIndex, np.ndarray, list[str]]:
    """Returns (prediction times in [start, end), X of shape (n, steps, channels), channel names)."""
    step = settings["cgm"]["expected_interval_minutes"]
    L = lookback_minutes // step
    if as_of is not None:
        cgm = cgm[cgm["available_at"] <= as_of]
    # Every step in the window needs the full feature lookback (e.g. 5 h of insulin for IOB) behind it.
    pad = pd.Timedelta(minutes=F.lookback_minutes(settings) + lookback_minutes)
    slots = F.make_slots(start - pad, end, step)

    grid = F.Grid.from_cgm(cgm, slots, step)
    G = np.column_stack([grid.known(k)[0] for k in range(L - 1, -1, -1)])  # oldest -> newest
    observed = ~np.isnan(G)
    Gf = pd.DataFrame(G).ffill(axis=1).bfill(axis=1).to_numpy()
    velocity = np.zeros_like(Gf)
    velocity[:, 1:] = (Gf[:, 1:] - Gf[:, :-1]) * (60 / step)

    stream = F.delivery_stream(boluses, segments, settings, as_of)
    bolus_kinds = ["bolus", "automated_correction", "manual_correction", "unknown"]
    total = F.bin_to_slots(stream, "units", slots, step)
    bolus = F.bin_to_slots(stream[stream["kind"].isin(bolus_kinds)], "units", slots, step)
    est_iob = iob.iob_from_slots(total, step, settings["iob"])

    seg = segments if as_of is None else segments[segments["start_at"] <= as_of]
    sg = seg[["start_at", "rate_u_per_h"]].assign(
        start_at=lambda d: pd.to_datetime(d["start_at"], utc=True).dt.as_unit("ns")).sort_values("start_at")
    basal = pd.merge_asof(pd.DataFrame({"ts": slots}), sg, left_on="ts", right_on="start_at",
                          direction="backward")["rate_u_per_h"].to_numpy(dtype=float)

    local = slots.tz_convert(settings["project"]["timezone"])
    hour = (local.hour + local.minute / 60).to_numpy()
    chans = {
        "glucose": Gf, "glucose_observed": observed.astype(float), "velocity": velocity,
        "insulin": _windows(total, L), "bolus_insulin": _windows(bolus, L),
        "basal_rate": _windows(np.nan_to_num(basal, nan=0.0), L), "estimated_iob": _windows(est_iob, L),
        "sin_hour": _windows(np.sin(2 * np.pi * hour / 24), L), "cos_hour": _windows(np.cos(2 * np.pi * hour / 24), L),
    }
    if include_carbs:
        c = boluses[(boluses["carbs_g"].fillna(0) > 0) & ~boluses["carbs_repeat_entry"].fillna(False).astype(bool)]
        if as_of is not None:
            c = c[c["requested_at"] <= as_of]
        ev = pd.DataFrame({"start_at": c["requested_at"], "end_at": c["requested_at"], "carbs": c["carbs_g"]})
        chans["carbs"] = _windows(F.bin_to_slots(ev, "carbs", slots, step), L)

    names = list(chans)
    X = np.stack([chans[n] for n in names], axis=2).astype(np.float32)
    keep = slots >= start
    return slots[keep], X[keep], names
