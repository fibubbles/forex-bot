"""Triple-barrier labels (v1).

For each bar t: enter at open of t+1, TP = tp_atr * ATR_t, SL = sl_atr * ATR_t,
max `horizon` bars. Label 1 if TP is hit first, else 0 (SL first or timeout).

Conservative rules:
- TP and SL inside the same bar -> assume SL hit first.
- Gap through a barrier -> filled at the bar open (worse than SL for stops).
- Bars are BID prices: longs enter at ask (+spread), shorts exit at ask.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LABEL_VERSION = "tb_v1"
TP_ATR = 1.5
SL_ATR = 1.0
HORIZON_BARS = 30
SIDES = ("long", "short")


def _barrier_outcome(i, side, o, h, l, c, a, tp_atr, sl_atr, horizon, spread, entry_at="next_open"):
    """Return (label, r_multiple, bars_held) for one entry decided at bar i."""
    risk = sl_atr * a[i]
    is_long = side == "long"

    base = o[i + 1] if entry_at == "next_open" else c[i]
    if is_long:
        entry = base + spread
        tp, sl = entry + tp_atr * a[i], entry - risk
    else:
        entry = base
        tp, sl = entry - tp_atr * a[i], entry + risk

    for j in range(i + 1, i + 1 + horizon):
        if is_long:
            if l[j] <= sl:  # SL checked first: worst case when both hit
                return 0.0, (min(o[j], sl) - entry) / risk, j - i
            if h[j] >= tp:
                return 1.0, (max(o[j], tp) - entry) / risk, j - i
        else:
            ask_open, ask_high, ask_low = o[j] + spread, h[j] + spread, l[j] + spread
            if ask_high >= sl:
                return 0.0, (entry - max(ask_open, sl)) / risk, j - i
            if ask_low <= tp:
                return 1.0, (entry - min(ask_open, tp)) / risk, j - i

    last = i + horizon
    if is_long:
        return 0.0, (c[last] - entry) / risk, horizon
    return 0.0, (entry - (c[last] + spread)) / risk, horizon


def triple_barrier_labels(
    feats: pd.DataFrame,
    tp_atr: float = TP_ATR,
    sl_atr: float = SL_ATR,
    horizon: int = HORIZON_BARS,
    spread: float = 0.0,
    entry_at: str = "next_open",
) -> pd.DataFrame:
    """Add {long,short}_{label,r,bars}. Last `horizon` rows stay NaN (no full future).

    entry_at: "next_open" (default, realistic) or "close" (diagnostic only).
    """
    if entry_at not in ("next_open", "close"):
        raise ValueError(f"entry_at must be 'next_open' or 'close', got {entry_at!r}")

    o, h, l, c, a = (feats[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "atr"))
    n = len(feats)
    out = {f"{s}_{f}": np.full(n, np.nan) for s in SIDES for f in ("label", "r", "bars")}

    for i in range(n - horizon):
        for side in SIDES:
            label, r, held = _barrier_outcome(
                i, side, o, h, l, c, a, tp_atr, sl_atr, horizon, spread, entry_at
            )
            out[f"{side}_label"][i] = label
            out[f"{side}_r"][i] = r
            out[f"{side}_bars"][i] = held

    return feats.assign(**out)