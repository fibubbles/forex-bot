import numpy as np
import pandas as pd

from src.features import FEATURE_COLUMNS, WARMUP_BARS, build_features

N_BARS = 900


def _synthetic_bars(n: int = N_BARS, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1.10 + np.cumsum(rng.normal(0, 0.001, n))
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 0.0008, n))
    return pd.DataFrame({
        "time": pd.date_range("2025-01-06", periods=n, freq="4h", tz="UTC"),
        "open": open_,
        "high": np.maximum(open_, close) + wick,
        "low": np.minimum(open_, close) - wick,
        "close": close,
        "tick_volume": 100,
        "spread": 10,
    })


def test_no_lookahead():
    bars = _synthetic_bars()
    full = build_features(bars).set_index("time")
    for k in (500, 700, 850):
        partial = build_features(bars.iloc[: k + 1]).set_index("time")
        t = bars["time"].iloc[k]
        pd.testing.assert_series_equal(
            full.loc[t, FEATURE_COLUMNS], partial.loc[t, FEATURE_COLUMNS], check_names=False
        )


def test_output_is_clean():
    feats = build_features(_synthetic_bars())
    assert feats[FEATURE_COLUMNS].notna().all().all()
    assert len(feats) == N_BARS - WARMUP_BARS