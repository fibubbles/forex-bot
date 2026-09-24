"""Amendment 3: build EURJPY/GBPJPY from validated majors and cross-check vs the broker's .vxb series."""
import logging
import sys
from pathlib import Path

import pandas as pd

from scripts.research_momentum_multi import SUFFIX
from src.config_schema import load_config, load_secrets
from src.mt5_client import MT5Client

logging.basicConfig(level=logging.WARNING)

DIR = Path("data/external_mt5")
THRESHOLD = 0.99
CROSSES = {"eurjpy": ("eurusd", "usdjpy"), "gbpjpy": ("gbpusd", "usdjpy")}


def read(pair: str) -> pd.DataFrame:
    return pd.read_csv(DIR / f"{pair}{SUFFIX}").set_index("timestamp")


def weekly_close_from_csv(df: pd.DataFrame) -> pd.Series:
    s = df["close"].copy()
    s.index = pd.to_datetime(s.index, unit="ms", utc=True)
    return s.resample("W-FRI").last()


def main() -> int:
    cfg = load_config()
    with MT5Client(load_secrets(), cfg.broker.terminal_path) as client:
        for cross, (a, b) in CROSSES.items():
            x = read(a).join(read(b), lsuffix="_a", rsuffix="_b", how="inner")
            # Only 'close' is used by the test; high/low products are approximate.
            synth = pd.DataFrame({c: x[f"{c}_a"] * x[f"{c}_b"] for c in ("open", "high", "low", "close")})
            synth.reset_index().to_csv(DIR / f"{cross}{SUFFIX}", index=False)

            vxb = client.get_rates(f"{cross.upper()}.vxb", "D1", 7000).set_index("time")["close"]
            both = pd.concat({"synthetic": weekly_close_from_csv(synth), "vxb": vxb.resample("W-FRI").last()},
                             axis=1, sort=True).dropna()
            r = both.pct_change(fill_method=None).dropna()
            corr = r["synthetic"].corr(r["vxb"])
            print(f"{cross}: {len(synth)} synthetic bars | vxb overlap {len(both)} weeks | "
                  f"weekly return corr {corr:.4f} | {'OK' if corr >= THRESHOLD else 'REJECT'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())