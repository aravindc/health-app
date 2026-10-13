from datetime import date

import pandas as pd
import pytest

from src import holdout, registry, roll
from tests.test_api_registry import HAS_MODELS, make_version, tmp_registry  # noqa: F401  (fixture)


def test_roll_needs_never_evaluated_data():
    with pytest.raises(roll.RollError, match="needs data through 2026-12-01"):
        roll.plan_roll(date(2026, 10, 6), date(2026, 7, 23), date(2026, 11, 20), 8, date(2026, 7, 22))


def test_roll_plan_dates():
    p = roll.plan_roll(date(2026, 10, 6), date(2026, 7, 23), date(2026, 12, 15), 8, date(2026, 7, 22))
    assert p.new_end == date(2026, 12, 15) and p.new_test_start == date(2026, 10, 21)
    assert (p.new_end - p.new_test_start).days + 1 == 56


def test_roll_flags_overlap_with_production_training():
    assert roll.plan_roll(date(2026, 10, 6), date(2026, 7, 23), date(2026, 12, 15), 8, date(2026, 11, 1)
                          ).production_overlap
    assert not roll.plan_roll(date(2026, 10, 6), date(2026, 7, 23), date(2026, 12, 15), 8, date(2026, 7, 22)
                              ).production_overlap


def test_apply_to_settings_keeps_comments(tmp_path):
    f = tmp_path / "settings.yaml"
    f.write_text('modelling_window:\n  start: "2025-05-19"\n  end: "2026-10-06"   # inclusive\n'
                 'splits:\n  test_start: "2026-07-23"  # fixed\n')
    roll.apply_to_settings(f, roll.RollPlan(date(2026, 10, 6), date(2026, 7, 23), date(2026, 12, 15),
                                            date(2026, 10, 21)))
    t = f.read_text()
    assert 'end: "2026-12-15"   # inclusive' in t and 'test_start: "2026-10-21"  # fixed' in t
    assert 'start: "2025-05-19"' in t


def test_promotion_requires_production_scored_on_same_period(tmp_registry):  # noqa: F811
    make_version(tmp_registry, "v1", mae=0.85, test_start="2026-07-23")
    registry.promote("rf", 30, "v1", "first")
    meta = registry.read_meta("rf", 30, "v1")
    meta["test_period"]["start_local"] = "2026-01-01"          # production last scored on an older period
    registry.write_meta("rf", 30, "v1", meta)
    make_version(tmp_registry, "v2", mae=0.80)
    with pytest.raises(PermissionError, match="has not been scored on the current test period"):
        registry.promote("rf", 30, "v2", "newer")


def test_single_use_rule_is_per_test_period(settings):
    from src import splits
    current = splits.make_folds(settings).query("role == 'test'").iloc[0]["start"]
    runs = pd.DataFrame({"run_at": [pd.Timestamp("2026-10-08", tz="UTC")] * 3,
                         "purpose": ["selection", "candidate", "selection"],
                         "test_start": [current, current, current - pd.Timedelta(days=90)]})
    assert len(holdout.selection_runs_for_current_period(runs)) == 1


@pytest.mark.skipif(not HAS_MODELS, reason="no production models")
def test_reload_endpoint_is_local_only():
    from fastapi.testclient import TestClient

    from src.api import app
    r = TestClient(app).post("/admin/reload")
    assert r.status_code == 200 and r.json()["reloaded"]
    r = TestClient(app, client=("203.0.113.5", 5000)).post("/admin/reload")
    assert r.status_code == 403
