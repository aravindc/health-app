"""Estimated insulin on board (IOB).

`estimated_iob` is a modelling approximation of insulin still expected to act.
It is NOT a physiological or medically accurate measurement.

Model: the exponential insulin action curve used by Loop (derived by Dragan
Maksimovic), parameterised by duration of insulin action (td) and peak time (tp).
"""

from __future__ import annotations

import numpy as np

MODELS = ("exponential",)


def exponential_remaining(t_minutes: np.ndarray, duration_hours: float, peak_minutes: float) -> np.ndarray:
    """Fraction of a dose still remaining t minutes after delivery (1 at t=0, 0 at t >= duration)."""
    td = duration_hours * 60.0
    tp = float(peak_minutes)
    if not 0 < tp < td / 2:
        raise ValueError("peak_minutes must be between 0 and half the insulin duration")
    tau = tp * (1 - tp / td) / (1 - 2 * tp / td)
    a = 2 * tau / td
    s = 1 / (1 - a + (1 + a) * np.exp(-td / tau))
    t = np.asarray(t_minutes, dtype=float)
    rem = 1 - s * (1 - a) * ((t**2 / (tau * td * (1 - a)) - t / tau - 1) * np.exp(-t / tau) + 1)
    rem = np.where(t <= 0, 1.0, rem)
    return np.clip(np.where(t >= td, 0.0, rem), 0.0, 1.0)


def remaining_curve(cfg: dict, t_minutes: np.ndarray) -> np.ndarray:
    model = cfg.get("model", "exponential")
    if model == "exponential":
        return exponential_remaining(t_minutes, cfg["insulin_duration_hours"], cfg["peak_minutes"])
    raise ValueError(f"unknown IOB model {model!r}; available: {MODELS}")


def iob_from_slots(units_per_slot: np.ndarray, step_minutes: float, cfg: dict) -> np.ndarray:
    """IOB at the end of each slot from insulin delivered during each slot.

    Insulin delivered during slot s (the interval ending at slot time t_s) is
    assumed to have been given at its midpoint, so at slot time t_i it has
    acted for (i - s) * step + step / 2 minutes. Only slots s <= i contribute,
    so the result is causal.
    """
    n_kernel = int(np.ceil(cfg["insulin_duration_hours"] * 60 / step_minutes)) + 1
    kernel = remaining_curve(cfg, np.arange(n_kernel) * step_minutes + step_minutes / 2)
    u = np.nan_to_num(units_per_slot, nan=0.0)
    return np.convolve(u, kernel)[: len(u)]
