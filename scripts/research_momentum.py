"""Pre-registered test: EURUSD D1 time-series momentum (see research/PREREG_momentum.md).

Run ONCE. Do not change any parameter after seeing the results.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.storage import bars_path, load_bars

DUKA_FILE = Path("data/external/eurusd-d1-bid-2005-01-01-2026-09-24.csv")
LOOKBACKS = (20, 60, 250)
COST_PIPS = 2.0
PIP = 0.0001
IS_END = pd.Timestamp("2018-12-31", tz="UTC")
OOS_START = pd.Timestamp("2019-01-01", tz="UTC")
FEED_SWITCH = pd.Timestamp("2024-07-01", tz="UTC")


def load_dukascopy() -> pd.Series:
    df = pd.read_csv(DUKA_FILE)
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    daily = df.set_index("time")["close"].sort_index()
    return daily[daily.index.dayofweek < 5]  # trading days only (Amendment 1)


def data_checks(daily: pd.Series) -> None:
    rets = daily.pct_change(fill_method=None).dropna()
    big = rets[rets.abs() > 0.03]
    print("=== 1. DUKASCOPY DATA CHECKS ===")
    print(f"rows {len(daily)} | {daily.index[0].date()} -> {daily.index[-1].date()} | "
          f"NaN {daily.isna().sum()} | duplicates {daily.index.duplicated().sum()}")
    print(f"close range {daily.min():.4f} .. {daily.max():.4f} | "
          f"median days/year {daily.groupby(daily.index.year).size().median():.0f}")
    print(f"daily moves > 3%: {len(big)} -> {[f'{d.date()} {r:+.1%}' for d, r in big.items()]}")


def cross_check(duka_weekly: pd.Series) -> None:
    vx = load_bars(bars_path(Path("data/raw"), "EURUSD", "H4")).set_index("time")["close"]
    both = pd.concat({"duka": duka_weekly, "valetax": vx.resample("W-FRI").last()}, axis=1).dropna()
    print("\n=== 2. DUKASCOPY vs VALETAX (weekly Friday closes) ===")
    for name, part in (("old feed", both[both.index < FEED_SWITCH]),
                       ("new feed", both[both.index >= FEED_SWITCH])):
        r = part.pct_change(fill_method=None).dropna()
        diff = (part["duka"] - part["valetax"]).abs().median() / PIP
        print(f"{name:8s}: weeks {len(part):4d} | weekly return corr {r['duka'].corr(r['valetax']):.4f} | "
              f"median |close diff| {diff:.1f} pips")


def momentum_weekly(daily: pd.Series, n: int) -> pd.DataFrame:
    """Signal = sign of past n-day return at Friday close; held for the following week."""
    past_ret = daily / daily.shift(n) - 1
    weekly_close = daily.resample("W-FRI").last()
    pos = np.sign(past_ret.resample("W-FRI").last()).shift(1)
    week_ret = weekly_close.pct_change(fill_method=None)
    changed = (pos != pos.shift(1)) & pos.notna()
    cost = changed * COST_PIPS * PIP / weekly_close.shift(1)
    return pd.DataFrame({"pos": pos, "net": pos * week_ret - cost}).dropna()


def stats(part: pd.DataFrame) -> dict:
    net = part["net"]
    ann, vol = net.mean() * 52, net.std() * np.sqrt(52)
    equity = net.cumsum()
    yearly = net.groupby(net.index.year).sum()
    return {
        "net%": net.sum() * 100,
        "ann%": ann * 100,
        "sharpe": ann / vol if vol > 0 else np.nan,
        "maxDD%": (equity - equity.cummax()).min() * 100,
        "win_wk%": (net > 0).mean() * 100,
        "changes": int((part["pos"] != part["pos"].shift(1)).sum()),
        "ex_best_yr%": (yearly.sum() - yearly.max()) * 100,
    }


def main() -> int:
    daily = load_dukascopy()
    data_checks(daily)
    cross_check(daily.resample("W-FRI").last())

    periods = (("IS 2005-2018", lambda d: d[d.index <= IS_END]),
               ("OOS 2019-2026", lambda d: d[d.index >= OOS_START]))
    rows = []
    for n in LOOKBACKS:
        df = momentum_weekly(daily, n)
        for pname, cut in periods:
            rows.append({"period": pname, "lookback": n, **stats(cut(df))})
    table = pd.DataFrame(rows).set_index(["period", "lookback"]).round(2)

    print(f"\n=== 3. MOMENTUM (net of {COST_PIPS} pips per position change) ===")
    print(table.to_string())

    wk = daily.resample("W-FRI").last().pct_change(fill_method=None).dropna()
    print("\nContext, buy & hold EURUSD:")
    for pname, cut in periods:
        p = cut(wk)
        print(f"  {pname}: net {p.sum() * 100:+.1f}% | sharpe {p.mean() * 52 / (p.std() * np.sqrt(52)):.2f}")

    oos = table.xs("OOS 2019-2026", level="period")
    positive = oos[oos["net%"] > 0]
    c1 = len(positive) >= 2
    c2 = oos["sharpe"].mean() > 0.3
    c3 = len(positive) > 0 and bool((positive["ex_best_yr%"] > 0).all())
    print("\n=== 4. PRE-REGISTERED VERDICT (out-of-sample) ===")
    print(f"1. >= 2 of 3 lookbacks net positive : {c1}  ({len(positive)}/3)")
    print(f"2. mean Sharpe > 0.3                 : {c2}  ({oos['sharpe'].mean():.2f})")
    print(f"3. positive without the best year    : {c3}")
    print(f"RESULT: {'PASS' if c1 and c2 and c3 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())