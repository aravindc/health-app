import numpy as np
import pandas as pd
import pytest

from src import iob, splits, targets
from tests import synth
from tests.synth import T0


def at(m):
    return T0 + pd.Timedelta(minutes=m)


# ------------------------------------------------------------------- IOB

def test_exponential_curve_shape():
    t = np.arange(0, 301, 5)
    r = iob.exponential_remaining(t, 5, 75)
    assert r[0] == 1 and r[-1] == 0
    assert np.all(np.diff(r) <= 1e-12)


def test_invalid_peak_rejected():
    with pytest.raises(ValueError):
        iob.exponential_remaining(np.array([0]), 3, 100)


def test_iob_is_causal():
    u = np.zeros(100)
    u[50] = 4.0
    out = iob.iob_from_slots(u, 5, {"model": "exponential", "insulin_duration_hours": 5, "peak_minutes": 75})
    assert np.all(out[:50] == 0) and out[50] > 3.9


# --------------------------------------------------------------- targets

def test_targets_use_nearest_reading_within_tolerance(settings):
    c = synth.cgm([(0, 5.0), (15, 6.0), (30, 7.0, 99), (60, 9.0)])  # late arrival does not matter for targets
    c.loc[2, "censored"] = "high"
    cur = pd.Series([5.0, 5.0], index=pd.DatetimeIndex([at(0), at(5)]))
    t = targets.make_targets(c, cur, settings)
    r = t.loc[at(0)]
    assert r["glucose_15m"] == 6.0 and r["delta_15m"] == pytest.approx(1.0)
    assert r["glucose_30m"] == 7.0 and r["target_30m_censored"] == "high"
    assert np.isnan(r["glucose_45m"])                   # nothing within 2.5 min of +45
    assert r["glucose_60m"] == 9.0
    assert np.isnan(t.loc[at(5), "glucose_15m"])         # +20 is 5 min from the nearest reading


# ---------------------------------------------------------------- splits

def test_folds_are_chronological_with_embargo(settings):
    f = splits.make_folds(settings)
    emb = pd.Timedelta(minutes=settings["splits"]["embargo_minutes"])
    test = f[(f["fold"] == "final") & (f["role"] == "test")].iloc[0]
    assert test["start"] == pd.Timestamp(settings["splits"]["test_start"], tz=settings["project"]["timezone"])
    cv = f[f["fold"].str.startswith("cv")]
    for name, g in cv.groupby("fold"):
        tr, va = g[g["role"] == "train"].iloc[0], g[g["role"] == "validation"].iloc[0]
        assert tr["end"] == va["start"] - emb
        assert va["end"] <= test["start"] - emb
        assert tr["start"] == f.iloc[0]["start"]          # expanding window
    starts = cv[cv["role"] == "validation"]["start"].tolist()
    assert starts == sorted(starts) and len(starts) >= 6


def test_assign_all_labels(settings):
    f = splits.make_folds(settings)
    test = f[(f["fold"] == "final") & (f["role"] == "test")].iloc[0]
    ts = pd.DatetimeIndex([test["start"] - pd.Timedelta(minutes=30), test["start"], f.iloc[0]["start"]])
    a = splits.assign_all(ts, f)
    assert list(a["split"]) == ["embargo", "test", "development"]
