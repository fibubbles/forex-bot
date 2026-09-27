"""Sanity check of mtf_v1 on history (XAUUSD.vxc): H1 structure -> M15 setup -> M5 breakout.

NOT a pre-registered study: the strategy goes live on the user's decision regardless. This shows
what to expect (trade frequency, win rate, R) and whether the H1/M15 filters help at all.

Read-only: attaches to the running MT5 terminal WITHOUT logging in again. Data is cached to CSV;
add --refresh to download again.

Uses the SAME functions as the live bot (src/mtf.py), one position at a time, entry at the next
M5 open, SL/TP from M15 ATR, spread 0.29, conservative fills (SL first if both hit in one bar).
"""
import argparse
import sys
import time
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from src.config_schema import load_config, load_secrets
from src.data_checks import validate_bars
from src.features import atr
from src.labeling import _barrier_outcome
from src.mt5_client import MT5Client
from src.mtf import ATR_PERIOD, SL_ATR, TP_ATR, h1_structure, m15_setup, m5_breakout
from src.storage import load_bars, save_bars
from src.timeutils import TIMEFRAME_DURATION

CONFIG, ENV = "config.cent.yaml", ".env.cent"
SYMBOL = "XAUUSD.vxc"
COUNTS = {"M5": 80_000, "M15": 30_000, "H1": 10_000}
SPREAD = 0.29
MAX_HOLD = 288  # M5 bars = 24h cap; the live bot has no time exit, almost all trades end earlier
NEW_FEED = pd.Timestamp("2025-01-01", tz="UTC")


def _download(tf: str, n: int, terminal_path: str) -> pd.DataFrame:
    """One timeframe, fresh attach each try (no re-login), up to 3 tries."""
    for attempt in range(1, 4):
        if not mt5.initialize(path=terminal_path, timeout=60000):
            print(f"{tf}: attach failed (try {attempt}): {mt5.last_error()}")
        else:
            try:
                client = MT5Client(load_secrets(ENV), terminal_path)
                clean, report = validate_bars(client.get_rates(SYMBOL, tf, n), tf)
                print(f"{tf}: {report.summary()}")
                return clean
            except Exception as e:  # IPC hiccups while the live bot shares the terminal
                print(f"{tf}: download failed (try {attempt}): {e}")
            finally:
                mt5.shutdown()  # closes THIS script's connection only
        time.sleep(5)
    raise SystemExit(f"{tf}: could not download after 3 tries; run the script again later")


def fetch(refresh: bool) -> dict[str, pd.DataFrame]:
    """Cached per timeframe, so a failure never re-downloads what already succeeded."""
    cfg = load_config(CONFIG, ENV)
    out = {}
    for tf, n in COUNTS.items():
        path = Path(f"data/raw/{SYMBOL}_{tf}_mtf.csv")
        if path.exists() and not refresh:
            out[tf] = load_bars(path)
            print(f"{tf}: loaded {len(out[tf])} cached bars")
            continue
        out[tf] = _download(tf, n, cfg.broker.terminal_path)
        save_bars(out[tf], path)
    return out


def align(m5: pd.DataFrame, htf: pd.DataFrame, tf: str, cols: dict[str, pd.Series]) -> pd.DataFrame:
    """For each M5 decision (at its CLOSE), attach the latest HTF bar that had already closed."""
    left = pd.DataFrame({"t": m5["time"] + TIMEFRAME_DURATION["M5"]})
    right = pd.DataFrame({"t": htf["time"] + TIMEFRAME_DURATION[tf], **cols})
    return pd.merge_asof(left, right.sort_values("t"), on="t", direction="backward")


def simulate(m5: pd.DataFrame, direction: np.ndarray, atr_m15: np.ndarray) -> pd.DataFrame:
    o, h, l, c = (m5[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    trades, busy_until = [], -1
    for i in np.flatnonzero(direction != 0):
        if i <= busy_until or i + MAX_HOLD + 1 >= len(m5) or not np.isfinite(atr_m15[i]):
            continue  # the bot holds one position at a time
        side = "long" if direction[i] > 0 else "short"
        win, r, held = _barrier_outcome(i, side, o, h, l, c, atr_m15, TP_ATR, SL_ATR, MAX_HOLD, SPREAD)
        busy_until = i + int(held)
        trades.append({"time": m5["time"].iat[i], "side": side, "win": win, "r": r,
                       "hours": held * 5 / 60, "usc": r * SL_ATR * atr_m15[i]})  # 0.01 lot: ~1 USC per $1
    return pd.DataFrame(trades)


def losing_streak(r: pd.Series) -> int:
    best = cur = 0
    for x in r:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def report(name: str, t: pd.DataFrame, weeks: float) -> None:
    if t.empty:
        print(f"{name}: no trades")
        return
    print(f"{name}: {len(t)} trades ({len(t) / weeks:.1f}/week) | win {t['win'].mean() * 100:.1f}% | "
          f"mean R {t['r'].mean():+.3f} | total {t['r'].sum():+.1f}R (~{t['usc'].sum():+.0f} USC) | "
          f"worst losing streak {losing_streak(t['r'])} | median hold {t['hours'].median():.1f}h")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="re-download data from MT5")
    args = parser.parse_args()

    d = fetch(args.refresh)
    m5, m15, h1 = d["M5"], d["M15"], d["H1"]
    start = max(m5["time"].min(), m15["time"].min() + pd.Timedelta(days=5), h1["time"].min() + pd.Timedelta(days=15))
    print(f"\nM5 data: {len(m5)} bars | {m5['time'].min()} -> {m5['time'].max()} | tested from {start}")

    s_h1 = align(m5, h1, "H1", {"h1": h1_structure(h1)})["h1"].fillna(0).to_numpy()
    a15 = align(m5, m15, "M15", {"m15": m15_setup(m15), "atr": atr(m15, ATR_PERIOD)})
    s_m15, atr_m15 = a15["m15"].fillna(0).to_numpy(), a15["atr"].to_numpy(float)
    s_m5 = m5_breakout(m5).to_numpy()

    valid = (m5["time"] >= start).to_numpy()
    full = np.where(valid & (s_h1 != 0) & (s_h1 == s_m15) & (s_m15 == s_m5), s_m5, 0)
    m5_only = np.where(valid, s_m5, 0)

    for label, direction in (("mtf_v1 (H1 + M15 + M5)", full), ("M5 breakout only, no filters", m5_only)):
        t = simulate(m5, direction, atr_m15)
        print(f"\n=== {label} | SL {SL_ATR} x ATR M15, TP {TP_ATR} x ATR M15, spread {SPREAD} ===")
        for part, mask in (("ALL", slice(None)), ("2025-2026 (live feed)", t["time"] >= NEW_FEED if len(t) else slice(None))):
            sub = t[mask] if len(t) else t
            weeks = max((m5["time"].max() - max(start, NEW_FEED if part != "ALL" else start)).days / 7, 1)
            report(part, sub, weeks)
        if len(t):
            by = t.groupby([t["time"].dt.year, "side"])["r"].agg(["count", "mean"]).round(3)
            print(by.to_string())

    print("\nBreak-even win rate at TP 2R / SL 1R is about 33% before spread; spread pushes it higher.")
    return 0


if __name__ == "__main__":
    sys.exit(main())