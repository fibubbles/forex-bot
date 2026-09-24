"""Bar storage helpers.

CSV instead of parquet: Windows Smart App Control blocks pyarrow's parquet DLL
on this machine. 25k-100k rows is small enough that CSV is fine.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def bars_path(directory: str | Path, symbol: str, timeframe: str) -> Path:
    return Path(directory) / f"{symbol}_{timeframe}.csv"


def save_bars(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")


def load_bars(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df