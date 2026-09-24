"""Walk-forward training: LightGBM (native API) per side + isotonic calibration, with purging.

No scikit-learn: Windows Smart App Control blocks some of its compiled modules here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.features import FEATURE_COLUMNS
from src.labeling import HORIZON_BARS
from src.mlutils import IsotonicCalibrator, roc_auc

log = logging.getLogger(__name__)

SIDES = ("long", "short")
NUM_BOOST_ROUND = 300
LGBM_PARAMS = {
    "objective": "binary",
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 100,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "feature_fraction": 0.8,
    "lambda_l2": 1.0,
    "seed": 42,
    "deterministic": True,
    "force_row_wise": True,
    "verbose": -1,
}


@dataclass(frozen=True)
class Fold:
    fold_id: int
    train_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass
class SideModel:
    booster: lgb.Booster
    calibrator: IsotonicCalibrator
    features: list[str]

    def raw_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(X[self.features])

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.calibrator.predict(self.raw_proba(X))


def make_folds(times: pd.Series, train_years: int = 3, test_months: int = 6) -> list[Fold]:
    first, last = times.min(), times.max()
    folds: list[Fold] = []
    test_start = first + pd.DateOffset(years=train_years)
    while test_start < last:
        test_end = min(test_start + pd.DateOffset(months=test_months), last + pd.Timedelta(seconds=1))
        folds.append(Fold(len(folds), test_start - pd.DateOffset(years=train_years), test_start, test_end))
        test_start = test_start + pd.DateOffset(months=test_months)
    return folds


def split_fold(
    labeled: pd.DataFrame, fold: Fold, purge_bars: int = HORIZON_BARS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train window minus the last `purge_bars` rows (their labels peek into test)."""
    train = labeled[(labeled["time"] >= fold.train_start) & (labeled["time"] < fold.test_start)]
    train = train.iloc[:-purge_bars] if len(train) > purge_bars else train.iloc[0:0]
    test = labeled[(labeled["time"] >= fold.test_start) & (labeled["time"] < fold.test_end)]
    return train, test


def fit_side_model(
    train: pd.DataFrame,
    side: str,
    features: list[str] = FEATURE_COLUMNS,
    calib_frac: float = 0.2,
    purge_bars: int = HORIZON_BARS,
) -> SideModel:
    """Fit on the older part, calibrate on the most recent part (purged in between)."""
    cut = int(len(train) * (1 - calib_frac))
    fit_part = train.iloc[: cut - purge_bars]
    calib_part = train.iloc[cut:]
    target = f"{side}_label"

    dataset = lgb.Dataset(fit_part[features], label=fit_part[target].astype(int))
    booster = lgb.train(LGBM_PARAMS, dataset, num_boost_round=NUM_BOOST_ROUND)

    raw = booster.predict(calib_part[features])
    calibrator = IsotonicCalibrator().fit(raw, calib_part[target].to_numpy())
    return SideModel(booster, calibrator, list(features))


def walk_forward(
    data: pd.DataFrame,
    folds: list[Fold],
    features: list[str] = FEATURE_COLUMNS,
    purge_bars: int = HORIZON_BARS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (out-of-sample predictions, per-fold metrics). purge_bars must be >= label horizon."""
    labeled = data.dropna(subset=[f"{s}_label" for s in SIDES]).reset_index(drop=True)
    keep = ["time"] + [f"{s}_{k}" for s in SIDES for k in ("label", "r", "bars")]
    preds, metrics = [], []

    for fold in folds:
        train, test = split_fold(labeled, fold, purge_bars)
        if len(test) == 0 or len(train) < 1000:
            continue

        fold_pred = test[keep].copy()
        fold_pred["fold"] = fold.fold_id
        row = {"fold": fold.fold_id, "test_start": fold.test_start.date(), "n_test": len(test)}

        for side in SIDES:
            sm = fit_side_model(train, side, features, purge_bars=purge_bars)
            raw = sm.raw_proba(test)
            fold_pred[f"prob_{side}"] = sm.calibrator.predict(raw)
            row[f"auc_{side}"] = roc_auc(test[f"{side}_label"].to_numpy(), raw)

        log.info("Fold %d (%s): AUC long=%.3f short=%.3f",
                 fold.fold_id, row["test_start"], row["auc_long"], row["auc_short"])
        preds.append(fold_pred)
        metrics.append(row)

    return pd.concat(preds, ignore_index=True), pd.DataFrame(metrics)