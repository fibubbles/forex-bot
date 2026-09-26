"""Honest check of trend_pullback_v0 on any symbol/timeframe from an MT5 account (read-only)."""
import argparse
import sys

import MetaTrader5 as mt5
import pandas as pd

from src.config_schema import load_config, load_secrets
from src.data_checks import validate_bars
from src.features import build_features
from src.labeling import triple_barrier_labels
from src.mt5_client import MT5Client
from src.strategy import TrendPullback


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--bars", type=int, default=20000)
    parser.add_argument("--horizon", type=int, default=60, help="max bars a trade is held")
    parser.add_argument("--timeframe", default=None, help="override the config timeframe, e.g. M15")
    args = parser.parse_args()

    cfg = load_config(args.config, args.env)
    sym, tf = cfg.broker.symbol, args.timeframe or cfg.broker.timeframe
    if not mt5.initialize(path=cfg.broker.terminal_path, timeout=15000):  # attach only, no re-login
        print(f"MT5 initialize failed: {mt5.last_error()}")
        return 1
    try:
        client = MT5Client(load_secrets(args.env), cfg.broker.terminal_path)
        info = client.symbol(sym)
        raw = client.get_rates(sym, tf, args.bars)
    finally:
        mt5.shutdown()  # closes THIS script's connection only

    clean, report = validate_bars(raw, tf)
    spread = max(float(clean["spread"].tail(500).median()), 10.0) * info.point
    strat = TrendPullback()
    data = triple_barrier_labels(build_features(clean), tp_atr=strat.tp_atr, sl_atr=strat.sl_atr,
                                 horizon=args.horizon, spread=spread)
    data = data.dropna(subset=["long_label", "short_label"])
    data["dir"] = strat.direction(data)

    print(f"\n=== {strat.name} on {sym} {tf} | {data['time'].min().date()} -> {data['time'].max().date()} "
          f"| {len(data)} bars | spread used {spread:.2f} ===")
    print(f"data check: {report.summary()}")
    print("(signals on consecutive bars overlap; the bot holds max 1 position)\n")

    spread_by_year = clean.groupby(clean["time"].dt.year)["spread"].median()
    rows = []
    for year, part in data.groupby(data["time"].dt.year):
        row = {"year": year, "spread_med": spread_by_year.get(year)}
        for side, d in (("long", 1), ("short", -1)):
            sel = part[part["dir"] == d]
            row[f"{side}_n"] = len(sel)
            row[f"{side}_R"] = round(sel[f"{side}_r"].mean(), 3) if len(sel) else float("nan")
            row[f"{side}_rand"] = round(part[f"{side}_r"].mean(), 3)
        rows.append(row)
    print(pd.DataFrame(rows).to_string(index=False))

    print("\nAll years:")
    for side, d in (("long", 1), ("short", -1)):
        sel = data[data["dir"] == d]
        print(f"  {side:5s}: signals {len(sel):5d} | win {sel[f'{side}_label'].mean() * 100:5.1f}% | "
              f"mean R {sel[f'{side}_r'].mean():+.3f} | random {data[f'{side}_r'].mean():+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())