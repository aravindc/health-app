import numpy as np
import pandas as pd
import pytest

from src import sequences
from tests import synth
from tests.synth import T0
from tests.test_leakage import scenario


def at(m):
    return T0 + pd.Timedelta(minutes=m)


def build(settings, cgm, boluses=None, segments=None, start=0, end=200, lookback=60, as_of=None, carbs=False):
    boluses = synth.boluses() if boluses is None else boluses
    segments = synth.steady_basal(0.6) if segments is None else segments
    return sequences.build_sequences(cgm, boluses, segments, settings, at(start), at(end), lookback, carbs,
                                     as_of=None if as_of is None else at(as_of))


def test_shape_and_channels(settings):
    ts, X, names = build(settings, synth.regular_cgm(-120, 200, lambda m: 6.0))
    assert names == sequences.BASE_CHANNELS
    assert X.shape == (len(ts), 12, len(names)) and X.dtype == np.float32
    assert ts[0] == at(0)


def test_window_is_oldest_first_and_ends_at_t(settings):
    ts, X, names = build(settings, synth.regular_cgm(-120, 200, lambda m: 5 + m / 60))
    g = X[list(ts).index(at(100)), :, names.index("glucose")]
    assert g[-1] == pytest.approx(5 + 100 / 60)            # last step = slot T
    assert g[0] == pytest.approx(5 + 45 / 60)              # first step = slot T-55
    v = X[list(ts).index(at(100)), :, names.index("velocity")]
    assert v[-1] == pytest.approx(1.0)                     # 1 mmol/L per hour


def test_late_reading_absent_until_it_arrives(settings):
    pts = [(m, 6.0) for m in range(-120, 200, 5) if m != 100] + [(100, 9.0, 20)]  # slot 100 arrives 20 min late
    ts, X, names = build(settings, synth.cgm(pts))
    gi, oi = names.index("glucose"), names.index("glucose_observed")
    row = X[list(ts).index(at(100))]
    assert row[-1, gi] == 6.0 and row[-1, oi] == 0         # not known yet: filled from the previous step
    row = X[list(ts).index(at(120))]
    assert row[-5, gi] == 9.0 and row[-5, oi] == 1         # known once it arrived


def test_insulin_channels(settings):
    b = synth.boluses([{"at": 50, "units": 3.0}])          # completes at 52 -> step ending at 55
    ts, X, names = build(settings, synth.regular_cgm(-120, 200, lambda m: 6.0), boluses=b)
    row = X[list(ts).index(at(60))]
    assert row[-2, names.index("bolus_insulin")] == pytest.approx(3.0)
    assert row[-1, names.index("basal_rate")] == pytest.approx(0.6)
    assert row[-1, names.index("estimated_iob")] > 2.9


def test_sequences_match_as_of_rebuild(settings):
    cgm, b, seg = scenario()
    ts, X, _ = sequences.build_sequences(cgm, b, seg, settings, T0, T0 + pd.Timedelta(hours=24), 120, True)
    for t in ts[60::13]:
        _, Xa, _ = sequences.build_sequences(cgm, b, seg, settings, t, t + pd.Timedelta(minutes=1), 120, True,
                                             as_of=t)
        np.testing.assert_allclose(Xa[0], X[list(ts).index(t)], rtol=1e-6, atol=1e-6, equal_nan=True)
