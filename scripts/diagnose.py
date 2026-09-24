"""Diagnose the OOS AUC collapse after 2023: data artifact or regime change?"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.config_schema import load_config
from src.features import FEATURE_COLUMNS
from src.storage import bars_path, load_bars
from src.train import SIDES, fit_side_model, make_folds, split_fold

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")
POINT = 0.00001
CUT = pd.Timestamp("2023-10-06", tz="UTC")


def bar_stats_by_year(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.sort_values("time").reset_index(drop=True)
    df["ret"] = np.log(df["close"]).diff()
    df["ret_next"] = df["ret"].shift(-1)
    df["gap_pts"] = (df["open"] - df["close"].shift(1)).abs() / POINT
    body_top = df[["open", "close"]].max(axis=1)
    body_bot = df[["open", "close"]].min(axis=1)
    df["no_up_wick"] = (df["high"] - body_top) < POINT / 2
    df["no_lo_wick"] = (body_bot - df["low"]) < POINT / 2

    g = df.groupby(df["time"].dt.year)
    return pd.DataFrame({
        "autocorr_1": g.apply(lambda x: x["ret"].corr(x["ret_next"])),
        "gap_pts": g["gap_pts"].mean(),
        "tickvol_med": g["tick_volume"].median(),
        "spread_med": g["spread"].median(),
        "no_up_wick%": g["no_up_wick"].mean() * 100,
        "no_lo_wick%": g["no_lo_wick"].mean() * 100,
    }).round(3)


def top_features(dataset: pd.DataFrame, fold_idx: int, side: str = "long", n: int = 8) -> pd.Series:
    labeled = dataset.dropna(subset=[f"{s}_label" for s in SIDES]).reset_index(drop=True)
    train, _ = split_fold(labeled, make_folds(dataset["time"])[fold_idx])
    gain = fit_side_model(train, side).booster.feature_importance(importance_type="gain")
    s = pd.Series(gain, index=FEATURE_COLUMNS)
    return (s / s.sum() * 100).sort_values(ascending=False).head(n).round(1)


def main() -> int:
    cfg = load_config()
    symbol, tf = cfg.broker.symbol, cfg.broker.timeframe
    raw = load_bars(bars_path(RAW_DIR, symbol, tf))
    dataset = load_bars(PROCESSED_DIR / f"{symbol}_{tf}_dataset.csv")
    preds = load_bars(PROCESSED_DIR / f"{symbol}_{tf}_oos_predictions.csv")

    print("\n=== 1. BAR CHARACTERISTICS BY YEAR ===")
    print(bar_stats_by_year(raw).to_string())

    print("\n=== 2. TOP FEATURES (gain %, long model) ===")
    for idx in (2, 14):
        print(f"\nFold {idx}:")
        print(top_features(dataset, idx).to_string())

    print("\n=== 3. OOS RESULTS BEFORE vs AFTER 2023-10 (threshold 0.50) ===")
    for name, part in (("before", preds[preds["time"] < CUT]), ("after", preds[preds["time"] >= CUT])):
        for side in SIDES:
            sel = part[part[f"prob_{side}"] >= 0.50]
            win = sel[f"{side}_label"].mean() * 100 if len(sel) else float("nan")
            mr = sel[f"{side}_r"].mean() if len(sel) else float("nan")
            print(f"{name:6s} {side:5s}: signals {len(sel):5d} | win {win:5.1f}% | mean R {mr:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())