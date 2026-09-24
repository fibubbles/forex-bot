import pandas as pd

from src.labeling import HORIZON_BARS
from src.train import make_folds, split_fold


def _labeled(years: int = 5) -> pd.DataFrame:
    times = pd.date_range("2018-01-01", periods=years * 365 * 6, freq="4h", tz="UTC")
    return pd.DataFrame({"time": times})


def test_folds_are_chronological_and_contiguous():
    folds = make_folds(_labeled()["time"], train_years=3, test_months=6)
    assert len(folds) >= 3
    for f in folds:
        assert f.train_start < f.test_start < f.test_end
    for a, b in zip(folds, folds[1:]):
        assert a.test_end == b.test_start


def test_purge_gap_between_train_and_test():
    df = _labeled()
    for fold in make_folds(df["time"]):
        train, test = split_fold(df, fold)
        if len(test) == 0:
            continue
        assert train["time"].max() < fold.test_start
        assert test.index.min() - train.index.max() > HORIZON_BARS