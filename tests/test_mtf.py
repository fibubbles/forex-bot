import numpy as np
import pandas as pd
import pytest

from src.mtf import BREAKOUT_LOOKBACK, MultiTimeframe, h1_structure, m15_setup, m5_breakout


def _bars(closes, spread=0.5, start="2026-09-28 00:00", freq="5min") -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({
        "time": pd.date_range(start, periods=len(c), freq=freq, tz="UTC"),
        "open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c,
    })


def _trend(n=200, step=1.0, start=4000.0, freq="1h"):
    return _bars(start + step * np.arange(n), freq=freq)


def _range_then_breakout(direction: int, hold: bool = True, strong: bool = True) -> pd.DataFrame:
    """20 flat bars around 4000, then a breakout candle, then a confirmation bar."""
    flat = pd.DataFrame({
        "time": pd.date_range("2026-09-28", periods=20, freq="5min", tz="UTC"),
        "open": 4000.0, "high": 4001.0, "low": 3999.0, "close": 4000.0,
    })
    t_b = flat["time"].iloc[-1] + pd.Timedelta(minutes=5)
    t_c = t_b + pd.Timedelta(minutes=5)
    d = direction
    if strong:
        b = dict(time=t_b, open=4000.0, high=4000.0 + d * 4 if d > 0 else 4000.2,
                 low=4000.0 + d * 4 if d < 0 else 3999.8, close=4000.0 + d * 3.8)
    else:  # long wicks, tiny body
        b = dict(time=t_b, open=4000.0, high=4006.0, low=3994.0, close=4000.0 + d * 1.5)
    c_close = 4000.0 + d * 3.5 if hold else 4000.0  # hold = stays beyond the 4001 / 3999 level
    c = dict(time=t_c, open=b["close"], high=max(b["close"], c_close) + 0.2,
             low=min(b["close"], c_close) - 0.2, close=c_close)
    return pd.concat([flat, pd.DataFrame([b, c])], ignore_index=True)


# --- H1 / M15 filters ---------------------------------------------------------
def test_h1_structure_up_and_down():
    assert h1_structure(_trend(step=1.0)).iloc[-1] == 1
    assert h1_structure(_trend(step=-1.0)).iloc[-1] == -1


def test_h1_structure_flat_market_is_zero():
    flat = _bars(np.full(200, 4000.0), freq="1h")
    assert h1_structure(flat).iloc[-1] == 0


def test_m15_setup_needs_close_beyond_both_emas():
    up = _trend(step=1.0, freq="15min")
    assert m15_setup(up).iloc[-1] == 1
    # sharp drop on the last bar: close falls below EMA20 but stays above EMA50 -> 0
    closes = np.r_[4000 + np.arange(199) * 1.0, 4000 + 198 - 12]
    assert m15_setup(_bars(closes, freq="15min")).iloc[-1] == 0


# --- M5 breakout ------------------------------------------------------------------
@pytest.mark.parametrize("d", [1, -1])
def test_breakout_detected_with_confirmation(d):
    assert m5_breakout(_range_then_breakout(d)).iloc[-1] == d


@pytest.mark.parametrize("d", [1, -1])
def test_breakout_needs_confirmation_bar(d):
    assert m5_breakout(_range_then_breakout(d, hold=False)).iloc[-1] == 0


def test_weak_candle_is_not_a_breakout():
    assert m5_breakout(_range_then_breakout(1, strong=False)).iloc[-1] == 0


def test_resistance_window_is_the_12_bars_before_the_breakout_candle():
    n = len(_range_then_breakout(1))
    outside = _range_then_breakout(1)
    outside.loc[n - 3 - BREAKOUT_LOOKBACK, "high"] = 4050.0  # 13 bars before the breakout: ignored
    assert m5_breakout(outside).iloc[-1] == 1
    inside = _range_then_breakout(1)
    inside.loc[n - 3, "high"] = 4050.0  # the bar just before the breakout: raises resistance
    assert m5_breakout(inside).iloc[-1] == 0


def test_no_breakout_without_enough_history():
    df = _range_then_breakout(1).tail(BREAKOUT_LOOKBACK)  # too short for the lookback window
    assert (m5_breakout(df) == 0).all()


# --- full decision ---------------------------------------------------------------------
def test_buy_only_when_all_three_agree():
    view = MultiTimeframe().analyse(_trend(step=1.0), _trend(step=1.0, freq="15min"), _range_then_breakout(1))
    assert view.signal is not None and view.signal.side == "long"
    assert view.signal.tp_distance == pytest.approx(2 * view.signal.sl_distance)
    assert view.signal.sl_distance == pytest.approx(view.atr_m15)


def test_sell_only_when_all_three_agree():
    view = MultiTimeframe().analyse(_trend(step=-1.0), _trend(step=-1.0, freq="15min"), _range_then_breakout(-1))
    assert view.signal is not None and view.signal.side == "short"


@pytest.mark.parametrize("h1_step, m15_step, m5_dir", [
    (1.0, 1.0, -1),    # M5 breaks the wrong way
    (1.0, -1.0, 1),    # M15 disagrees with H1
    (-1.0, 1.0, 1),    # H1 disagrees
])
def test_no_trade_when_timeframes_disagree(h1_step, m15_step, m5_dir):
    view = MultiTimeframe().analyse(_trend(step=h1_step), _trend(step=m15_step, freq="15min"),
                                    _range_then_breakout(m5_dir))
    assert view.signal is None
    assert "H1" in view.summary and "M15" in view.summary


def test_not_enough_history_returns_no_signal():
    view = MultiTimeframe().analyse(_trend(n=50), _trend(n=50, freq="15min"), _range_then_breakout(1))
    assert view.signal is None and "not enough" in view.summary


# --- config: strategy and timeframe must match ------------------------------------------
def _config(strategy: str | None, timeframe: str) -> dict:
    from tests.test_micro import BASE
    cfg = {**BASE, "mode": "dry_run", "broker": {**BASE["broker"], "timeframe": timeframe}}
    return cfg if strategy is None else {**cfg, "strategy": strategy}


def test_config_defaults_to_trend_pullback():
    from src.config_schema import AppConfig
    assert AppConfig.model_validate(_config(None, "H1")).strategy == "trend_pullback_v0"


def test_config_mtf_on_m5_is_valid():
    from src.config_schema import AppConfig
    assert AppConfig.model_validate(_config("mtf_v1", "M5")).strategy == "mtf_v1"


@pytest.mark.parametrize("strategy, timeframe", [("mtf_v1", "H1"), ("trend_pullback_v0", "M5"), (None, "M5")])
def test_config_rejects_mismatched_strategy_and_timeframe(strategy, timeframe):
    from pydantic import ValidationError
    from src.config_schema import AppConfig
    with pytest.raises(ValidationError):
        AppConfig.model_validate(_config(strategy, timeframe))
