"""Multi-timeframe strategy mtf_v1: H1 structure -> M15 setup -> M5 breakout entry.

NOT validated (see docs/EVALUATION_PLAN.md, amendment 4). Rules are fixed here, not tuned.
Inputs must be CLOSED bars only: validate_bars() already drops the bar that is still forming.

BUY needs all three (SELL is the mirror):
  H1  structure: EMA20 > EMA50 and close > EMA50
  M15 setup    : close above both EMA20 and EMA50
  M5  entry    : breakout candle (bar -2) closes above the highest high of the 12 bars before it,
                 is bullish with body >= 50% of its range; the next bar (bar -1) also closes above
                 that level. Enter at market right after bar -1 closes.
Exits: SL = 1.0 x ATR(14) M15, TP = 2.0 x ATR(14) M15.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.features import atr
from src.strategy import Signal

EMA_FAST, EMA_SLOW = 20, 50
BREAKOUT_LOOKBACK = 12   # M5 bars = 1 hour
MIN_BODY_RATIO = 0.5     # "strong candle": body at least half of the high-low range
SL_ATR, TP_ATR = 1.0, 2.0
ATR_PERIOD = 14
MIN_HTF_BARS = 3 * EMA_SLOW  # enough history for EMA50 to settle

WORDS = {1: "UP", -1: "DOWN", 0: "FLAT"}


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def h1_structure(bars: pd.DataFrame) -> pd.Series:
    """+1 uptrend, -1 downtrend, 0 unclear, for every bar."""
    c = bars["close"]
    fast, slow = _ema(c, EMA_FAST), _ema(c, EMA_SLOW)
    up = (fast > slow) & (c > slow)
    down = (fast < slow) & (c < slow)
    return pd.Series(np.select([up, down], [1, -1], 0), index=bars.index)


def m15_setup(bars: pd.DataFrame) -> pd.Series:
    """+1 close above both EMAs, -1 below both, 0 in between."""
    c = bars["close"]
    fast, slow = _ema(c, EMA_FAST), _ema(c, EMA_SLOW)
    up = (c > fast) & (c > slow)
    down = (c < fast) & (c < slow)
    return pd.Series(np.select([up, down], [1, -1], 0), index=bars.index)


def m5_breakout(bars: pd.DataFrame) -> pd.Series:
    """At bar i: +1 if bar i-1 broke out bullish and bar i held above; -1 mirror; else 0."""
    o, h, l, c = bars["open"], bars["high"], bars["low"], bars["close"]
    resistance = h.shift(2).rolling(BREAKOUT_LOOKBACK).max()  # highs of bars i-13 .. i-2
    support = l.shift(2).rolling(BREAKOUT_LOOKBACK).min()
    bo, bh, bl, bc = o.shift(1), h.shift(1), l.shift(1), c.shift(1)  # the breakout candle
    rng = bh - bl
    strong = (rng > 0) & ((bc - bo).abs() >= MIN_BODY_RATIO * rng)
    up = strong & (bc > bo) & (bc > resistance) & (c > resistance)
    down = strong & (bc < bo) & (bc < support) & (c < support)
    return pd.Series(np.select([up, down], [1, -1], 0), index=bars.index)


@dataclass(frozen=True)
class MTFView:
    h1: int
    m15: int
    m5: int
    atr_m15: float
    signal: Signal | None
    note: str = ""

    @property
    def summary(self) -> str:
        if self.note:
            return self.note
        m5 = {1: "bullish breakout", -1: "bearish breakout", 0: "no breakout"}[self.m5]
        return f"H1 {WORDS[self.h1]} | M15 {WORDS[self.m15]} | M5 {m5}"


class MultiTimeframe:
    name = "mtf_v1"

    def analyse(self, h1: pd.DataFrame, m15: pd.DataFrame, m5: pd.DataFrame) -> MTFView:
        if len(h1) < MIN_HTF_BARS or len(m15) < MIN_HTF_BARS or len(m5) < BREAKOUT_LOOKBACK + 2:
            return MTFView(0, 0, 0, float("nan"), None, note="not enough bars on H1/M15/M5")

        s_h1 = int(h1_structure(h1).iloc[-1])
        s_m15 = int(m15_setup(m15).iloc[-1])
        s_m5 = int(m5_breakout(m5).iloc[-1])
        a = float(atr(m15, ATR_PERIOD).iloc[-1])
        if not np.isfinite(a) or a <= 0:
            return MTFView(s_h1, s_m15, s_m5, a, None, note="invalid M15 ATR")

        signal = None
        if s_h1 != 0 and s_h1 == s_m15 == s_m5:
            side = "long" if s_h1 == 1 else "short"
            trend = WORDS[s_h1]
            signal = Signal(
                side=side,
                sl_distance=SL_ATR * a,
                tp_distance=TP_ATR * a,
                reason=f"H1 {trend} + M15 {trend} + M5 {'bullish' if s_h1 == 1 else 'bearish'} breakout confirmed",
                strategy=self.name,
            )
        return MTFView(s_h1, s_m15, s_m5, a, signal)

    def evaluate(self, h1: pd.DataFrame, m15: pd.DataFrame, m5: pd.DataFrame) -> Signal | None:
        return self.analyse(h1, m15, m5).signal
