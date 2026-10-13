import pandas as pd
import pytest

from src import validation as v
from tests.conftest import T0

THR = {"very_low": 3.0, "low": 3.9, "high": 10.0, "very_high": 13.9}


def test_range_boundaries():
    g = pd.Series([2.9, 3.0, 3.89, 3.9, 10.0, 10.01, 13.9, 13.91])
    assert list(v.classify_ranges(g, THR)) == [
        "very_low", "low", "low", "target", "target", "high", "high", "very_high"]


def test_time_in_ranges_sums_to_100():
    tir = v.time_in_ranges(pd.Series([2.5, 5, 6, 7, 12, 15]), THR)
    assert sum(tir.values()) == pytest.approx(100)
    assert tir["target"] == pytest.approx(50)


def readings(minutes):
    return pd.DataFrame({"measured_at": [T0 + pd.Timedelta(minutes=m) for m in minutes],
                         "glucose_mmol": 6.0, "censored": None})


def test_cgm_metrics_full_coverage(settings):
    m = v.cgm_metrics(readings(range(0, 60, 5)), settings, T0, T0 + pd.Timedelta(hours=1))
    assert m["expected_slots"] == 12 and m["filled_slots"] == 12
    assert m["coverage_pct"] == pytest.approx(100)
    assert m["n_gaps"] == 0 and m["cv_pct"] == pytest.approx(0)


def test_cgm_metrics_detects_gaps(settings):
    mins = [0, 5, 10, 35, 40, 45, 50, 55]  # 25-minute gap
    m = v.cgm_metrics(readings(mins), settings, T0, T0 + pd.Timedelta(hours=1))
    assert m["missing_slots"] == 4
    assert m["n_gaps"] == 1 and m["longest_gap_hours"] == pytest.approx(25 / 60)


def test_hourly_insulin_uses_local_dates():
    # 23:30 UTC in June is 00:30 next day in London.
    t = pd.Timestamp("2026-06-01 23:30", tz="UTC")
    d = pd.DataFrame({"start_at": [t], "end_at": [t], "units": [2.0], "delivery_type": ["bolus"]})
    h = v.hourly_insulin(d, "Europe/London")
    assert str(h.loc[0, "date"]) == "2026-06-02" and h.loc[0, "hour"] == 0


def test_insulin_daily_marks_gap_days_incomplete():
    tz = "Europe/London"
    day = pd.Timestamp("2026-06-02 00:00", tz=tz).tz_convert("UTC")
    deliveries = pd.DataFrame({"start_at": [day + pd.Timedelta(hours=1)], "end_at": [day + pd.Timedelta(hours=2)],
                               "units": [0.5], "delivery_type": ["basal"]})
    segments = pd.DataFrame({"start_at": [day + pd.Timedelta(hours=3)], "end_at": [day + pd.Timedelta(hours=11)],
                             "duration_minutes": [480.0], "is_gap": [True]})
    daily = v.insulin_daily(v.hourly_insulin(deliveries, tz), segments, tz, "2026-06-01", "2026-06-03")
    assert list(daily["complete"]) == [True, False, True]
    assert daily.loc[pd.Timestamp("2026-06-02").date(), "basal_unknown_hours"] == pytest.approx(8)
    assert daily["total"].sum() == pytest.approx(0.5)
