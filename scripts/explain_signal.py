"""Show what the strategy sees on the latest closed bar and which conditions are met (read-only)."""
import sys

import MetaTrader5 as mt5

from src.config_schema import load_config, load_secrets
from src.data_checks import validate_bars
from src.features import build_features
from src.mt5_client import MT5Client
from src.strategy import TrendPullback

CONFIG, ENV = "config.live.yaml", ".env.cent"


def mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def main() -> int:
    cfg = load_config(CONFIG, ENV)
    if not mt5.initialize(path=cfg.broker.terminal_path, timeout=15000):  # attach only, no re-login
        print(f"MT5 initialize failed: {mt5.last_error()}")
        return 1
    try:
        client = MT5Client(load_secrets(ENV), cfg.broker.terminal_path)
        raw = client.get_rates(cfg.broker.symbol, cfg.broker.timeframe, 2500)
    finally:
        mt5.shutdown()  # closes THIS script's connection only

    clean, _ = validate_bars(raw, cfg.broker.timeframe)
    feats = build_features(clean)
    last = feats.iloc[-1]
    d1, e20, e50 = last["d1_trend"], last["dist_ema20"], last["dist_ema50"]

    print(f"\nLatest closed bar: {last['time']} | close {last['close']:.2f}")
    print(f"  D1 trend        : {d1:+.2f}  ({'UP' if d1 > 0 else 'DOWN'})")
    print(f"  distance EMA20  : {e20:+.2f} ATR ({'above' if e20 > 0 else 'below'})")
    print(f"  distance EMA50  : {e50:+.2f} ATR ({'above' if e50 > 0 else 'below'})")

    print("\nBUY needs:  D1 up, price below EMA20, price above EMA50")
    print(f"  {mark(d1 > 0)} D1 up   {mark(e20 < 0)} below EMA20   {mark(e50 > 0)} above EMA50")
    print("SELL needs: D1 down, price above EMA20, price below EMA50")
    print(f"  {mark(d1 < 0)} D1 down {mark(e20 > 0)} above EMA20   {mark(e50 < 0)} below EMA50")

    recent = feats.tail(120).copy()
    recent["dir"] = TrendPullback().direction(recent)
    signals = recent[recent["dir"] != 0]
    print(f"\nSignals in the last {len(recent)} H1 bars (about 5 trading days): {len(signals)}")
    for _, r in signals.iterrows():
        print(f"  {r['time']}  {'BUY ' if r['dir'] > 0 else 'SELL'}  close {r['close']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())