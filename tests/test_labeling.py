import numpy as np
import pandas as pd
import pytest

from src.labeling import triple_barrier_labels

ATR = 0.0010  # entry 1.1000 -> long TP 1.1015, SL 1.0990


def _df(rows):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["atr"] = ATR
    return df


def test_long_tp_and_decision_bar_ignored():
    df = _df([
        (1.1000, 1.1030, 1.0970, 1.1000),  # t0: decision bar, huge range must be ignored
        (1.1000, 1.1005, 1.0995, 1.1003),  # t1: entry at open
        (1.1003, 1.1020, 1.1001, 1.1018),  # t2: TP 1.1015 hit
        (1.1018, 1.1019, 1.1017, 1.1018),
    ])
    res = triple_barrier_labels(df, horizon=3)
    assert res.loc[0, "long_label"] == 1
    assert res.loc[0, "long_r"] == pytest.approx(1.5)
    assert res.loc[0, "long_bars"] == 2
    assert res.loc[0, "short_label"] == 0          # short SL 1.1010 hit at t2
    assert res.loc[0, "short_r"] == pytest.approx(-1.0)


def test_same_bar_tp_and_sl_counts_as_sl():
    df = _df([
        (1.1000, 1.1001, 1.0999, 1.1000),
        (1.1000, 1.1020, 1.0980, 1.1000),  # both barriers inside one bar
        (1.1000, 1.1001, 1.0999, 1.1000),
        (1.1000, 1.1001, 1.0999, 1.1000),
    ])
    res = triple_barrier_labels(df, horizon=3)
    assert res.loc[0, "long_label"] == 0
    assert res.loc[0, "long_r"] == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open():
    df = _df([
        (1.1000, 1.1001, 1.0999, 1.1000),
        (1.1000, 1.1004, 1.0996, 1.0998),
        (1.0980, 1.0985, 1.0975, 1.0982),  # opens below SL 1.0990
        (1.0982, 1.0983, 1.0981, 1.0982),
    ])
    res = triple_barrier_labels(df, horizon=3)
    assert res.loc[0, "long_r"] == pytest.approx(-2.0)
    assert res.loc[0, "long_bars"] == 2


def test_timeout_uses_last_close():
    df = _df([
        (1.1000, 1.1001, 1.0999, 1.1000),
        (1.1000, 1.1005, 1.0995, 1.1002),
        (1.1002, 1.1006, 1.0996, 1.1001),
        (1.1001, 1.1005, 1.0997, 1.1004),
    ])
    res = triple_barrier_labels(df, horizon=3)
    assert res.loc[0, "long_label"] == 0
    assert res.loc[0, "long_r"] == pytest.approx(0.4)
    assert res.loc[0, "long_bars"] == 3


def test_rows_without_full_horizon_are_nan():
    df = _df([(1.1000, 1.1001, 1.0999, 1.1000)] * 4)
    res = triple_barrier_labels(df, horizon=3)
    assert np.isnan(res.loc[1:, "long_label"]).all()