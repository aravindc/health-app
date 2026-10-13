import copy
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src import config, monitor, refresh, registry
from tests.test_api_registry import make_version, tmp_registry  # noqa: F401  (fixture)


def preds(days: int, horizon=30, role="production", model="rf", version="v1", noise=0.5, seed=0, start=date(2026, 9, 1)):
    """Synthetic monitor rows: 288 per day, errors ~ N(0, noise)."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=288 * days, freq="5min")
    y = 7 + rng.normal(0, 2, len(ts))
    p = pd.DataFrame({"ts": ts, "horizon": horizon, "role": role, "model": model, "version": version, "y_true": y,
                      "y_pred": y + rng.normal(0, noise, len(ts)), "persistence": y + rng.normal(0, 2 * noise, len(ts)),
                      "grange": "7.0–10.0", "direction": "stable"})
    p["day"] = p["ts"].dt.date
    for k in (80, 95):
        p[f"lo{k}"], p[f"hi{k}"] = p["y_pred"] - 0.1, p["y_pred"] + 0.1   # static: far too narrow
    return p


@pytest.fixture
def s():
    s = copy.deepcopy(config.settings())
    s["monitoring"] |= {"recalibration_window_days": 10, "recalibration_min_days": 5, "min_day_samples": 200,
                        "drift_window_days": 3}
    return s


def test_scored_from_is_after_training_end_and_embargo(settings):
    meta = {"training_period": {"end": "2026-07-22 21:55:00+00:00"}}
    assert monitor.scored_from(meta, settings) == pd.Timestamp("2026-07-22 23:00", tz="UTC")


def test_rolling_intervals_use_only_earlier_days(s):
    mcfg = config.models()
    p = preds(12)
    last = p["day"] == p["day"].max()
    p.loc[last, "y_true"] += 50                                   # a wild day must not widen its own interval
    r = monitor.add_rolling_intervals(p, s, mcfg)
    assert r.loc[r["day"] < date(2026, 9, 6), "rlo80"].isna().all()   # fewer than 5 earlier days
    width = (r["rhi80"] - r["rlo80"]).groupby(r["day"]).mean()
    assert width.iloc[-1] < 2 and width.iloc[-1] == pytest.approx(width.iloc[-2], rel=0.2)


def test_recalibration_written_when_better_and_removed_when_worse(tmp_registry, s):  # noqa: F811
    mcfg = config.models()
    make_version(tmp_registry, "v1")
    live = tmp_registry / "rf" / "30m" / "v1" / registry.CONF_LIVE
    entry = {"role": "production", "model": "rf", "version": "v1", "horizon": 30}
    p = monitor.add_rolling_intervals(preds(12), s, mcfg)
    rec = monitor.recalibrate(p, entry, s, mcfg)
    assert rec["applied"] and live.exists() and rec["rolling_error"] < rec["static_error"]
    assert json.loads(live.read_text())["categories"]["calibration_samples"] == rec["samples"]
    q = p.copy()
    for k in (80, 95):                                            # static now exact, rolling made useless
        q[f"lo{k}"], q[f"hi{k}"] = q["y_pred"] - 100, q["y_pred"] + 100
        q[f"rlo{k}"] = q[f"rhi{k}"] = q["y_pred"]
    for k, L in ((80, 0.8), (95, 0.95)):
        half = np.quantile(np.abs(q["y_true"] - q["y_pred"]), L)
        q[f"lo{k}"], q[f"hi{k}"] = q["y_pred"] - half, q["y_pred"] + half
    rec = monitor.recalibrate(q, entry, s, mcfg)
    assert not rec["applied"] and not live.exists()


def test_drift_alert_on_worse_skill_and_low_coverage(s):
    rows = [{"role": "production", "model": "rf", "version": "v1", "horizon": 30, "day": date(2026, 9, 1) + timedelta(d),
             "n": 288, "mae": 0.8 if d < 10 else 1.2, "persistence_mae": 1.0, "coverage80_static": 0.8 if d < 10 else 0.6,
             "coverage80_rolling": np.nan} for d in range(13)]
    alerts = monitor.drift_alerts(pd.DataFrame(rows), s)
    assert any("drifted" in a for a in alerts) and any("covered 60%" in a for a in alerts)
    rows = [r | {"mae": 0.8, "coverage80_static": 0.8} for r in rows]
    assert monitor.drift_alerts(pd.DataFrame(rows), s) == []


def test_paired_comparison_uses_identical_samples():
    prod = preds(5, version="v1", noise=0.6)
    sh = preds(5, role="shadow", version="v2", noise=0.4, seed=1).iloc[288:]   # shadow starts a day later
    err = sh["y_pred"] - sh["y_true"]
    sh["y_true"] = prod["y_true"].iloc[288:].to_numpy()
    sh["y_pred"] = sh["y_true"] + err
    d = refresh.paired(pd.concat([prod, sh]), 30, {"model": "rf", "version": "v1"}, {"model": "rf", "version": "v2"},
                       200)
    assert len(d) == 4 and (d["n"] == 288).all() and (d["mae_shadow"] < d["mae_prod"]).all()


def shadow_comparison(prod_version, days, mae, prod_mae):
    return {"production": {"model": "rf", "version": prod_version}, "days": days, "mae": mae,
            "production_mae": prod_mae, "n": days * 288}


def test_shadow_promotion_rules(tmp_registry):  # noqa: F811
    make_version(tmp_registry, "v1", mae=0.85)
    registry.promote("rf", 30, "v1", "first")
    make_version(tmp_registry, "v2")
    reg = registry.load_registry()
    reg["shadow"]["30"] = {"model": "rf", "version": "v2"}
    registry.save_registry(reg)

    def with_sc(sc):
        meta = registry.read_meta("rf", 30, "v2")
        meta["shadow_comparison"] = sc
        registry.write_meta("rf", 30, "v2", meta)

    with_sc(shadow_comparison("v0", 20, 0.8, 0.85))
    with pytest.raises(PermissionError, match="production is now rf/v1"):
        registry.promote("rf", 30, "v2", "r")
    with_sc(shadow_comparison("v1", 5, 0.8, 0.85))
    with pytest.raises(PermissionError, match="paired shadow days"):
        registry.promote("rf", 30, "v2", "r")
    with_sc(shadow_comparison("v1", 20, 0.9, 0.85))
    with pytest.raises(PermissionError, match="worse than production"):
        registry.promote("rf", 30, "v2", "r")
    with_sc(shadow_comparison("v1", 20, 0.8, 0.85))
    e = registry.promote("rf", 30, "v2", "monthly refresh")
    reg = registry.load_registry()
    assert e["evidence"] == "shadow" and reg["production"]["30"]["version"] == "v2" and reg["shadow"] == {}
    assert registry.read_meta("rf", 30, "v1")["status"].startswith("retired")


def test_load_prefers_live_calibration(tmp_registry, monkeypatch):  # noqa: F811
    make_version(tmp_registry, "v1")
    d = tmp_registry / "rf" / "30m" / "v1"
    monkeypatch.setattr(registry.joblib, "load", lambda path: "est")
    seen = []
    monkeypatch.setattr(registry.iv, "conformal_from_dict", lambda x: seen.append(x) or x)
    registry.load("rf", 30, "v1")
    (d / registry.CONF_LIVE).write_text('{"live": true}')
    registry.load("rf", 30, "v1")
    assert seen == [{}, {"live": True}]
