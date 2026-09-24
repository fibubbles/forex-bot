"""Small ML helpers in pure numpy/pandas (no scikit-learn).

Windows Smart App Control blocks some compiled scikit-learn modules on this
machine, and we only need these two tools.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def roc_auc(y_true, score) -> float:
    """ROC AUC via the rank-sum (Mann-Whitney) formula, ties averaged."""
    y = np.asarray(y_true).astype(bool)
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = pd.Series(np.asarray(score, dtype=float)).rank(method="average").to_numpy()
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


class IsotonicCalibrator:
    """Monotonic step-function calibration (pool-adjacent-violators), clipped to [0, 1]."""

    def fit(self, x, y) -> "IsotonicCalibrator":
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        ux, inv = np.unique(x, return_inverse=True)  # pool tied scores first
        weights = np.bincount(inv).astype(float)
        values = np.bincount(inv, weights=y) / weights

        v, w, xmax = [], [], []
        for xi, vi, wi in zip(ux, values, weights):
            v.append(vi)
            w.append(wi)
            xmax.append(xi)
            while len(v) > 1 and v[-2] > v[-1]:  # merge blocks that break monotonicity
                total = w[-2] + w[-1]
                v[-2:] = [(v[-2] * w[-2] + v[-1] * w[-1]) / total]
                w[-2:] = [total]
                xmax[-2:] = [xmax[-1]]

        self.x_ = np.array(xmax)
        self.y_ = np.clip(np.array(v), 0.0, 1.0)
        return self

    def predict(self, x) -> np.ndarray:
        idx = np.searchsorted(self.x_, np.asarray(x, dtype=float), side="left")
        return self.y_[np.clip(idx, 0, len(self.y_) - 1)]