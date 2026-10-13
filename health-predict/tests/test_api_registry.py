import json

import numpy as np
import pandas as pd
import pytest

from src import registry, tracking

HAS_MODELS = registry.load_registry()["production"] != {}


# ---------------------------------------------------------------- registry

@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "model_root", lambda: tmp_path)
    monkeypatch.setattr(registry, "REGISTRY", str(tmp_path / "registry.json"))
    return tmp_path


def make_version(root, version, mae=None, conformal=True, test_start="2026-07-23"):
    d = root / "rf" / "30m" / version
    d.mkdir(parents=True)
    meta = {"model": "rf", "horizon": 30, "version": version, "status": "candidate"}
    if mae is not None:
        meta |= {"test_metrics": {"mae": mae}, "test_period": {"start_local": test_start}}
    (d / "metadata.json").write_text(json.dumps(meta))
    if conformal:
        (d / "conformal.json").write_text("{}")


def test_promotion_requires_holdout_evaluation(tmp_registry):
    make_version(tmp_registry, "v1")
    with pytest.raises(PermissionError, match="not been evaluated"):
        registry.promote("rf", 30, "v1", "r")


def test_promotion_requires_same_test_period_and_calibration(tmp_registry):
    make_version(tmp_registry, "v1", mae=0.9, test_start="2026-01-01")
    with pytest.raises(PermissionError, match="fixed test period"):
        registry.promote("rf", 30, "v1", "r")
    make_version(tmp_registry, "v2", mae=0.9, conformal=False)
    with pytest.raises(PermissionError, match="calibration"):
        registry.promote("rf", 30, "v2", "r")


def test_worse_candidate_is_not_promoted_without_force(tmp_registry):
    make_version(tmp_registry, "v1", mae=0.85)
    make_version(tmp_registry, "v2", mae=0.90)
    make_version(tmp_registry, "v3", mae=0.80)
    registry.promote("rf", 30, "v1", "first")
    with pytest.raises(PermissionError, match="worse"):
        registry.promote("rf", 30, "v2", "newer")
    with pytest.raises(PermissionError, match="reason"):
        registry.promote("rf", 30, "v2", "", force=True)
    registry.promote("rf", 30, "v3", "better")
    reg = registry.load_registry()
    assert reg["production"]["30"]["version"] == "v3"
    assert [h["version"] for h in reg["history"]] == ["v1", "v3"]
    assert registry.read_meta("rf", 30, "v1")["status"].startswith("retired")
    assert registry.read_meta("rf", 30, "v3")["status"] == "production"


def test_conformal_round_trip():
    from src import intervals as iv
    mc = iv.MondrianConformal([0.8], 10).fit(pd.Series(np.linspace(-1, 1, 200)), pd.Series(["a"] * 200),
                                             pd.Series(["stable"] * 200))
    back = iv.conformal_from_dict(json.loads(json.dumps(iv.conformal_to_dict(mc, {}))))
    a = mc.interval(np.array([5.0]), pd.Series(["a"]), pd.Series(["stable"]), 0.8)
    b = back.interval(np.array([5.0]), pd.Series(["a"]), pd.Series(["stable"]), 0.8)
    assert np.allclose(a, b)


def test_hypo_scores():
    rec, prec = tracking.hypo_scores([3.0, 3.5, 6.0, 7.0], [3.2, 5.0, 3.0, 7.0], 3.9)
    assert rec == 0.5 and prec == 0.5


# --------------------------------------------------------------------- API

def request_body(at="2026-09-21T18:00:00Z", hours=6, glucose=7.0, unit="mmol/L", bolus=True):
    t = pd.Timestamp(at)
    pts = [{"timestamp": (t - pd.Timedelta(minutes=m)).isoformat(), "glucose": glucose * (18.016 if unit == "mg/dL" else 1)}
           for m in range(5, hours * 60, 5)]
    ins = [{"timestamp": (t - pd.Timedelta(minutes=m)).isoformat(), "type": "basal_rate", "rate_u_per_h": 0.4}
           for m in range(30, hours * 60 + 60, 60)]
    if bolus:
        ins.append({"timestamp": (t - pd.Timedelta(minutes=90)).isoformat(), "type": "bolus", "units": 4.0})
    return {"timestamp": at, "glucose": glucose * (18.016 if unit == "mg/dL" else 1), "glucose_unit": unit,
            "recent_glucose": pts, "recent_insulin": ins}


