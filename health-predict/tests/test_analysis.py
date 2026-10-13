import numpy as np
import pandas as pd
import pytest

from src import error_analysis as ea
from src import intervals as iv


def test_glucose_range_and_direction_labels():
    g = pd.Series([3.0, 3.9, 8.0, 10.0, 14.0])
    assert list(iv.glucose_range(g, [3.9, 7.0, 10.0, 13.9])) == ["<3.9", "3.9–7.0", "7.0–10.0", "10.0–13.9", ">13.9"]
    r = pd.Series([-7.0, -4.0, 0.0, 4.0, 7.0, np.nan])  # mmol/L/h
    assert list(iv.direction(r, 0.055, 0.1)) == ["rapidly falling", "falling", "stable", "rising", "rapidly rising",
                                                  "unknown"]


def test_conformal_coverage_on_exchangeable_data():
    rng = np.random.default_rng(1)
    n = 20000
    g = pd.Series(rng.choice(["a", "b"], n))
    d = pd.Series(["stable"] * n)
    scale = np.where(g == "a", 0.5, 2.0)                 # heteroscedastic: bins must adapt
    res = rng.normal(0, 1, n) * scale
    mc = iv.MondrianConformal([0.8, 0.95], 100).fit(pd.Series(res[:10000]), g[:10000], d[:10000])
    y = res[10000:]
    lo, hi = mc.interval(np.zeros(10000), g[10000:], d[10000:], 0.8)
    m = iv.interval_metrics(y, lo, hi)
    assert m["coverage"] == pytest.approx(0.8, abs=0.015)
    for grp in ("a", "b"):                               # coverage holds within each bin
        sel = (g[10000:] == grp).to_numpy()
        assert iv.interval_metrics(y[sel], lo[sel], hi[sel])["coverage"] == pytest.approx(0.8, abs=0.03)
    wa = (hi - lo)[(g[10000:] == "a").to_numpy()].mean()
    wb = (hi - lo)[(g[10000:] == "b").to_numpy()].mean()
    assert wb > 3 * wa


def test_conformal_falls_back_for_small_bins():
    res = pd.Series(np.linspace(-1, 1, 1000))
    g = pd.Series(["big"] * 990 + ["tiny"] * 10)
    d = pd.Series(["stable"] * 1000)
    mc = iv.MondrianConformal([0.8], 100).fit(res, g, d)
    lo, hi = mc.interval(np.zeros(2), pd.Series(["tiny", "never seen"]), pd.Series(["stable", "stable"]), 0.8)
    assert np.isfinite(lo).all() and np.isfinite(hi).all()
    assert ("tiny", "stable") not in mc.table_[0.8]["gd"]


def test_binary_metrics():
    m = ea.binary_metrics(np.array([1, 1, 0, 0, 1], bool), np.array([1, 0, 0, 1, 1], bool))
    assert m["events"] == 3 and m["sensitivity (recall)"] == pytest.approx(2 / 3)
    assert m["specificity"] == pytest.approx(0.5) and m["precision"] == pytest.approx(2 / 3)
    assert m["false-negative rate"] == pytest.approx(1 / 3)


def test_becoming_low_only_counts_currently_not_low():
    ts = pd.date_range("2026-08-01", periods=3, freq="5min", tz="UTC")
    rows = []
    for t, cur, a15, a30, p15, p30 in [(ts[0], 5.0, 4.2, 3.5, 4.5, 3.8),   # becomes low, predicted
                                       (ts[1], 5.0, 5.5, 6.0, 5.2, 5.1),   # stays in range
                                       (ts[2], 3.5, 3.4, 3.6, 3.5, 3.5)]:  # already low: excluded
        rows += [{"ts": t, "model": "m", "horizon": 15, "current_glucose": cur, "y_true": a15, "y_pred": p15},
                 {"ts": t, "model": "m", "horizon": 30, "current_glucose": cur, "y_true": a30, "y_pred": p30}]
    out = ea.becoming_low(pd.DataFrame(rows), "m", 3.9)
    r = out.iloc[0]
    assert r["events"] == 1 and r["flagged"] == 1 and r["specificity"] == 1.0
