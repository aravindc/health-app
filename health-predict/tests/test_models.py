from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from src import baselines, evaluate, models, splits, train
from src.metrics import regression_metrics


def test_persistence_and_linear_trend():
    X = pd.DataFrame({"current_glucose": [6.0, 6.0, 6.0], "v15": [2.0, np.nan, np.nan], "v5": [9.0, 4.0, np.nan]})
    assert list(baselines.persistence(X, 30)) == [6.0, 6.0, 6.0]
    assert list(baselines.linear_trend(X, 30, ["v15", "v5"])) == [7.0, 8.0, 6.0]  # first available, else flat


def test_metrics():
    m = regression_metrics([5, 10, np.nan], [6, 8, 1])
    assert m["n"] == 2
    assert m["mae"] == pytest.approx(1.5) and m["rmse"] == pytest.approx(np.sqrt(2.5))
    assert m["bias"] == pytest.approx(-0.5) and m["mape_pct"] == pytest.approx((0.2 + 0.2) / 2 * 100)


def test_quantile_clipper_uses_training_limits():
    c = models.QuantileClipper((0.0, 1.0)).fit(np.array([[1.0], [2.0], [3.0]]))
    assert c.transform(np.array([[10.0], [-5.0]])).ravel().tolist() == [3.0, 1.0]


def test_learned_models_fit_and_handle_missing_values():
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(300, 3)), columns=list("abc"))
    X.iloc[::7, 1] = np.nan
    y = X["a"].fillna(0) * 2 + rng.normal(0, 0.1, 300)
    for name in models.LEARNED:
        params = {"n_estimators": 20} if name != "linear" else {"alpha": 1.0}
        if name == "lightgbm":
            params["verbose"] = -1
        est = models.make_model(name, params, 0).fit(X, y)
        assert np.isfinite(est.predict(X)).all()


class Spy:
    """Records the timestamps it is trained on."""
    seen: ClassVar[list] = []

    def fit(self, X, y):
        Spy.seen.append(X.index)
        return self

    def predict(self, X):
        return np.zeros(len(X))


def test_cv_never_trains_on_validation_or_embargo(settings, monkeypatch):
    folds = splits.make_folds(settings)
    ts = pd.date_range(folds["start"].min(), folds["end"].max(), freq="5min", tz="UTC")
    df = pd.DataFrame({"current_glucose": 6.0, "glucose_30m": 6.5, "delta_30m": 0.5, "insulin_known": True,
                       "target_30m_censored": None}, index=ts)
    catalog = pd.DataFrame({"feature": ["current_glucose"], "group": ["current"]})
    mcfg = {"feature_sets": {"fs": ["current"]}, "sample_filter": {"require_insulin_known": True},
            "models": {"linear": {}}}
    Spy.seen = []
    monkeypatch.setattr(models, "make_model", lambda *a, **k: Spy())
    preds = train.run_cv(df, folds, catalog, "linear", 30, "delta", "fs", settings, mcfg)
    emb = pd.Timedelta(minutes=settings["splits"]["embargo_minutes"])
    val = folds[folds["role"] == "validation"].set_index("fold")
    assert len(Spy.seen) == len(val)
    for fold, seen in zip(sorted(val.index), Spy.seen):
        assert seen.max() < val.loc[fold, "start"] - emb + pd.Timedelta(minutes=5)
    test_start = folds[folds["role"] == "test"]["start"].iloc[0]
    assert preds["ts"].max() < test_start                     # test period never predicted in CV
    assert (preds["y_pred"] == 6.0).all()                     # delta prediction (0) + current glucose


def test_selection_requires_material_and_consistent_improvement():
    rows = []
    for i, f in enumerate([f"cv{k:02d}" for k in range(1, 9)]):
        rows.append(("persistence", "-", "-", 30, f, 1.00))
        rows.append(("linear_trend", "-", "-", 30, f, 1.20))
        rows.append(("linear", "delta", "fs", 30, f, 0.97))                  # 3% better: not material
        rows.append(("lightgbm", "delta", "fs", 30, f, 0.90 if i < 7 else 1.01))  # 10% better, wins 7/8
    folds = pd.DataFrame(rows, columns=evaluate.KEYS + ["horizon", "fold", "mae"])
    pool = folds.groupby(evaluate.KEYS + ["horizon"], as_index=False)["mae"].mean()
    chosen, log = evaluate.select(pool, folds, 30, "fs", {"min_relative_improvement": 0.05, "min_fold_win_share": 0.75})
    assert chosen == ("lightgbm", "delta", "fs")
    assert [r[-1] for r in log] == ["not adopted", "adopted"]
