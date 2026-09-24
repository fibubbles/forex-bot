"""Phase 1 / Step 1: download historical bars, validate, save to parquet."""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.config_schema import load_config, load_secrets
from src.data_checks import validate_bars
from src.mt5_client import MT5Client
from src.storage import bars_path, save_bars
from src.timeutils import TIMEFRAME_DURATION

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fetch_data")

RAW_DIR = Path("data/raw")


def bars_to_request(start: pd.Timestamp, timeframe: str) -> int:
    """Calendar-based estimate; overshoots (weekends) so we never request too few."""
    span = datetime.now(timezone.utc) - start.to_pydatetime()
    return int(span / TIMEFRAME_DURATION[timeframe]) + 100


def main() -> int:
    cfg = load_config()
    secrets = load_secrets()
    symbol, tf = cfg.broker.symbol, cfg.broker.timeframe
    start = pd.Timestamp(cfg.data.history_start, tz="UTC")
    count = bars_to_request(start, tf)

    log.info("Requesting %d %s bars for %s (from %s)", count, tf, symbol, start.date())
    with MT5Client(secrets, cfg.broker.terminal_path) as client:
        raw = client.get_rates(symbol, tf, count)

    clean, report = validate_bars(raw, tf)
    clean = clean[clean["time"] >= start].reset_index(drop=True)

    if clean.empty:
        log.error("No data after validation.")
        return 1

    first, last = clean["time"].iloc[0], clean["time"].iloc[-1]
    if first > start + pd.Timedelta(days=30):
        log.warning("Broker history starts at %s, later than requested %s", first.date(), start.date())

    out_path = bars_path(RAW_DIR, symbol, tf)
    save_bars(clean, out_path)

    print("\n=== FETCH SUMMARY ===")
    print(f"file:    {out_path}")
    print(f"bars:    {len(clean)}")
    print(f"range:   {first} -> {last}")
    print(f"check:   {report.summary()}")
    print(f"median spread (points): {clean['spread'].median():.0f}")
    if report.unexpected_gaps:
        print(f"\nUnexpected gaps ({len(report.unexpected_gaps)}), first 10:")
        for g in report.unexpected_gaps[:10]:
            print(f"  {g}")
    if report.outlier_bars:
        print(f"\nOutlier bars ({len(report.outlier_bars)}): {report.outlier_bars[:10]}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())