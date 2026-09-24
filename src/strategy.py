"""Strategy interface + a simple rule-based placeholder for pipeline testing.

The executor only depends on `Signal` and `Strategy.evaluate`, so a validated
strategy can replace the placeholder later without touching the executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Signal:
    side: Literal["long", "short"]
    sl_distance: float
    tp_distance: float
    reason: str
    strategy: str


class Strategy(Protocol):
    name: str

    def direction(self, feats: pd.DataFrame) -> pd.Series:
        """Vectorised: +1 long, -1 short, 0 none. Used for backtests."""
        ...

    def evaluate(self, feats: pd.DataFrame) -> Signal | None:
        """Decision for the LAST row (the most recent closed bar)."""
        ...


class TrendPullback:
    """PLACEHOLDER (not validated): buy dips in a D1 uptrend, sell rallies in a D1 downtrend."""

    name = "trend_pullback_v0"

    def __init__(self, tp_atr: float = 2.0, sl_atr: float = 1.5) -> None:
        self.tp_atr = tp_atr
        self.sl_atr = sl_atr

    def direction(self, feats: pd.DataFrame) -> pd.Series:
        long = (feats["d1_trend"] > 0) & (feats["dist_ema20"] < 0) & (feats["dist_ema50"] > 0)
        short = (feats["d1_trend"] < 0) & (feats["dist_ema20"] > 0) & (feats["dist_ema50"] < 0)
        return pd.Series(np.select([long, short], [1, -1], 0), index=feats.index)

    def evaluate(self, feats: pd.DataFrame) -> Signal | None:
        if feats.empty:
            return None
        last = feats.iloc[[-1]]
        d = int(self.direction(last).iloc[0])
        if d == 0:
            return None

        atr = float(last["atr"].iloc[0])
        side = "long" if d == 1 else "short"
        trend = "up" if d == 1 else "down"
        return Signal(
            side=side,
            sl_distance=self.sl_atr * atr,
            tp_distance=self.tp_atr * atr,
            reason=f"D1 {trend}trend + H4 pullback to EMA20-EMA50 zone",
            strategy=self.name,
        )