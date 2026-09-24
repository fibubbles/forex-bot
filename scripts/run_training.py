"""Phase 3 / Step 1: walk-forward evaluation. Saves out-of-sample predictions only."""
import logging
import sys
from pathlib import Path

import pandas as pd

from src.config_schema import load_config
from src.storage import load_bars, save_bars
from src.train import SIDES, make_folds, walk_forward

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PROCESSED_DIR = Path("data/processed")
THRESHOLDS = (0.35, 0.40, 0.45, 0.50, 0.55, 0.60)


def threshold_table(preds: pd.DataFrame, side: str) -> pd.DataFrame:
    rows = []
    for t in THRESHOLDS:
        sel = preds[preds[f"prob_{side}"] >= t]
        rows.append({
            "thr": t,
            "signals": len(sel),
            "pct_bars": round(100 * len(sel) / len(preds), 1),
            "win%": round(100 * sel[f"{side}_label"].mean(), 1) if len(sel) else float("nan"),
            "mean_R": round(sel[f"{side}_r"].mean(), 3) if len(sel) else float("nan"),
        })
    return pd.DataFrame(rows)


def main() -> int:
    cfg = load_config()
    symbol, tf = cfg.broker.symbol, cfg.broker.timeframe
    data = load_bars(PROCESSED_DIR / f"{symbol}_{tf}_dataset.csv")

    preds, metrics = walk_forward(data, make_folds(data["time"]))
    save_bars(preds, PROCESSED_DIR / f"{symbol}_{tf}_oos_predictions.csv")

    print("\n=== WALK-FORWARD (out-of-sample) ===")
    print(f"folds: {len(metrics)} | OOS rows: {len(preds)} | "
          f"{preds['time'].min().date()} -> {preds['time'].max().date()}")
    print(metrics.round(3).to_string(index=False))
    print(f"\nmean AUC  long={metrics['auc_long'].mean():.3f}  short={metrics['auc_short'].mean():.3f}")

    for side in SIDES:
        base = preds[f"{side}_label"].mean() * 100
        print(f"\n{side.upper()} signals by threshold (base win {base:.1f}%, breakeven ~40%):")
        print(threshold_table(preds, side).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())