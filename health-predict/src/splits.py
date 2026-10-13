"""Chronological train / validation / test splits.

* Test set: fixed start date (settings.splits.test_start) to the end of the
  modelling window. It is held out until model selection is complete.
* Model selection: rolling-origin (expanding window) folds. Fold k validates
  on one calendar month and trains on everything from the window start up to
  that month.
* Embargo: training sample times end `embargo_minutes` (= the longest
  horizon) before validation/test starts, so no training target lies inside a
  validation/test period. Validation folds also stop `embargo_minutes` before
  the test period.
* Features may look back across a boundary: that history was known at T.
* Never random splits.
"""

from __future__ import annotations

import pandas as pd


def _local(day: str, tz: str) -> pd.Timestamp:
    return pd.Timestamp(day, tz=tz).tz_convert("UTC")


def make_folds(settings: dict) -> pd.DataFrame:
    tz = settings["project"]["timezone"]
    scfg = settings["splits"]
    emb = pd.Timedelta(minutes=scfg["embargo_minutes"])
    w_start = _local(settings["modelling_window"]["start"], tz)
    w_end = _local(settings["modelling_window"]["end"], tz) + pd.Timedelta(days=1)
    test_start = _local(scfg["test_start"], tz)
    rows = []
    month = pd.Period(scfg["first_validation_month"], freq="M")
    k = 1
    while (v_start := _local(month.start_time.strftime("%Y-%m-%d"), tz)) < test_start - emb:
        v_end = min(_local((month + 1).start_time.strftime("%Y-%m-%d"), tz), test_start - emb)
        name = f"cv{k:02d}"
        rows += [(name, "train", w_start, v_start - emb, str(month)), (name, "validation", v_start, v_end, str(month))]
        month += 1
        k += 1
    rows += [("final", "train", w_start, test_start - emb, None), ("final", "test", test_start, w_end - emb, None)]
    return pd.DataFrame(rows, columns=["fold", "role", "start", "end", "validation_month"])


def mask(ts: pd.DatetimeIndex, folds: pd.DataFrame, fold: str, role: str) -> pd.Series:
    r = folds[(folds["fold"] == fold) & (folds["role"] == role)]
    if r.empty:
        raise KeyError(f"no {role} period for fold {fold}")
    r = r.iloc[0]
    return pd.Series((ts >= r["start"]) & (ts < r["end"]), index=ts)


def assign_all(ts: pd.DatetimeIndex, folds: pd.DataFrame) -> pd.DataFrame:
    """Per sample: 'development' (usable for training/validation), 'test', or 'embargo'."""
    dev = mask(ts, folds, "final", "train")
    test = mask(ts, folds, "final", "test")
    split = pd.Series("embargo", index=ts)
    split[dev] = "development"
    split[test] = "test"
    return pd.DataFrame({"split": split}, index=pd.Index(ts, name="ts"))
