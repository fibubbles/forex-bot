"""Real order execution through MT5 (DEMO accounts only for now).

Safety:
- Refuses to trade unless the account is a DEMO account on a server named '*demo*'.
- Every order carries SL and TP. After filling, the position is verified; if the broker
  did not attach the stop loss, the position is closed immediately.
- order_check runs before order_send. Requotes / price changes are retried with a fresh
  price (max 3 attempts); every other rejection fails immediately.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
DEVIATION_POINTS = 20

# Bit flags in symbol_info().filling_mode (MQL5 SYMBOL_FILLING_*).
# The MetaTrader5 Python package does not export these, so they are defined here.
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2


class OrderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fill:
    ticket: int            # position ticket
    price: float           # actual fill price
    requested_price: float
    lots: float
    sl: float
    tp: float


def assert_demo_account(account_info, demo_trade_mode: int) -> None:
    server = (account_info.server or "").lower()
    if account_info.trade_mode != demo_trade_mode or "demo" not in server:
        raise OrderError(
            f"Refusing to trade: account {account_info.login} on '{account_info.server}' is not a demo account"
        )


def pick_filling(symbol_filling_flags: int, api) -> int:
    """Choose an order filling mode the broker supports for this symbol."""
    if symbol_filling_flags & SYMBOL_FILLING_FOK:
        return api.ORDER_FILLING_FOK
    if symbol_filling_flags & SYMBOL_FILLING_IOC:
        return api.ORDER_FILLING_IOC
    return api.ORDER_FILLING_RETURN


class LiveBroker:
    def __init__(self, api, symbol: str, magic: int, digits: int,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.api = api
        self.symbol = symbol
        self.magic = magic
        self.digits = digits
        self._sleep = sleep
        info = api.symbol_info(symbol)
        if info is None:
            raise OrderError(f"symbol_info failed for {symbol}: {api.last_error()}")
        self.filling = pick_filling(info.filling_mode, api)
        self._retry_codes = {api.TRADE_RETCODE_REQUOTE, api.TRADE_RETCODE_PRICE_CHANGED,
                             api.TRADE_RETCODE_PRICE_OFF}

    # --- queries -------------------------------------------------------------
    def positions(self) -> list:
        pos = self.api.positions_get(symbol=self.symbol)
        if pos is None:
            raise OrderError(f"positions_get failed: {self.api.last_error()}")
        return [p for p in pos if p.magic == self.magic]

    def _tick(self):
        tick = self.api.symbol_info_tick(self.symbol)
        if tick is None:
            raise OrderError(f"no tick for {self.symbol}: {self.api.last_error()}")
        return tick

    # --- open ----------------------------------------------------------------
    def open(self, side: str, lots: float, sl_distance: float, tp_distance: float,
             comment: str = "forex-bot") -> Fill:
        last = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            tick = self._tick()
            if side == "long":
                price = tick.ask
                sl, tp = price - sl_distance, price + tp_distance
                order_type = self.api.ORDER_TYPE_BUY
            else:
                price = tick.bid
                sl, tp = price + sl_distance, price - tp_distance
                order_type = self.api.ORDER_TYPE_SELL

            req = {
                "action": self.api.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": lots,
                "type": order_type,
                "price": price,
                "sl": round(sl, self.digits),
                "tp": round(tp, self.digits),
                "deviation": DEVIATION_POINTS,
                "magic": self.magic,
                "comment": comment[:31],
                "type_time": self.api.ORDER_TIME_GTC,
                "type_filling": self.filling,
            }

            check = self.api.order_check(req)
            if check is None or check.retcode != 0:
                raise OrderError(f"order_check rejected: {getattr(check, 'retcode', None)} "
                                 f"{getattr(check, 'comment', '')} {self.api.last_error()}")

            res = self.api.order_send(req)
            if res is None:
                raise OrderError(f"order_send returned None: {self.api.last_error()}")
            if res.retcode == self.api.TRADE_RETCODE_DONE:
                return self._verify(res.order, price)
            if res.retcode in self._retry_codes:
                log.warning("Attempt %d: retcode %s (%s), retrying", attempt, res.retcode, res.comment)
                last = res
                self._sleep(0.5)
                continue
            raise OrderError(f"order_send failed: {res.retcode} {res.comment}")

        raise OrderError(f"order_send failed after {MAX_ATTEMPTS} attempts: "
                         f"{getattr(last, 'retcode', '?')} {getattr(last, 'comment', '')}")

    def _find_position(self, order_ticket: int):
        for _ in range(5):
            for p in self.positions():
                if p.ticket == order_ticket or getattr(p, "identifier", None) == order_ticket:
                    return p
            self._sleep(0.3)
        return None

    def _verify(self, order_ticket: int, requested_price: float) -> Fill:
        pos = self._find_position(order_ticket)
        if pos is None:
            raise OrderError(f"Order {order_ticket} filled but position not found: CHECK MT5 MANUALLY")
        if not pos.sl:
            log.critical("Position %s has NO stop loss: closing immediately", pos.ticket)
            self.close(pos, reason="missing SL")
            raise OrderError(f"Position {pos.ticket} had no SL and was closed immediately")
        log.info("Opened #%s %s lots @ %s | SL %s | TP %s", pos.ticket, pos.volume, pos.price_open, pos.sl, pos.tp)
        return Fill(ticket=pos.ticket, price=pos.price_open, requested_price=requested_price,
                    lots=pos.volume, sl=pos.sl, tp=pos.tp)

    # --- close ---------------------------------------------------------------
    def close(self, pos, reason: str = "") -> float:
        """Close one position at market. Returns the exit price."""
        is_long = pos.type == self.api.POSITION_TYPE_BUY
        last = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            tick = self._tick()
            req = {
                "action": self.api.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": pos.volume,
                "type": self.api.ORDER_TYPE_SELL if is_long else self.api.ORDER_TYPE_BUY,
                "position": pos.ticket,
                "price": tick.bid if is_long else tick.ask,
                "deviation": DEVIATION_POINTS,
                "magic": self.magic,
                "comment": f"close {reason}"[:31],
                "type_time": self.api.ORDER_TIME_GTC,
                "type_filling": self.filling,
            }
            res = self.api.order_send(req)
            if res is not None and res.retcode == self.api.TRADE_RETCODE_DONE:
                log.info("Closed #%s @ %s (%s)", pos.ticket, res.price, reason)
                return res.price
            if res is not None and res.retcode in self._retry_codes:
                last = res
                self._sleep(0.5)
                continue
            raise OrderError(f"close #{pos.ticket} failed: {getattr(res, 'retcode', None)} "
                             f"{getattr(res, 'comment', '')} {self.api.last_error()}")
        raise OrderError(f"close #{pos.ticket} failed after {MAX_ATTEMPTS} attempts: "
                         f"{getattr(last, 'retcode', '?')}")

    def close_all(self, reason: str) -> list[int]:
        closed = []
        for p in self.positions():   # only our magic number: manual trades are never touched
            self.close(p, reason)
            closed.append(p.ticket)
        return closed