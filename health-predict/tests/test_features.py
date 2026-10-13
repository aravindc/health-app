import numpy as np
import pandas as pd
import pytest

from src import features as F
from tests import synth
from tests.synth import T0

MIN = pd.Timedelta(minutes=1)


def at(minutes):
    return T0 + pd.Timedelta(minutes=minutes)


def build(settings, cgm, boluses=None, segments=None, start=0, end=600, as_of=None):
    boluses = synth.boluses() if boluses is None else boluses
    segments = synth.steady_basal(0.5) if segments is None else segments
    f, cat = F.build_features(cgm, boluses, segments, settings, at(start), at(end),
                              as_of=None if as_of is None else at(as_of))
    return f, cat


# ---------------------------------------------------------------- grid

def test_grid_layers_keep_every_reading_in_a_slot():
    c = synth.cgm([(10, 5.0), (10.5, 6.0, 30)])  # two readings in slot 10; the later one arrives 30 min late
    slots = F.make_slots(at(0), at(60), 5)
    g = F.Grid.from_cgm(c, slots, 5)
    assert g.value.shape[0] == 2
    v, _, _, layer = g.known(0)
    i10 = 2  # slot index of T0+10
    assert v[i10] == 5.0 and layer[i10] == 1          # late reading not yet available at T+10
    i45 = 9
    v45, _, _, _ = g.known(7)                          # slot 10 seen from T+45: late reading has arrived
    assert v45[i45] == 6.0


# ------------------------------------------------------------- glucose

def test_current_glucose_respects_availability(settings):
    c = synth.cgm([(50, 5.0), (55, 6.0), (60, 7.0, 20)])  # reading for slot 60 arrives 20 min late
    f, _ = build(settings, c, start=0, end=120)
    assert f.loc[at(60), "current_glucose"] == 6.0
    assert f.loc[at(80), "glucose_lag_20m"] == 7.0       # known once it arrived


def test_current_glucose_is_missing_when_stale(settings):
    c = synth.cgm([(0, 5.0)])
    f, _ = build(settings, c, start=0, end=60)
    assert f.loc[at(10), "current_glucose"] == 5.0
    assert f.loc[at(10), "minutes_since_last_reading"] == pytest.approx(10.8)
    assert np.isnan(f.loc[at(15), "current_glucose"])     # 15.8 min old > 15 min


def test_lag_falls_back_to_older_reading_never_newer(settings):
    pts = [(m, m / 10) for m in range(0, 65, 5) if m != 30]
    f, _ = build(settings, synth.cgm(pts), start=0, end=120)
    assert f.loc[at(60), "glucose_lag_30m"] == 2.5       # slot 30 missing -> slot 25, not slot 35
    assert f.loc[at(60), "glucose_lag_20m"] == 4.0


def test_rolling_statistics_and_slope(settings):
    c = synth.regular_cgm(0, 400, lambda m: 5 + m / 30)  # +2 mmol/L per hour
    f, _ = build(settings, c, start=0, end=400)
    row = f.loc[at(300)]
    window = [5 + m / 30 for m in range(245, 305, 5)]   # slots in (T-60, T]
    assert row["glucose_mean_60m"] == pytest.approx(np.mean(window))
    assert row["glucose_min_60m"] == pytest.approx(min(window))
    assert row["glucose_range_60m"] == pytest.approx(max(window) - min(window))
    assert row["glucose_slope_60m"] == pytest.approx(2.0)
    assert row["glucose_slope_180m"] == pytest.approx(2.0)


def test_rolling_needs_enough_readings(settings):
    c = synth.cgm([(0, 5.0), (60, 6.0)])
    f, _ = build(settings, c, start=0, end=120)
    assert np.isnan(f.loc[at(60), "glucose_mean_60m"])  # 2 of 12 slots


def test_velocity_and_acceleration(settings):
    lin, _ = build(settings, synth.regular_cgm(0, 400, lambda m: 5 + m / 30), start=0, end=400)
    r = lin.loc[at(300)]
    assert r["velocity_5m_mmol_per_h"] == pytest.approx(2.0)
    assert r["velocity_15m_mgdl_per_min"] == pytest.approx(2.0 / 60 * settings["units"]["mgdl_per_mmol"])
    assert r["glucose_change_30m"] == pytest.approx(1.0)
    assert r["acceleration_mmol_per_h2"] == pytest.approx(0.0, abs=1e-9)
    quad, _ = build(settings, synth.regular_cgm(0, 400, lambda m: 5 + (m / 60) ** 2), start=0, end=400)
    assert quad.loc[at(300), "acceleration_mmol_per_h2"] == pytest.approx(2.0, rel=1e-6)


def test_time_features_use_local_time(settings):
    f, _ = build(settings, synth.cgm([(0, 5.0)]), start=0, end=10)
    assert f.loc[at(0), "hour"] == 1                    # 00:00 UTC = 01:00 BST
    assert f.loc[at(0), "day_of_week"] == 0             # Monday
    assert f.loc[at(0), "sin_hour"] == pytest.approx(np.sin(2 * np.pi / 24))


# ------------------------------------------------------------- insulin

