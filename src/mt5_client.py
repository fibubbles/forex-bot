"""Thin, safe wrapper around the MetaTrader5 package."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

from src.config_schema import Secrets
from src.timeutils import server_epoch_to_utc

log = logging.getLogger(__name__)

TIMEFRAMES: dict[str, int] = {
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


class MT5Error(RuntimeError):
    pass


class MT5Client:
    def __init__(self, secrets: Secrets, terminal_path: str | None = None) -> None:
        self._secrets = secrets
        self._terminal_path = terminal_path
        self._connected = False

    # --- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        kwargs = {
            "login": self._secrets.mt5_login,
            "password": self._secrets.mt5_password.get_secret_value(),
            "server": self._secrets.mt5_server,
            "timeout": 15_000,  # ms — fail fast instead of hanging
        }
        if self._terminal_path:
            kwargs["path"] = self._terminal_path

        log.info("Connecting to MT5 (%s)...", self._secrets.mt5_server)
        if not mt5.initialize(**kwargs):
            raise MT5Error(f"MT5 initialize failed: {mt5.last_error()}")
        self._connected = True

        term = mt5.terminal_info()
        if term is None or not term.trade_allowed:
            log.warning("Algo Trading is DISABLED in the MT5 terminal")
        log.info("Connected to %s", self._secrets.mt5_server)

    def shutdown(self) -> None:
        if self._connected:
            mt5.shutdown()
            self._connected = False
            log.info("MT5 connection closed")

    def __enter__(self) -> "MT5Client":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # --- queries ---------------------------------------------------------
    def account(self):
        acc = mt5.account_info()
        if acc is None:
            raise MT5Error(f"account_info failed: {mt5.last_error()}")
        return acc

    def find_symbols(self, pattern: str) -> list[str]:
        return [s.name for s in (mt5.symbols_get(pattern) or ())]

    def symbol(self, name: str):
        if not mt5.symbol_select(name, True):
            raise MT5Error(f"Cannot select symbol {name}: {mt5.last_error()}")
        info = mt5.symbol_info(name)
        if info is None:
            raise MT5Error(f"symbol_info failed for {name}: {mt5.last_error()}")
        return info

    def server_utc_offset_hours(self, symbol: str) -> int:
        """Estimate broker server offset from UTC. Only valid while market is open."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5Error(f"No tick for {symbol}: {mt5.last_error()}")
        diff_sec = tick.time - datetime.now(timezone.utc).timestamp()
        return round(diff_sec / 3600)

    def get_rates(self, symbol: str, timeframe: str, count: int, chunk: int = 5000) -> pd.DataFrame:
        """Fetch up to `count` latest bars in chunks, with UTC times.

        Chunking keeps each IPC request small, so a slow history download
        on the terminal side doesn't break the connection.
        """
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        self.symbol(symbol)
        tf = TIMEFRAMES[timeframe]

        parts: list[pd.DataFrame] = []
        pos = 0
        while pos < count:
            n = min(chunk, count - pos)
            rates = mt5.copy_rates_from_pos(symbol, tf, pos, n)
            got = 0 if rates is None else len(rates)
            log.info("Bars %d-%d: received %d", pos, pos + n, got)

            if got == 0:
                if pos == 0:
                    raise MT5Error(f"copy_rates_from_pos failed for {symbol} {timeframe}: {mt5.last_error()}")
                log.warning("No more history at position %d: %s", pos, mt5.last_error())
                break

            parts.append(pd.DataFrame(rates))
            if got < n:  # reached the oldest bar the broker provides
                break
            pos += n
            time.sleep(0.5)

        df = pd.concat(parts, ignore_index=True)  # overlaps are removed by data_checks
        df["time"] = server_epoch_to_utc(df["time"])
        return df[["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]]

    def positions(self, symbol: str):
        """All open positions on `symbol` (ours and manual ones). Empty tuple if none."""
        pos = mt5.positions_get(symbol=symbol)
        if pos is None:
            raise MT5Error(f"positions_get failed for {symbol}: {mt5.last_error()}")
        return pos

    def tick(self, symbol: str):
        """Latest bid/ask for `symbol`."""
        t = mt5.symbol_info_tick(symbol)
        if t is None:
            raise MT5Error(f"symbol_info_tick failed for {symbol}: {mt5.last_error()}")
        return t