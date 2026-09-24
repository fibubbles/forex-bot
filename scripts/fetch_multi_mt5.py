"""Amendment 2: fetch D1 bars for the 10-pair universe from MT5 instead of Dukascopy."""
import logging
import sys
from pathlib import Path

import pandas as pd

from scripts.research_momentum_multi import PAIRS, SUFFIX
from src.config_schema import load_config, load_secrets
from src.data_checks import validate_bars
from src.mt5_client import MT5Client
from src.timeutils import utc_to_server_wall

logging.basicConfig(level=logging.WARNING)

OUT_DIR = Path("data/external_mt5")
D1_BARS = 7000
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def main() -> int:
    cfg = load_config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with MT5Client(load_secrets(), cfg.broker.terminal_path) as client:
        for pair in PAIRS:
            sym = pair.upper()
            matches = client.find_symbols(f"*{sym}*")
            if sym not in matches:
                print(f"{pair}: symbol {sym} NOT FOUND (similar: {matches})")
                continue

            clean, rep = validate_bars(client.get_rates(sym, "D1", D1_BARS), "D1")
            day = utc_to_server_wall(clean["time"]).dt.normalize().dt.tz_localize("UTC")
            out = pd.DataFrame({
                "timestamp": (day - EPOCH) // pd.Timedelta("1ms"),
                "open": clean["open"], "high": clean["high"],
                "low": clean["low"], "close": clean["close"],
            })
            out.to_csv(OUT_DIR / f"{pair}{SUFFIX}", index=False)
            print(f"{pair}: {len(out)} bars | {day.iloc[0].date()} -> {day.iloc[-1].date()} | {rep.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())