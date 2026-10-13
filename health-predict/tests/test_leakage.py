"""Features at T must equal features built from only the data known at T."""

import numpy as np
import pandas as pd

from src import features as F
from tests import synth
from tests.synth import T0


def scenario():
    rng = np.random.default_rng(0)
    pts = []
    for m in range(-400, 1440, 5):
        late = 25 if rng.random() < 0.15 else 0          # 15% of readings are late backfills
        if rng.random() < 0.05:
            continue                                     # 5% missing
        pts.append((m, 6 + 2 * np.sin(m / 90) + rng.normal(0, 0.2), late))
    pts.append((602.5, 9.0, 0))                          # second reading inside an occupied slot
    cgm = synth.cgm(pts)
    b = synth.boluses([
        {"at": 100, "units": 4.0, "carbs": 50},
        {"at": 300, "units": 3.0, "shape": "extended", "pct": 40.0, "planned": 60},
        {"at": 500, "units": 3.0, "shape": "extended", "pct": 0.0, "planned": 90, "took": 40, "delivered": 1.3},
        {"at": 700, "units": 0.4, "type": "automated_correction"},
        {"at": 900, "units": 2.0, "type": "manual_correction"},
    ])
    changes = [(m, 0.5) for m in range(-720, 120, 120)] + [(120, 0.0), (150, 0.9), (400, 0.6), (600, 0.6),
                                                          (800, 0.7), (1000, 0.4), (1200, 0.4), (1400, 0.5)]
    seg = synth.segments(changes, end_min=1500)
    return cgm, b, seg


def compare_at(settings, cgm, b, seg, full, times, as_of_cgm=None):
    cols = [c for c in full.columns if c != "insulin_known"]
    bad = {}
    for t in times:
        part, _ = F.build_features(as_of_cgm if as_of_cgm is not None else cgm, b, seg, settings, t,
                                   t + pd.Timedelta(minutes=1), as_of=t)
        diff = [c for c in cols if not F._same(full.loc[t, c], part.loc[t, c])]
        if diff:
            bad[t] = diff
    return bad


def test_features_match_as_of_rebuild(settings):
    cgm, b, seg = scenario()
    full, _ = F.build_features(cgm, b, seg, settings, T0, T0 + pd.Timedelta(hours=24))
    times = full.index[full["insulin_known"]][::7]
    assert len(times) > 30
    assert compare_at(settings, cgm, b, seg, full, times) == {}


def test_audit_detects_a_planted_leak(settings):
    """Negative control: pretend late readings were available immediately in the full build."""
    cgm, b, seg = scenario()
    leaky = cgm.assign(available_at=cgm["measured_at"])
    full, _ = F.build_features(leaky, b, seg, settings, T0, T0 + pd.Timedelta(hours=24))
    late = cgm[cgm["available_at"] > cgm["measured_at"]]
    times = pd.DatetimeIndex(late["measured_at"].dt.ceil("5min")).intersection(full.index)[:10]
    bad = compare_at(settings, leaky, b, seg, full, times, as_of_cgm=cgm)
    assert len(bad) >= 5 and all("current_glucose" in v or "glucose_mean_15m" in v for v in bad.values())
