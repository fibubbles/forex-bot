"""Pre-registered test: candle confirmation on trend_pullback_v0 (research/PREREG_candle_confirmation.md).

Run ONCE. Offline: reads the cached CSV from Study 4, never touches MT5.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.features import atr, build_features
from src.labeling import triple_barrier_labels
from src.storage import load_bars
from src.strategy import TrendPullback

CACHE = Path("data/raw/XAUUSD.vxc_H1.csv")
SPREAD = 0.29
TP_ATR, SL_ATR, HORIZON = 1.5, 1.0, 30
NEW_FEED = pd.Timestamp("2025-01-01", tz="UTC")
N_DRAWS, SEED = 10_000, 42


def candle_flags(bars: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = bars["open"], bars["high"], bars["low"], bars["close"]
    po, pc = o.shift(1), c.shift(1)
    rng = h - l
    lower = np.minimum(o, c) - l
    upper = h - np.maximum(o, c)
    return pd.DataFrame({
        "bull_engulf": (pc < po) & (c > o) & (c >= po) & (o <= pc),
        "bear_engulf": (pc > po) & (c < o) & (c <= po) & (o >= pc),
        "hammer": (rng > 0) & (lower >= 0.6 * rng) & (upper <= 0.15 * rng),
        "shooting_star": (rng > 0) & (upper >= 0.6 * rng) & (lower <= 0.15 * rng),
    }, index=bars.index)


def build_signals(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars[["time", "open", "high", "low", "close"]].copy()
    df["atr"] = atr(bars, 14)
    lab = triple_barrier_labels(df, tp_atr=TP_ATR, sl_atr=SL_ATR, horizon=HORIZON, spread=SPREAD)

    feats = build_features(bars)
    direction = pd.Series(np.asarray(TrendPullback().direction(feats)), index=feats["time"].to_numpy())
    df["dir"] = df["time"].map(direction).fillna(0).astype(int)

    flags = candle_flags(bars)
    df["r"] = np.where(df["dir"] == 1, lab["long_r"], np.where(df["dir"] == -1, lab["short_r"], np.nan))
    df["pattern"] = np.select(
        [(df["dir"] == 1) & flags["bull_engulf"], (df["dir"] == 1) & flags["hammer"],
         (df["dir"] == -1) & flags["bear_engulf"], (df["dir"] == -1) & flags["shooting_star"]],
        ["bull_engulf", "hammer", "bear_engulf", "shooting_star"], default="")
    df["confirmed"] = df["pattern"] != ""

    sig = df[(df["dir"] != 0) & df["r"].notna()].copy()
    sig["year"] = sig["time"].dt.year
    return sig


def summary(name: str, sig: pd.DataFrame) -> None:
    conf = sig[sig["confirmed"]]
    print(f"{name}: base {len(sig)} signals, mean R {sig['r'].mean():+.3f} | "
          f"confirmed {len(conf)} signals, mean R {conf['r'].mean():+.3f}")
    for side, label in ((1, "BUY "), (-1, "SELL")):
        b, cf = sig[sig["dir"] == side], conf[conf["dir"] == side]
        print(f"    {label}: base {len(b):4d} mean R {b['r'].mean():+.3f} | "
              f"confirmed {len(cf):4d} mean R {cf['r'].mean():+.3f}")


def main() -> int:
    if not CACHE.exists():
        print(f"Missing {CACHE}; it should exist from Study 4. Do not refetch without an amendment.")
        return 1
    bars = load_bars(CACHE)
    print(f"data: {len(bars)} bars | {bars['time'].min()} -> {bars['time'].max()}")

    sig = build_signals(bars)
    old, new = sig[sig["time"] < NEW_FEED], sig[sig["time"] >= NEW_FEED]
    print(f"\n=== CANDLE CONFIRMATION | TP {TP_ATR} ATR, SL {SL_ATR} ATR, {HORIZON} bars, spread {SPREAD} ===")
    summary("OLD FEED (observe only)", old)
    summary("NEW FEED (decision)", new)

    conf = new[new["confirmed"]]
    print("\nNew feed by year:")
    by_year = pd.DataFrame({
        "base_n": new.groupby("year")["r"].count(), "base_R": new.groupby("year")["r"].mean(),
        "conf_n": conf.groupby("year")["r"].count(), "conf_R": conf.groupby("year")["r"].mean(),
    }).round(3)
    print(by_year.to_string())

    print("\nNew feed by pattern (observe only):")
    print(conf.groupby("pattern")["r"].agg(["count", "mean"]).round(3).to_string())

    base_mean, conf_mean, n_conf = new["r"].mean(), conf["r"].mean(), len(conf)
    rng = np.random.default_rng(SEED)
    base_r = new["r"].to_numpy()
    draws = np.array([rng.choice(base_r, size=n_conf, replace=False).mean() for _ in range(N_DRAWS)]) \
        if 0 < n_conf <= len(base_r) else np.array([np.inf])
    p_value = float((draws >= conf_mean).mean())

    c1 = n_conf >= 60
    c2 = conf_mean - base_mean >= 0.10
    c3 = all(by_year.at[y, "conf_R"] >= by_year.at[y, "base_R"] if y in by_year.index else False
             for y in (2025, 2026))
    c4 = conf_mean > 0.10
    c5 = p_value < 0.05
    print("\n=== PRE-REGISTERED VERDICT (new feed) ===")
    print(f"1. at least 60 confirmed signals     : {c1} ({n_conf})")
    print(f"2. beats base by >= +0.10 R          : {c2} ({conf_mean - base_mean:+.3f})")
    print(f"3. >= base in 2025 and 2026          : {c3}")
    print(f"4. confirmed mean R > +0.10          : {c4} ({conf_mean:+.3f})")
    print(f"5. random-subset p < 0.05            : {c5} (p = {p_value:.4f})")
    print(f"RESULT: {'PASS' if all((c1, c2, c3, c4, c5)) else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())