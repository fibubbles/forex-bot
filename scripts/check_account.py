"""Read-only account/symbol check: spec, margin, and loss at the minimum lot for ATR-based stops."""
import argparse
import sys
import time

import MetaTrader5 as mt5

from src.config_schema import load_config, load_secrets
from src.data_checks import validate_bars
from src.features import atr
from src.mt5_client import MT5Client
from src.risk import SymbolSpec, position_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--env", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config, args.env)
    sym = cfg.broker.symbol
    with MT5Client(load_secrets(args.env), cfg.broker.terminal_path) as client:
        acc = client.account()
        print(f"\nAccount {acc.login} @ {acc.server} | {acc.currency} | balance {acc.balance} | "
              f"equity {acc.equity} | leverage 1:{acc.leverage} | trade_allowed={acc.trade_allowed}")

        gold = client.find_symbols("*XAU*")
        print(f"Gold symbols on this account: {gold}")
        if sym not in gold:
            print(f"\n'{sym}' not found. Put the right name from the list above in the config.")
            return 1

        info = client.symbol(sym)
        spec = SymbolSpec.from_mt5(info)
        tick = client.tick(sym)
        for _ in range(10):  # a newly selected symbol may need a moment to receive quotes
            if tick.bid > 0:
                break
            time.sleep(0.5)
            tick = client.tick(sym)
        if tick.bid <= 0:
            print(f"No live quote for {sym}: market closed, or the symbol is not tradeable on this account")
            return 1
        margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, sym, info.volume_min, tick.ask)
        print(f"{sym}: digits {info.digits} | contract {info.trade_contract_size} | min lot {info.volume_min} | "
              f"step {info.volume_step} | spread {info.spread} pts | tick value {info.trade_tick_value}")
        print(f"Price {tick.bid}/{tick.ask} | margin for {info.volume_min} lot: {margin}")

        bars, _ = validate_bars(client.get_rates(sym, cfg.broker.timeframe, 300), cfg.broker.timeframe)
        atr14 = float(atr(bars, 14).iloc[-1])
        print(f"ATR(14) on {cfg.broker.timeframe}: {atr14:.{info.digits}f}")

        print(f"\nIf the stop loss is hit at the minimum lot ({info.volume_min}):")
        for k in (1.0, 1.5, 2.0):
            dist = k * atr14
            loss = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, info.volume_min, tick.ask, tick.ask - dist)
            pct = abs(loss) / acc.equity * 100 if acc.equity > 0 else float("nan")
            lots = position_size(acc.equity, 1.0, dist, spec) if acc.equity > 0 else 0.0
            print(f"  SL {k} x ATR ({dist:.2f}): loss {loss:.2f} {acc.currency} ({pct:.1f}% of equity) | "
                  f"lots allowed at 1% risk: {lots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())