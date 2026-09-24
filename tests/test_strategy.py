import pandas as pd
import pytest

from src.strategy import TrendPullback


def _feats(d1_trend: float, dist_ema20: float, dist_ema50: float, atr: float = 0.0020) -> pd.DataFrame:
    return pd.DataFrame([{"d1_trend": d1_trend, "dist_ema20": dist_ema20,
                          "dist_ema50": dist_ema50, "atr": atr}])


def test_long_signal_in_uptrend_pullback():
    sig = TrendPullback().evaluate(_feats(d1_trend=1.0, dist_ema20=-0.5, dist_ema50=0.8))
    assert sig is not None and sig.side == "long"
    assert sig.sl_distance == pytest.approx(0.0030)   # 1.5 x ATR
    assert sig.tp_distance == pytest.approx(0.0040)   # 2.0 x ATR


def test_short_signal_in_downtrend_rally():
    sig = TrendPullback().evaluate(_feats(d1_trend=-1.0, dist_ema20=0.5, dist_ema50=-0.8))
    assert sig is not None and sig.side == "short"


@pytest.mark.parametrize("d1, e20, e50", [
    (1.0, 0.5, 0.8),     # uptrend but no pullback
    (1.0, -0.5, -0.8),   # pulled back too far (below EMA50)
    (-1.0, -0.5, -0.8),  # downtrend but no rally
])
def test_no_signal(d1, e20, e50):
    assert TrendPullback().evaluate(_feats(d1, e20, e50)) is None


def test_empty_input_returns_none():
    assert TrendPullback().evaluate(pd.DataFrame()) is None