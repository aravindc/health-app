"""Prediction intervals.

Primary method (declared before looking at test results): Mondrian split
conformal. Signed residuals (actual − predicted) from out-of-fold validation
predictions in the development period are grouped into bins of current glucose
range × direction of change. For a level L, the interval is
[pred + q_lo, pred + q_hi] with q_lo/q_hi the bin's (1−L)/2 and (1+L)/2 residual
quantiles (finite-sample adjusted). Bins with too few residuals fall back to the
glucose range alone, then to all residuals.

Comparison: LightGBM quantile regression (raw quantiles, not conformalised).

Residuals come from models trained on expanding windows with less data than the
final model, so intervals are, if anything, slightly conservative.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd


def glucose_range(g: pd.Series, edges: list[float]) -> pd.Series:
    labels = [f"<{edges[0]}"] + [f"{a}–{b}" for a, b in pairwise(edges)] + [f">{edges[-1]}"]
    return pd.Series(pd.cut(g, [-np.inf, *edges, np.inf], labels=labels, right=False).astype(str), index=g.index)


def direction(rate_mmol_per_h: pd.Series, stable: float, rapid: float) -> pd.Series:
    r = rate_mmol_per_h / 60  # mmol/L/min
    out = np.select([r <= -rapid, r <= -stable, r < stable, r < rapid], ["rapidly falling", "falling", "stable",
                                                                          "rising"], default="rapidly rising")
    return pd.Series(np.where(r.isna(), "unknown", out), index=rate_mmol_per_h.index)


def conformal_quantile(res: np.ndarray, p: float) -> float:
    """Finite-sample adjusted empirical quantile (conservative in the tails)."""
    n = len(res)
    if p >= 0.5:
        k = min(np.ceil((n + 1) * p) / n, 1.0)
    else:
        k = max(np.floor((n + 1) * p) / n, 0.0)
    return float(np.quantile(res, k, method="higher" if p >= 0.5 else "lower"))


class MondrianConformal:
    def __init__(self, levels: list[float], min_bin_size: int):
        self.levels, self.min_bin = levels, min_bin_size

    def fit(self, residuals: pd.Series, grange: pd.Series, direc: pd.Series) -> MondrianConformal:
        d = pd.DataFrame({"r": residuals.to_numpy(), "g": grange.to_numpy(), "d": direc.to_numpy()})
        self.table_ = {}
        for L in self.levels:
            lo_p, hi_p = (1 - L) / 2, (1 + L) / 2
            q = lambda x, lo=lo_p, hi=hi_p: (conformal_quantile(x, lo), conformal_quantile(x, hi), len(x))
            self.table_[L] = {
                "global": q(d["r"].to_numpy()),
                "g": {k: q(v.to_numpy()) for k, v in d.groupby("g")["r"] if len(v) >= self.min_bin},
                "gd": {k: q(v.to_numpy()) for k, v in d.groupby(["g", "d"])["r"] if len(v) >= self.min_bin},
            }
        return self

    def interval(self, pred: np.ndarray, grange: pd.Series, direc: pd.Series, level: float) -> tuple[np.ndarray, np.ndarray]:
        t = self.table_[level]
        lo, hi = np.empty(len(pred)), np.empty(len(pred))
        for i, (g, d) in enumerate(zip(grange, direc)):
            ql, qh, _ = t["gd"].get((g, d)) or t["g"].get(g) or t["global"]
            lo[i], hi[i] = ql, qh
        return pred + lo, pred + hi

    def bins_frame(self, level: float) -> pd.DataFrame:
        t = self.table_[level]
        rows = [(g, d, ql, qh, n) for (g, d), (ql, qh, n) in t["gd"].items()]
        return pd.DataFrame(rows, columns=["glucose range", "direction", "lower offset", "upper offset", "n"])


def interval_metrics(y, lo, hi) -> dict[str, float]:
    y, lo, hi = (np.asarray(a, dtype=float) for a in (y, lo, hi))
    ok = ~np.isnan(y) & ~np.isnan(lo) & ~np.isnan(hi)
    inside = (y[ok] >= lo[ok]) & (y[ok] <= hi[ok])
    return {"n": int(ok.sum()), "coverage": float(inside.mean()), "mean_width": float(np.mean(hi[ok] - lo[ok])),
            "below": float((y[ok] < lo[ok]).mean()), "above": float((y[ok] > hi[ok]).mean())}


def sequential_check(cal: pd.DataFrame, levels: list[float], min_bin: int) -> pd.DataFrame:
    """Calibrate on validation months before k, check coverage in month k (honest, development data only).

    `cal` needs: fold, horizon, residual, y_true, y_pred, grange, direction.
    """
    rows = []
    folds = sorted(cal["fold"].unique())
    for h, ch in cal.groupby("horizon"):
        for k in folds[2:]:
            past, now = ch[ch["fold"] < k], ch[ch["fold"] == k]
            mc = MondrianConformal(levels, min_bin).fit(past["residual"], past["grange"], past["direction"])
            for L in levels:
                lo, hi = mc.interval(now["y_pred"].to_numpy(), now["grange"], now["direction"], L)
                rows.append({"horizon": h, "fold": k, "level": L, **interval_metrics(now["y_true"], lo, hi)})
    return pd.DataFrame(rows)


def conformal_to_dict(mc: MondrianConformal, categories: dict) -> dict:
    """JSON-serialisable form (stored next to a model so the API can produce intervals)."""
    def enc(t):
        return {"global": list(t["global"]), "g": {k: list(v) for k, v in t["g"].items()},
                "gd": {f"{g}|{d}": list(v) for (g, d), v in t["gd"].items()}}
    return {"method": "mondrian_split_conformal", "levels": mc.levels, "min_bin_size": mc.min_bin,
            "categories": categories, "tables": {str(L): enc(t) for L, t in mc.table_.items()}}


def conformal_from_dict(d: dict) -> MondrianConformal:
    mc = MondrianConformal(d["levels"], d["min_bin_size"])
    mc.table_ = {}
    for L, t in d["tables"].items():
        mc.table_[float(L)] = {"global": tuple(t["global"]), "g": {k: tuple(v) for k, v in t["g"].items()},
                               "gd": {tuple(k.split("|", 1)): tuple(v) for k, v in t["gd"].items()}}
    return mc
