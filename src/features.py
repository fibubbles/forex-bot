"""Feature engineering v1. Every feature at bar t uses only bars <= t.

The bot decides at the close of bar t and would enter at the open of t+1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.timeutils import utc_to_server_wall

FEATURE_VERSION = "v1"
WARMUP_BARS = 400  # EMA200 and D1 EMA50 need history before values are reliable

FEATURE_COLUMNS = [
    "ret_1", "ret_3", "ret_6", "ret_12",
    "atr_pct", "atr_ratio",
    "rsi_14", "rsi_8", "willr_14",
    "dist_ema20", "dist_ema50", "dist_ema200",
    "slope_ema20", "slope_ema50",
    "range_pos_20",
    "d1_trend",
    "bar_slot", "day_of_week",
]


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return _wilder(tr, n)


def rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0), n)
    loss = _wilder(-delta.clip(upper=0), n)
    return (100 * gain / (gain + loss)).fillna(50.0)


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n).max()
    ll = df["low"].rolling(n).min()
    return (-100 * (hh - df["close"]) / (hh - ll)).where(hh > ll)


def _d1_trend(close: pd.Series, server_time: pd.Series, atr_h4: pd.Series) -> pd.Series:
    """Previous completed day's close vs its EMA50, in H4 ATR units."""
    day = server_time.dt.normalize()
    daily_close = close.groupby(day).last()
    daily_ema = _ema(daily_close, 50)
    prev_gap = (daily_close - daily_ema).shift(1)  # only fully completed days
    return day.map(prev_gap) / atr_h4


def build_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Return time, OHLC, raw `atr` (for labeling) and FEATURE_COLUMNS."""
    df = bars.sort_values("time").reset_index(drop=True)
    c = df["close"]
    a = atr(df, 14)

    out = df[["time", "open", "high", "low", "close"]].copy()
    out["atr"] = a

    log_c = np.log(c)
    for n in (1, 3, 6, 12):
        out[f"ret_{n}"] = log_c.diff(n)

    out["atr_pct"] = a / c
    out["atr_ratio"] = a / atr(df, 56)
    out["rsi_14"] = rsi(c, 14)
    out["rsi_8"] = rsi(c, 8)
    out["willr_14"] = williams_r(df, 14)

    for span in (20, 50, 200):
        e = _ema(c, span)
        out[f"dist_ema{span}"] = (c - e) / a
        if span in (20, 50):
            out[f"slope_ema{span}"] = (e - e.shift(5)) / a

    hh = df["high"].rolling(20).max()
    ll = df["low"].rolling(20).min()
    out["range_pos_20"] = ((c - ll) / (hh - ll)).where(hh > ll)

    server = utc_to_server_wall(df["time"])
    out["d1_trend"] = _d1_trend(c, server, a)
    out["bar_slot"] = server.dt.hour // 4
    out["day_of_week"] = server.dt.dayofweek

    out = out.iloc[WARMUP_BARS:]
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURE_COLUMNS + ["atr"])
    return out.reset_index(drop=True)