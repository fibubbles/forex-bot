"""Phase 1 sanity check: connection, account type, symbol specs, server time."""
import logging

from src.config_schema import load_config, load_secrets
from src.mt5_client import MT5Client

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


def main() -> None:
    cfg = load_config()
    secrets = load_secrets()

    with MT5Client(secrets, cfg.broker.terminal_path) as client:
        acc = client.account()
        print("\n=== ACCOUNT ===")
        print(f"server={acc.server} | company={acc.company}")
        print(f"currency={acc.currency} | balance={acc.balance} | leverage=1:{acc.leverage}")
        print(f"trade_allowed={acc.trade_allowed} | trade_expert={acc.trade_expert}")
        print(f"margin_call={acc.margin_so_call}% | stop_out={acc.margin_so_so}%")

        print("\n=== EURUSD SYMBOLS ===")
        symbols = client.find_symbols("*EURUSD*")
        if not symbols:
            print("No EURUSD symbol found!")
            return
        for name in symbols:
            s = client.symbol(name)
            print(
                f"{name}: digits={s.digits} contract={s.trade_contract_size} "
                f"vol_min={s.volume_min} vol_step={s.volume_step} "
                f"spread_pts={s.spread} tick_value={s.trade_tick_value}"
            )

        if cfg.broker.symbol not in symbols:
            print(f"\nWARNING: config symbol '{cfg.broker.symbol}' not found. Update config.yaml.")

        ref = cfg.broker.symbol if cfg.broker.symbol in symbols else symbols[0]
        print("\n=== SERVER TIME ===")
        print(f"Server UTC offset ≈ {client.server_utc_offset_hours(ref):+d} hours")


if __name__ == "__main__":
    main()