@pytest.fixture(scope="module")
def client():
    if not HAS_MODELS:
        pytest.skip("no production models in models/registry.json")
    from fastapi.testclient import TestClient

    from src.api import app
    return TestClient(app)


def test_root_and_model_show_disclaimer(client):
    assert "not a medical device" in client.get("/").json()["disclaimer"]
    assert client.get("/health").json() == {"status": "ok"}
    assert set(client.get("/model").json()["production"]) == {"15", "30", "45", "60"}


def test_predict_returns_all_horizons_with_intervals(client):
    r = client.post("/predict", json=request_body())
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out["predictions"]) == {"15m", "30m", "45m", "60m"}
    for p in out["predictions"].values():
        assert p["lower_95"] <= p["lower"] <= p["value"] <= p["upper"] <= p["upper_95"]
        assert p["confidence_level"] == 0.8
    assert "not a medical device" in out["disclaimer"]
    assert not any("dose" in w.lower() and "recommend" in w.lower() for w in out["warnings"])


def test_units_are_converted(client):
    a = client.post("/predict", json=request_body()).json()["predictions"]
    b = client.post("/predict", json=request_body(unit="mg/dL")).json()["predictions"]
    assert a == b


def test_input_errors(client):
    body = request_body()
    body["timestamp"] = "2026-09-21T18:00:00"                       # no timezone
    assert client.post("/predict", json=body).status_code == 422
    body = request_body()
    body["recent_glucose"].append({"timestamp": "2026-09-21T19:00:00Z", "glucose": 6})   # after T
    assert client.post("/predict", json=body).status_code == 422
    body = request_body()
    body["glucose"] = None
    body["recent_glucose"] = [p for p in body["recent_glucose"]
                              if pd.Timestamp(p["timestamp"]) < pd.Timestamp("2026-09-21T17:30:00Z")]  # stale
    r = client.post("/predict", json=body)
    assert r.status_code == 422 and "no glucose reading" in r.json()["detail"]


def test_warnings_for_short_or_incomplete_history(client):
    out = client.post("/predict", json=request_body(hours=2, bolus=False)).json()
    text = " ".join(out["warnings"])
    assert "glucose history covers" in text and "basal history" in text and "no user bolus" in text


def test_dashboard_is_self_contained_and_served(client):
    import re

    from src import dashboard
    page = dashboard.build(pd.Timestamp("2026-09-21T18:00:00Z"))
    assert "not a medical device" in page
    assert page.count('class="plotly-graph-div"') == 4 and page.count('class="card"') == 4
    assert not re.findall(r'<(script|link)[^>]+(src|href)="https?://', page)   # works offline
    assert client.get("/dashboard").status_code in (200, 404)


def test_models_load_at_startup():
    if not HAS_MODELS:
        pytest.skip("no production models in models/registry.json")
    from fastapi.testclient import TestClient

    from src import serving
    from src.api import app
    serving.production_models.cache_clear()
    with TestClient(app):                      # runs the lifespan startup
        assert serving.production_models.cache_info().currsize == 1


def test_without_models_the_server_starts_and_answers_503(monkeypatch):
    from fastapi.testclient import TestClient

    from src import serving
    from src.api import app

    def missing():
        raise FileNotFoundError("models/registry.json")
    monkeypatch.setattr(serving, "production_models", missing)
    with TestClient(app) as c:                 # startup must not fail
        r = c.get("/health")
        assert r.status_code == 503 and "models not available" in r.json()["detail"]
        assert c.post("/predict", json=request_body()).status_code == 503
        assert "not a medical device" in c.get("/").json()["disclaimer"]
