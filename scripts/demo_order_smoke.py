"""Demo smoke test: open the minimum lot on EURUSD with SL/TP, verify it, then close it."""
import logging
import sys
import time

import MetaTrader5 as mt5

from src.broker import LiveBroker, OrderError, assert_demo_account
from src.config_schema import load_config, load_secrets
from src.mt5_client import MT5Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

CONFIG, ENV = "config.demo.yaml", ".env.demo"


def main() -> int:
    cfg = load_config(CONFIG)
    with MT5Client(load_secrets(ENV), cfg.broker.terminal_path) as client:
        acc = client.account()
        assert_demo_account(acc, mt5.ACCOUNT_TRADE_MODE_DEMO)
        info = client.symbol(cfg.broker.symbol)
        broker = LiveBroker(mt5, cfg.broker.symbol, cfg.broker.magic_number, info.digits)
        print(f"\nAccount {acc.login} on {acc.server} (DEMO) | balance {acc.balance} {acc.currency}")

        try:
            fill = broker.open("long", info.volume_min, sl_distance=0.0030, tp_distance=0.0040,
                               comment="smoke test")
        except OrderError as e:
            print(f"\nORDER FAILED: {e}")
            print("If the code is 10027, turn ON the Algo Trading button in MT5 and retry.")
            return 1

        pos = next(p for p in broker.positions() if p.ticket == fill.ticket)
        slip = (fill.price - fill.requested_price) / info.point
        print(f"OPENED #{fill.ticket} BUY {fill.lots} @ {fill.price} | SL {pos.sl} | TP {pos.tp} | "
              f"slippage {slip:+.1f} pts")

        print("Holding 10 seconds: look at the Trade tab in MT5 ...")
        time.sleep(10)

        exit_price = broker.close(pos, reason="smoke test")
        print(f"CLOSED #{fill.ticket} @ {exit_price}")
        print(f"Bot positions remaining: {len(broker.positions())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())