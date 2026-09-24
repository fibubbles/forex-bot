"""Data quality checks for OHLC bars with a tz-aware UTC `time` column."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.timeutils import TIMEFRAME_DURATION, is_bar_closed, utc_to_server_wall

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread"]
MAX_INVALID_RATIO = 0.01


@dataclass
class DataCheckReport:
    rows_in: int = 0
    rows_out: int = 0
    dropped_nat_time: int = 0
    dropped_duplicates: int = 0
    dropped_weekend: int = 0
    dropped_invalid_ohlc: int = 0
    dropped_unclosed_bar: int = 0
    outlier_bars: list[str] = field(default_factory=list)
    unexpected_gaps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        if self.rows_out == 0:
            return False
        return self.dropped_invalid_ohlc / max(self.rows_in, 1) < MAX_INVALID_RATIO

    def summary(self) -> str:
        return (
            f"rows {self.rows_in} -> {self.rows_out} | "
            f"NaT={self.dropped_nat_time} dup={self.dropped_duplicates} wkend={self.dropped_weekend} "
            f"invalid={self.dropped_invalid_ohlc} unclosed={self.dropped_unclosed_bar} | "
            f"outliers={len(self.outlier_bars)} gaps={len(self.unexpected_gaps)} | "
            f"ok={self.ok}"
        )


def validate_bars(
    df: pd.DataFrame,
    timeframe: str,
    outlier_threshold: float = 0.03,
    now_utc: datetime | None = None,
) -> tuple[pd.DataFrame, DataCheckReport]:
    """Clean bars and report issues. Drops bad rows; only reports outliers and gaps."""
    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    report = DataCheckReport(rows_in=len(df))
    out = df.copy()

    nat = out["time"].isna()
    report.dropped_nat_time = int(nat.sum())
    out = out[~nat].sort_values("time")

    dup = out["time"].duplicated(keep="last")
    report.dropped_duplicates = int(dup.sum())
    out = out[~dup]

    weekend = utc_to_server_wall(out["time"]).dt.dayofweek >= 5  # server Sat/Sun
    report.dropped_weekend = int(weekend.sum())
    out = out[~weekend]

    oc = out[["open", "close"]]
    valid = (
        (out[["open", "high", "low", "close"]] > 0).all(axis=1)
        & (out["high"] >= oc.max(axis=1))
        & (out["low"] <= oc.min(axis=1))
    )
    report.dropped_invalid_ohlc = int((~valid).sum())
    out = out[valid]

    if len(out) and not is_bar_closed(out["time"].iloc[-1], timeframe, now_utc):
        out = out.iloc[:-1]
        report.dropped_unclosed_bar = 1

    log_ret = np.log(out["close"]).diff().abs()
    report.outlier_bars = out.loc[log_ret > outlier_threshold, "time"].astype(str).tolist()

    prev, cur = out["time"].shift(), out["time"]
    delta = cur - prev
    is_gap = delta > TIMEFRAME_DURATION[timeframe] * 3
    is_weekend = (
        (prev.dt.weekday == 4)
        & cur.dt.weekday.isin([6, 0])
        & (delta <= timedelta(days=4))
    )
    unexpected = is_gap & ~is_weekend
    report.unexpected_gaps = [f"{p} -> {c}" for p, c in zip(prev[unexpected], cur[unexpected])]

    report.rows_out = len(out)
    log.info("Data check: %s", report.summary())
    return out.reset_index(drop=True), report