def test_bolus_counted_from_completion_in_trailing_windows(settings):
    b = synth.boluses([{"at": 10, "units": 4.0}])     # completes at 12
    f, _ = build(settings, synth.cgm([(0, 5)]), boluses=b, start=0, end=120)
    assert f.loc[at(10), "bolus_insulin_last_30m"] == 0
    assert f.loc[at(15), "insulin_last_15m"] == pytest.approx(4.0 + 0.5 * 15 / 60)
    assert f.loc[at(40), "bolus_insulin_last_30m"] == pytest.approx(4.0)
    assert f.loc[at(45), "bolus_insulin_last_30m"] == 0  # (15, 45] no longer contains minute 12


def test_basal_units_and_insulin_known(settings):
    seg = synth.steady_basal(1.2, start_min=0)
    f, _ = build(settings, synth.cgm([(0, 5)]), segments=seg, start=0, end=400)
    assert f.loc[at(120), "basal_insulin_last_60m"] == pytest.approx(1.2)
    assert f.loc[at(120), "basal_rate_now"] == 1.2
    assert not f.loc[at(200), "insulin_known"]          # lookback reaches before the first basal record
    assert f.loc[at(240), "insulin_known"]


def test_basal_gap_makes_insulin_unknown(settings):
    seg = synth.segments([(-600, 1.0), (-400, 1.0), (-200, 1.0), (0, 1.0), (150, 1.0), (300, 1.0),  # 300 -> 700:
                          (700, 1.0), (900, 1.0), (1100, 1.0)], end_min=1300)                     # gap (> 6 h)
    f, _ = build(settings, synth.cgm([(0, 5)]), segments=seg, start=0, end=1200)
    assert f.loc[at(290), "insulin_known"]
    assert not f.loc[at(500), "insulin_known"]
    assert not f.loc[at(930), "insulin_known"]          # gap still inside the 240-min lookback
    assert f.loc[at(945), "insulin_known"]


def test_extended_bolus_uses_programmed_rate_and_stops_on_cancel(settings):
    full = synth.boluses([{"at": 0, "units": 3.0, "shape": "extended", "pct": 0.0, "planned": 60}])
    f, _ = build(settings, synth.cgm([(0, 5)]), boluses=full, segments=synth.steady_basal(0))
    assert f.loc[at(30), "bolus_insulin_last_30m"] == pytest.approx(1.5)
    assert f.loc[at(90), "bolus_insulin_last_120m"] == pytest.approx(3.0)
    cancelled = synth.boluses([{"at": 0, "units": 3.0, "shape": "extended", "pct": 0.0, "planned": 60, "took": 30,
                                "delivered": 1.5}])
    f2, _ = build(settings, synth.cgm([(0, 5)]), boluses=cancelled, segments=synth.steady_basal(0))
    assert f2.loc[at(90), "bolus_insulin_last_120m"] == pytest.approx(1.5)


def test_minutes_since_last_bolus_ignores_automated_corrections(settings):
    b = synth.boluses([{"at": 10, "units": 4.0}, {"at": 50, "units": 0.3, "type": "automated_correction"}])
    f, _ = build(settings, synth.cgm([(0, 5)]), boluses=b, start=0, end=120)
    assert f.loc[at(60), "minutes_since_last_bolus"] == pytest.approx(48)
    assert f.loc[at(60), "last_bolus_amount"] == 4.0
    assert f.loc[at(60), "automated_correction_last_60m"] == pytest.approx(0.3)
    assert f.loc[at(5), "minutes_since_last_bolus"] == settings["features"]["minutes_since_cap"]


def test_estimated_iob_rises_then_decays(settings):
    b = synth.boluses([{"at": 10, "units": 4.0}])
    seg = synth.steady_basal(0)
    f, _ = build(settings, synth.cgm([(0, 5)]), boluses=b, segments=seg, start=0, end=400)
    iob = f["estimated_iob"]
    assert iob[at(10)] == 0
    assert 3.9 < iob[at(15)] <= 4.0
    assert iob[at(60)] < iob[at(15)] and iob[at(240)] < iob[at(60)]
    assert iob[at(320)] == pytest.approx(0, abs=1e-9)   # after 5 h


def test_carb_features(settings):
    b = synth.boluses([{"at": 10, "units": 5.0, "carbs": 60.0}])
    f, _ = build(settings, synth.cgm([(0, 5)]), boluses=b, start=0, end=120)
    assert f.loc[at(30), "carbs_last_30m"] == 60
    assert f.loc[at(30), "minutes_since_carbs"] == 20
    assert f.loc[at(10), "carbs_last_30m"] == 0         # entered at 10 -> counted from slot 15


def test_catalog_covers_every_feature(settings):
    f, cat = build(settings, synth.cgm([(0, 5)]), start=0, end=10)
    inputs = set(f.columns) - set(F.META_COLUMNS)
    assert inputs <= set(cat["feature"])


def test_rate_needs_minimum_spacing(settings):
    c = synth.cgm([(50, 5.0), (51, 6.0)])  # current (slot 55) and lag-5 (slot 50) readings only 1 min apart
    f, _ = build(settings, c, start=0, end=120)
    assert np.isnan(f.loc[at(55), "velocity_5m_mmol_per_h"])


def test_day_of_year_is_not_a_model_feature_group(settings):
    _, cat = build(settings, synth.cgm([(0, 5)]), start=0, end=10)
    groups = dict(zip(cat["feature"], cat["group"]))
    assert groups["day_of_year"] == groups["sin_day_of_year"] == "calendar"
    assert groups["hour"] == "time"
