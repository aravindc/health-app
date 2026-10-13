"""Rule-based baselines. They need no training and predict glucose directly."""

from __future__ import annotations

import numpy as np
import pandas as pd


def persistence(X: pd.DataFrame, horizon: int) -> np.ndarray:
    """Future glucose = current glucose."""
    return X["current_glucose"].to_numpy(dtype=float)


def linear_trend(X: pd.DataFrame, horizon: int, velocity_features: list[str]) -> np.ndarray:
    """Extrapolate the recent trend: current + velocity (mmol/L/h) * horizon.

    Uses the first available velocity in `velocity_features`; with none, falls back to persistence.
    """
    v = pd.Series(np.nan, index=X.index)
    for col in velocity_features:
        v = v.fillna(X[col])
    return (X["current_glucose"] + v.fillna(0.0) * horizon / 60).to_numpy(dtype=float)
