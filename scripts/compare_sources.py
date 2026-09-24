"""Amendment 2 validation: MT5 vs complete Dukascopy files, weekly Friday closes."""
from pathlib import Path

import pandas as pd

from scripts.research_momentum_multi import load

THRESHOLD = 0.99

for pair in ("eurusd", "audusd"):
    duka = load(pair, Path("data/external")).resample("W-FRI").last()
    mt5 = load(pair, Path("data/external_mt5")).resample("W-FRI").last()
    both = pd.concat({"duka": duka, "mt5": mt5}, axis=1).dropna()
    r = both.pct_change(fill_method=None).dropna()
    corr = r["duka"].corr(r["mt5"])
    print(f"{pair}: weeks {len(both)} | weekly return corr {corr:.4f} | "
          f"{'OK' if corr >= THRESHOLD else 'REJECT'}")