"""Pre-registered test: XAUUSD H1 London-session breakout (research/PREREG_gold_session_breakout.md).

Run ONCE. Read-only: attaches to the running MT5 terminal WITHOUT logging in again,
so the live bot on the same terminal is not disturbed. Data is cached to CSV.
"""
import argparse
import sys
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from src.config_schema import load_config, load_secrets
from src.data_checks import validate_bars
from src.features import atr
from src.labeling import triple_barrier_labels
from src.mt5_client import MT5Client
from src.storage import load_bars, save_bars

CONFIG, ENV = "config.cent.yaml", ".env.cent"
SYMBOL = "XAUUSD.vxc"
CACHE = Path("data/raw/XAUUSD.vxc_H1.csv")
SPREAD = 0.29
SL_ATR, TP_ATR, HORIZON = 1.0, 2.0, 10
ASIA_HOURS = range(0, 7)      # bars starting 00:00-06:59 London time
WINDOW_HOURS = range(7, 12)   # bars starting 07:00-11:59 London time
NEW_FEED = pd.Timestamp("2025-01-01", tz="UTC")


def fetch(refresh: bool) -> pd.DataFrame:
    if CACHE.exists() and not refresh:
        return load_bars(CACHE)
    cfg = load_config(CONFIG, ENV)
    if not mt5.initialize(path=cfg.broker.terminal_path, timeout=15000):  # attach only, no re-login
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        client = MT5Client(load_secrets(ENV), cfg.broker.terminal_path)  # used only for data helpers
        raw = client.get_rates(SYMBOL, "H1", 25000)
    finally:
        mt5.shutdown()  # closes THIS script's connection only
    clean, report = validate_bars(raw, "H1")
    print(f"fetched: {report.summary()}")
    save_bars(clean, CACHE)
    return clean


def find_signals(bars: pd.DataFrame) -> list[tuple[int, str]]:
    london = bars["time"].dt.tz_convert("Europe/London")
    hour = london.dt.hour
    signals = []
    for _, idx in bars.groupby(london.dt.date).groups.items():
        rows, h = bars.loc[idx], hour.loc[idx]
        asia = rows[h.isin(ASIA_HOURS).to_numpy()]
        if asia.empty:
            continue
        hi, lo = asia["high"].max(), asia["low"].min()
        for i in rows.index[h.isin(WINDOW_HOURS).to_numpy()]:
            close = bars.at[i, "close"]
            if close > hi:
                signals.append((i, "long"))
                break
            if close < lo:
                signals.append((i, "short"))
                break
    return signals


def summary(name: str, part: pd.DataFrame) -> None:
    if part.empty:
        print(f"{name}: no trades")
        return
    edge = part["r"].mean() - part["base_r"].mean()
    print(f"{name}: trades {len(part)} | win {part['win'].mean() * 100:.1f}% | mean R {part['r'].mean():+.3f} | "
          f"always-long {part['base_r'].mean():+.3f} | vs baseline {edge:+.3f}")
    for side in ("long", "short"):
        s = part[part["side"] == side]
        if len(s):
            print(f"    {side:5s}: {len(s):4d} trades | mean R {s['r'].mean():+.3f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="re-download data from MT5")
    args = parser.parse_args()

    bars = fetch(args.refresh)
    print(f"data: {len(bars)} bars | {bars['time'].min()} -> {bars['time'].max()}")

    df = bars[["time", "open", "high", "low", "close"]].copy()
    df["atr"] = atr(bars, 14)
    lab = triple_barrier_labels(df, tp_atr=TP_ATR, sl_atr=SL_ATR, horizon=HORIZON, spread=SPREAD)

    rows = []
    for i, side in find_signals(bars):
        if pd.isna(lab.at[i, f"{side}_r"]):
            continue
        rows.append({"time": bars.at[i, "time"], "side": side, "r": lab.at[i, f"{side}_r"],
                     "win": lab.at[i, f"{side}_label"], "base_r": lab.at[i, "long_r"]})
    trades = pd.DataFrame(rows)

    old = trades[trades["time"] < NEW_FEED]
    new = trades[trades["time"] >= NEW_FEED]
    print(f"\n=== LONDON BREAKOUT | SL {SL_ATR} ATR, TP {TP_ATR} ATR, {HORIZON} bars, spread {SPREAD} ===")
    summary("OLD FEED 2023-2024 (observe only)", old)
    summary("NEW FEED 2025-2026 (decision)", new)

    print("\nNew feed by year:")
    print(new.groupby(new["time"].dt.year)["r"].agg(["count", "mean"]).round(3).to_string())

    c1 = len(new) >= 60
    c2 = new["r"].mean() > 0.10
    c3 = all(new.loc[new["time"].dt.year == y, "r"].mean() > 0 for y in (2025, 2026))
    c4 = new["r"].mean() - new["base_r"].mean() >= 0.10
    print("\n=== PRE-REGISTERED VERDICT (new feed) ===")
    print(f"1. at least 60 trades              : {c1} ({len(new)})")
    print(f"2. mean R > +0.10                   : {c2} ({new['r'].mean():+.3f})")
    print(f"3. positive in 2025 and 2026        : {c3}")
    print(f"4. beats always-long by >= +0.10 R  : {c4} ({new['r'].mean() - new['base_r'].mean():+.3f})")
    print(f"RESULT: {'PASS' if c1 and c2 and c3 and c4 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())