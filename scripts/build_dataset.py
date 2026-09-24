"""Phase 2 / Step 2: raw bars -> features + triple-barrier labels -> processed CSV."""
import logging
import sys
from pathlib import Path

from src.config_schema import load_config
from src.features import FEATURE_COLUMNS, FEATURE_VERSION, build_features
from src.labeling import HORIZON_BARS, LABEL_VERSION, SL_ATR, TP_ATR, triple_barrier_labels
from src.storage import bars_path, load_bars, save_bars

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("build_dataset")

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")
POINT = 0.00001               # EURUSD, 5 digits
LABEL_SPREAD_POINTS = 20      # conservative vs ~15 live; historical bar spread is unreliable


def main() -> int:
    cfg = load_config()
    symbol, tf = cfg.broker.symbol, cfg.broker.timeframe

    raw = load_bars(bars_path(RAW_DIR, symbol, tf))
    feats = build_features(raw)
    data = triple_barrier_labels(feats, spread=LABEL_SPREAD_POINTS * POINT)
    labeled = data.dropna(subset=["long_label", "short_label"])

    out_path = PROCESSED_DIR / f"{symbol}_{tf}_dataset.csv"
    save_bars(data, out_path)

    breakeven = SL_ATR / (TP_ATR + SL_ATR) * 100

    print("\n=== DATASET SUMMARY ===")
    print(f"file:      {out_path}")
    print(f"versions:  features={FEATURE_VERSION} ({len(FEATURE_COLUMNS)} cols) | labels={LABEL_VERSION}")
    print(f"barriers:  TP={TP_ATR} ATR | SL={SL_ATR} ATR | horizon={HORIZON_BARS} bars | spread={LABEL_SPREAD_POINTS} pts")
    print(f"rows:      {len(data)} total | {len(labeled)} labeled")
    print(f"range:     {labeled['time'].min()} -> {labeled['time'].max()}")

    print(f"\nRandom-entry baseline (breakeven win rate ~{breakeven:.0f}% ignoring timeouts):")
    for side in ("long", "short"):
        print(
            f"  {side:5s}: win {labeled[f'{side}_label'].mean() * 100:5.1f}% | "
            f"mean R {labeled[f'{side}_r'].mean():+.3f} | "
            f"avg bars {labeled[f'{side}_bars'].mean():.1f}"
        )

    yearly = (
        labeled.groupby(labeled["time"].dt.year)[["long_label", "short_label"]]
        .mean()
        .mul(100)
        .round(1)
    )
    yearly["rows"] = labeled.groupby(labeled["time"].dt.year).size()
    print("\nWin rate % by year:")
    print(yearly.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())