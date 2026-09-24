"""Pre-registered test: multi-pair FX time-series momentum (research/PREREG_momentum_multi.md).

Run ONCE. Do not change any parameter after seeing the results.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path("data/external_mt5")  # Amendment 2
SUFFIX = "-d1-bid-2005-01-01-2026-09-24.csv"
PAIRS = ["eurusd", "gbpusd", "usdjpy", "usdchf", "audusd",
         "usdcad", "nzdusd", "eurgbp", "eurjpy", "gbpjpy"]
CROSSES = {"eurgbp", "eurjpy", "gbpjpy"}
LOOKBACKS = (60, 125, 250)
VOL_WINDOW = 60
TARGET_VOL = 0.10
IS_END = pd.Timestamp("2018-12-31", tz="UTC")
IS_START = pd.Timestamp("2005-01-01", tz="UTC")
OOS_START = pd.Timestamp("2019-01-01", tz="UTC")


def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("jpy") else 0.0001


def cost_pips(pair: str) -> float:
    return 3.0 if pair in CROSSES else 2.0


def load(pair: str, data_dir: Path = DATA_DIR) -> pd.Series:
    df = pd.read_csv(data_dir / f"{pair}{SUFFIX}")
    s = df.set_index(pd.to_datetime(df["timestamp"], unit="ms", utc=True))["close"].sort_index()
    return s[s.index.dayofweek < 5]  # trading days only


def data_checks(closes: dict[str, pd.Series]) -> None:
    rows = []
    for pair, s in closes.items():
        ret = s.pct_change(fill_method=None)
        big = ret[ret.abs() > 0.05]
        worst = big.abs().idxmax() if len(big) else None
        rows.append({
            "pair": pair, "rows": len(s), "first": s.index[0].date(), "last": s.index[-1].date(),
            "days/yr": int(s.groupby(s.index.year).size().median()),
            "NaN": int(s.isna().sum()), "dup": int(s.index.duplicated().sum()),
            "moves>5%": len(big),
            "biggest": f"{worst.date()} {ret[worst]:+.1%}" if worst is not None else "-",
        })
    print("=== 1. DATA CHECKS (trading days only) ===")
    print(pd.DataFrame(rows).set_index("pair").to_string())


def pair_net_returns(s: pd.Series, pair: str, n: int) -> pd.Series:
    """Weekly net return contribution of one pair (equal-risk weight, decided at Friday close)."""
    daily_ret = s.pct_change(fill_method=None)
    vol = daily_ret.rolling(VOL_WINDOW).std() * np.sqrt(252)
    direction = np.sign(s / s.shift(n) - 1)
    weight = (direction * TARGET_VOL / vol / len(PAIRS)).resample("W-FRI").last()
    held = weight.shift(1)                                   # held during the following week
    wk_close = s.resample("W-FRI").last()
    gross = held * wk_close.pct_change(fill_method=None)
    turnover = (held - held.shift(1).fillna(0)).abs()        # every size change pays the spread
    cost = turnover * cost_pips(pair) * pip_size(pair) / wk_close.shift(1)
    return gross - cost


def stats(nets: pd.DataFrame) -> dict:
    net = nets.sum(axis=1)
    ann, vol = net.mean() * 52, net.std() * np.sqrt(52)
    equity = net.cumsum()
    yearly = net.groupby(net.index.year).sum()
    contrib = nets.sum()
    return {
        "weeks": len(net),
        "net%": net.sum() * 100,
        "ann%": ann * 100,
        "vol%": vol * 100,
        "sharpe": ann / vol if vol > 0 else np.nan,
        "maxDD%": (equity - equity.cummax()).min() * 100,
        "ex_best_yr%": (yearly.sum() - yearly.max()) * 100,
        "ex_best_pair%": (contrib.sum() - contrib.max()) * 100,
    }


def main() -> int:
    closes = {p: load(p) for p in PAIRS}
    data_checks(closes)

    rows, contribs = [], {}
    for n in LOOKBACKS:
        nets = pd.DataFrame({p: pair_net_returns(s, p, n) for p, s in closes.items()}).dropna()
        is_part = nets[(nets.index >= IS_START) & (nets.index <= IS_END)]
        oos_part = nets[nets.index >= OOS_START]
        rows.append({"period": "IS 2005-2018", "lookback": n, "start": is_part.index[0].date(), **stats(is_part)})
        rows.append({"period": "OOS 2019-2026", "lookback": n, "start": oos_part.index[0].date(), **stats(oos_part)})
        contribs[n] = oos_part.sum() * 100

    table = pd.DataFrame(rows).set_index(["period", "lookback"]).round(2)
    print("\n=== 2. PORTFOLIO RESULTS (net of costs) ===")
    print(table.to_string())

    print("\n=== 3. OOS CONTRIBUTION BY PAIR (net %) ===")
    print(pd.DataFrame(contribs).round(2).to_string())

    oos = table.xs("OOS 2019-2026", level="period")
    positive = oos[oos["net%"] > 0]
    c1 = len(positive) >= 2
    c2 = oos["sharpe"].mean() > 0.3
    c3 = len(positive) > 0 and bool((positive["ex_best_yr%"] > 0).all())
    c4 = len(positive) > 0 and bool((positive["ex_best_pair%"] > 0).all())
    print("\n=== 4. PRE-REGISTERED VERDICT (out-of-sample) ===")
    print(f"1. >= 2 of 3 lookbacks net positive      : {c1}  ({len(positive)}/3)")
    print(f"2. mean Sharpe > 0.3                      : {c2}  ({oos['sharpe'].mean():.2f})")
    print(f"3. positive without the best year         : {c3}")
    print(f"4. positive without the best pair         : {c4}")
    print(f"RESULT: {'PASS' if c1 and c2 and c3 and c4 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())