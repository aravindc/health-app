"""Model factories. Every learned model is a scikit-learn compatible estimator.

Anything fitted to data (clipping limits, imputation values, scaling) lives
inside the estimator, so it is fitted on the training fold only.
"""

from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

LEARNED = ("linear", "random_forest", "xgboost", "lightgbm")
RULES = ("persistence", "linear_trend")
SEQUENCE = ("lstm", "tcn")  # src/seq_models.py; one network predicts every horizon


class QuantileClipper(BaseEstimator, TransformerMixin):
    """Winsorise each column at quantiles learned from the training data."""

    def __init__(self, quantiles=(0.001, 0.999)):
        self.quantiles = quantiles

    def fit(self, X, y=None):
        X = np.asarray(X, dtype=float)
        self.lo_, self.hi_ = np.nanquantile(X, self.quantiles[0], axis=0), np.nanquantile(X, self.quantiles[1], axis=0)
        return self

    def transform(self, X):
        return np.clip(np.asarray(X, dtype=float), self.lo_, self.hi_)


def make_model(name: str, params: dict, seed: int):
    p = dict(params)
    if name == "linear":
        q = tuple(p.pop("clip_quantiles", (0.001, 0.999)))
        return make_pipeline(QuantileClipper(q), SimpleImputer(strategy="median", keep_empty_features=True),
                             StandardScaler(), Ridge(**p))
    if name == "random_forest":
        return make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                             RandomForestRegressor(random_state=seed, **p))
    if name == "xgboost":
        from xgboost import XGBRegressor
        return XGBRegressor(random_state=seed, **p)
    if name == "lightgbm":
        from lightgbm import LGBMRegressor
        return LGBMRegressor(random_state=seed, **p)
    raise ValueError(f"unknown model {name!r}")
