"""Read-only check for the micro live experiment: margin and loss per SL at the minimum lot."""
import sys

import MetaTrader5 as mt5

from src.config_schema import load_config, load_secrets
from src.mt5_client import MT5Client

CONFIG, ENV = "config.valetax.yaml", ".env.valetax"
FLOOR = 5.0


def main() -> int:
    cfg = load_config(CONFIG)
    with MT5Client(load_secrets(ENV), cfg.broker.terminal_path) as client:
        acc = client.account()
        info = client.symbol(cfg.broker.symbol)
        tick = client.tick(cfg.broker.symbol)
        lot = info.volume_min
        margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, cfg.broker.symbol, lot, tick.ask)

        print(f"\nAccount {acc.login} @ {acc.server} | {acc.currency} | balance {acc.balance} | "
              f"equity {acc.equity} | leverage 1:{acc.leverage}")
        print(f"margin call {acc.margin_so_call}% | stop out {acc.margin_so_so}% | trade_allowed={acc.trade_allowed}")
        print(f"{cfg.broker.symbol}: min lot {lot} | contract {info.trade_contract_size} | spread {info.spread} pts")
        print(f"Margin for {lot} lot: {margin} -> free margin after open: {acc.equity - (margin or 0):.2f}")

        print("\nLoss if SL is hit at min lot:")
        for pips in (20, 30, 40):
            sl_price = tick.ask - pips * 10 * info.point
            loss = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, cfg.broker.symbol, lot, tick.ask, sl_price)
            left = (acc.equity - FLOOR) / abs(loss) if loss else float("nan")
            print(f"  {pips} pips: {loss:.2f} ({abs(loss) / acc.equity * 100:.0f}% of equity) | "
                  f"~{left:.1f} losses until the ${FLOOR:.0f} floor")
    return 0


if __name__ == "__main__":
    sys.exit(main())