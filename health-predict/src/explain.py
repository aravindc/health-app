"""SHAP explanations for the selected tree model.

The selected model predicts the CHANGE in glucose (delta target), so SHAP values
explain contributions to the predicted change from current glucose.

Feature importance describes what the model relies on. It is NOT evidence of
cause and effect in the body: correlated inputs share or trade credit, and the
model learns associations in this person's data, including the behaviour of
the pump's own algorithm.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from joblib import Parallel, delayed


def _chunk(model, X):
    import shap
    return shap.TreeExplainer(model).shap_values(X, check_additivity=False)


def shap_values(estimator, X: pd.DataFrame, n_jobs: int = 8) -> tuple[np.ndarray, pd.DataFrame, float]:
    """SHAP values for a fitted pipeline (imputer -> tree model) or a bare tree model.

    Returns (values (n, features), X as the tree model sees it, expected value).
    """
    import shap

    steps = getattr(estimator, "steps", None)
    model = steps[-1][1] if steps else estimator
    Xm = X
    if steps:
        for _, step in steps[:-1]:
            Xm = pd.DataFrame(step.transform(Xm), columns=X.columns, index=X.index)
    chunks = np.array_split(Xm.to_numpy(), max(1, min(n_jobs, len(Xm))))
    parts = Parallel(n_jobs=n_jobs)(delayed(_chunk)(model, c) for c in chunks)
    values = np.vstack(parts)
    base = float(np.ravel(shap.TreeExplainer(model).expected_value)[0])
    return values, Xm, base


def importance(values: np.ndarray, columns: list[str], catalog: pd.DataFrame | None = None) -> pd.DataFrame:
    imp = pd.DataFrame({"feature": columns, "mean |SHAP|": np.abs(values).mean(axis=0)})
    imp = imp.sort_values("mean |SHAP|", ascending=False).reset_index(drop=True)
    imp["share %"] = imp["mean |SHAP|"] / imp["mean |SHAP|"].sum() * 100
    if catalog is not None:
        imp = imp.merge(catalog[["feature", "group"]], on="feature", how="left")
    imp.index = imp.index + 1
    return imp


def group_importance(imp: pd.DataFrame) -> pd.DataFrame:
    return imp.groupby("group", as_index=False)[["mean |SHAP|", "share %"]].sum().sort_values("share %", ascending=False)
