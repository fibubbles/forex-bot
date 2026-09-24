"""Walk-forward on the live-consistent feed only (Valetax switched feed in 2024-07)."""
import logging
import sys
from pathlib import Path

import pandas as pd

from src.config_schema import load_config
from src.features import FEATURE_COLUMNS, build_features
from src.labeling import triple_barrier_labels
from src.storage import bars_path, load_bars
from src.train import SIDES, make_folds, walk_forward

logging.basicConfig(level=logging.WARNING)

RAW_DIR = Path("data/raw")
POINT = 0.00001
SPREAD_POINTS = 20
FEED_SWITCH = pd.Timestamp("2024-07-01", tz="UTC")
ROWS_FROM = pd.Timestamp("2024-09-01", tz="UTC")  # 2-month buffer: lookbacks mostly on new feed

SHORT_TERM = {"willr_14", "rsi_8", "range_pos_20", "ret_1"}
SLOW_FEATURES = [f for f in FEATURE_COLUMNS if f not in SHORT_TERM]

LABEL_CONFIGS = [  # (name, tp_atr, sl_atr, horizon_bars)
    ("1.5/1.0 h30", 1.5, 1.0, 30),
    ("2.0/1.5 h60", 2.0, 1.5, 60),
    ("3.0/1.5 h90", 3.0, 1.5, 90),
]
FEATURE_SETS = [("all", FEATURE_COLUMNS), ("slow", SLOW_FEATURES)]


def summarize(label_name: str, feat_name: str, preds: pd.DataFrame, metrics: pd.DataFrame) -> dict:
    row = {"labels": label_name, "feats": feat_name, "folds": len(metrics), "oos": len(preds)}
    for side in SIDES:
        s = side[0]
        prob = preds[f"prob_{side}"]
        top = preds[prob >= prob.quantile(0.8)]
        row[f"auc_{s}"] = metrics[f"auc_{side}"].mean()
        row[f"randR_{s}"] = preds[f"{side}_r"].mean()
        row[f"top20R_{s}"] = top[f"{side}_r"].mean()
        row[f"top20N_{s}"] = len(top)
    return row


def main() -> int:
    cfg = load_config()
    raw = load_bars(bars_path(RAW_DIR, cfg.broker.symbol, cfg.broker.timeframe))
    feats = build_features(raw)
    feats = feats[feats["time"] >= FEED_SWITCH].reset_index(drop=True)

    rows = []
    for label_name, tp, sl, horizon in LABEL_CONFIGS:
        labeled = triple_barrier_labels(
            feats, tp_atr=tp, sl_atr=sl, horizon=horizon, spread=SPREAD_POINTS * POINT
        )
        data = labeled[labeled["time"] >= ROWS_FROM].reset_index(drop=True)
        folds = make_folds(data["time"], train_years=1, test_months=3)

        for feat_name, cols in FEATURE_SETS:
            print(f"Running {label_name} | {feat_name} ...", flush=True)
            preds, metrics = walk_forward(data, folds, cols, purge_bars=horizon)
            rows.append(summarize(label_name, feat_name, preds, metrics))

    table = pd.DataFrame(rows).round(3)
    print("\n=== RECENT FEED ONLY (Sep 2024+; train 1y / test 3m) ===")
    print("auc = mean OOS AUC | randR = mean R of random entry | top20R/N = mean R / count of top 20% probs")
    print(table.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())