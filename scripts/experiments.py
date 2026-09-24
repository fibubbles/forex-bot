"""Phase 3 diagnostics: is the pre-2024 edge a data artifact?"""
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
CUT = pd.Timestamp("2023-10-06", tz="UTC")
CUT_NAIVE = pd.Timestamp("2023-10-06")

SHORT_TERM = {"willr_14", "rsi_8", "range_pos_20", "ret_1"}
SLOW_FEATURES = [f for f in FEATURE_COLUMNS if f not in SHORT_TERM]

EXPERIMENTS = [
    ("baseline", "next_open", FEATURE_COLUMNS),
    ("A: entry@close", "close", FEATURE_COLUMNS),
    ("B: slow feats", "next_open", SLOW_FEATURES),
    ("A+B", "close", SLOW_FEATURES),
]


def summarize(name: str, preds: pd.DataFrame, metrics: pd.DataFrame) -> dict:
    fold_start = pd.to_datetime(metrics["test_start"])
    row = {"experiment": name}
    for period, mask in (("pre", fold_start < CUT_NAIVE), ("post", fold_start >= CUT_NAIVE)):
        for side in SIDES:
            row[f"auc_{side[0]}_{period}"] = metrics.loc[mask, f"auc_{side}"].mean()

    post = preds[preds["time"] >= CUT]
    for side in SIDES:
        sel = post[post[f"prob_{side}"] >= 0.50]
        row[f"postR_{side[0]}"] = sel[f"{side}_r"].mean() if len(sel) else float("nan")
        row[f"postN_{side[0]}"] = len(sel)
    return row


def main() -> int:
    cfg = load_config()
    raw = load_bars(bars_path(RAW_DIR, cfg.broker.symbol, cfg.broker.timeframe))
    feats = build_features(raw)

    rows = []
    for name, entry_at, features in EXPERIMENTS:
        print(f"Running {name} ...", flush=True)
        data = triple_barrier_labels(feats, spread=SPREAD_POINTS * POINT, entry_at=entry_at)
        preds, metrics = walk_forward(data, make_folds(data["time"]), features)
        rows.append(summarize(name, preds, metrics))

    table = pd.DataFrame(rows).set_index("experiment").round(3)
    print("\n=== EXPERIMENTS ===")
    print("auc_*_pre = folds before 2023-10 | auc_*_post = after | postR/postN = mean R / signals after 2023-10 at prob>=0.50")
    print(table.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())