"""Regression metrics on the glucose scale (mmol/L)."""

from __future__ import annotations

import numpy as np


def regression_metrics(y_true, y_pred) -> dict[str, float]:
    y, p = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    ok = ~(np.isnan(y) | np.isnan(p))
    y, p = y[ok], p[ok]
    if len(y) == 0:
        return {"n": 0}
    err = p - y
    abs_err = np.abs(err)
    ss_res = float(np.sum(err**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return {
        "n": len(y),
        "mae": float(abs_err.mean()),
        "rmse": float(np.sqrt(np.mean(err**2))),
        "mape_pct": float(np.mean(abs_err / y) * 100),   # glucose is always > 0
        "median_ae": float(np.median(abs_err)),
        "r2": 1 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
        "bias": float(err.mean()),                     # mean error, prediction − actual
        "p95_ae": float(np.quantile(abs_err, 0.95)),
    }
