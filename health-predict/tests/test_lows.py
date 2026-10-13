import numpy as np
import pandas as pd
import pytest

from src import lows

T0 = pd.Timestamp("2026-08-01 00:00", tz="UTC")


def at(m):
    return T0 + pd.Timedelta(minutes=m)


def test_threshold_reaches_target_sensitivity():
    scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.1, 0.2])
    y = np.array([1, 1, 1, 1, 1, 0, 0])
    t = lows.threshold_for(scores, y, 0.8)
    assert t == 0.6 and ((scores >= t) & (y == 1)).sum() / y.sum() >= 0.8


def test_low_onsets_merge_episodes():
    cgm = pd.DataFrame({"measured_at": [at(m) for m in (0, 5, 10, 15, 20, 90, 95)],
                        "glucose_mmol": [5, 3.5, 3.4, 4.5, 3.6, 3.2, 3.3]})
    on = lows.low_onsets(cgm, 3.9, quiet_minutes=30)
    assert list(on) == [at(5), at(90)]          # 3.6 at +20 is the same episode (within 30 min)


def test_alert_episodes_and_operating_point():
    idx = pd.DatetimeIndex([at(m) for m in range(0, 120, 5)])
    alert = np.zeros(len(idx), bool)
    alert[[2, 3, 4]] = True                      # one episode at 10–20 min
    alert[[20]] = True                           # a second, false, episode at 100 min
    y = np.zeros(len(idx), int)
    y[[3, 4, 5]] = 1                             # a low follows these times
    frame = pd.DataFrame({"y": y, "alert": alert}, index=idx)
    eps = lows.alert_episodes(idx, alert)
    assert len(eps) == 2
    op = lows.operating_point(frame, pd.DatetimeIndex([at(40)]), days=1.0)
    assert op["sensitivity"] == pytest.approx(2 / 3) and op["precision"] == pytest.approx(2 / 4)
    assert op["false alert episodes / day"] == 1 and op["low episodes caught"] == 1.0
    assert op["median warning (min)"] == 30.0   # first alert at 10 min, low starts at 40
