"""Honest check of the placeholder strategy: mean R of its signals vs random entry."""
import sys
from pathlib import Path

import pandas as pd

from src.config_schema import load_config
from src.features import build_features
from src.labeling import triple_barrier_labels
from src.storage import bars_path, load_bars
from src.strategy import TrendPullback

RAW_DIR = Path("data/raw")
POINT = 0.00001
SPREAD_POINTS = 20
FEED_SWITCH = pd.Timestamp("2024-07-01", tz="UTC")


def main() -> int:
    cfg = load_config()
    strat = TrendPullback()
    feats = build_features(load_bars(bars_path(RAW_DIR, cfg.broker.symbol, cfg.broker.timeframe)))
    data = triple_barrier_labels(
        feats, tp_atr=strat.tp_atr, sl_atr=strat.sl_atr, horizon=60, spread=SPREAD_POINTS * POINT
    ).dropna(subset=["long_label", "short_label"])
    data["dir"] = strat.direction(data)

    print(f"\n=== {strat.name}: TP {strat.tp_atr} / SL {strat.sl_atr} ATR, spread {SPREAD_POINTS} pts ===")
    print("(signals on consecutive bars overlap; the executor would hold max 1 position)\n")
    periods = [
        ("old feed (<2024-07)", data[data["time"] < FEED_SWITCH]),
        ("new feed (>=2024-07)", data[data["time"] >= FEED_SWITCH]),
    ]
    for name, part in periods:
        print(name)
        for side, d in (("long", 1), ("short", -1)):
            sel = part[part["dir"] == d]
            rand = part[f"{side}_r"].mean()
            if len(sel):
                print(f"  {side:5s}: signals {len(sel):5d} | win {sel[f'{side}_label'].mean() * 100:5.1f}% | "
                      f"mean R {sel[f'{side}_r'].mean():+.3f} | random {rand:+.3f}")
            else:
                print(f"  {side:5s}: no signals")
    return 0


if __name__ == "__main__":
    sys.exit(main())