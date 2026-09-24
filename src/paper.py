"""Paper broker for dry_run: realistic simulated fills, no orders sent.

Same conservative rules as labeling:
- SL is checked before TP within one bar.
- Gaps through a barrier fill at the bar open.
- Bars are BID prices: longs enter at ask and exit at bid; shorts the reverse.
"""
from __future__ import annotations

import logging

from src.db import TradeLog
from src.risk import SymbolSpec

log = logging.getLogger(__name__)

MODE = "dry_run"


class PaperBroker:
    def __init__(self, trade_log: TradeLog, spec: SymbolSpec, spread_points: float) -> None:
        self.log = trade_log
        self.spec = spec
        self.spread = spread_points * spec.point

    def open_trades(self) -> list[dict]:
        return [t for t in self.log.open_trades() if t["mode"] == MODE]

    def open(self, side: str, lots: float, bid: float, ask: float,
             sl: float, tp: float | None, strategy: str, spread_points: float) -> int:
        ticket = self.log.next_paper_ticket()
        price = ask if side == "long" else bid
        self.log.record_open(ticket, MODE, strategy, side, lots, price, price, sl, tp, spread_points)
        log.info("PAPER OPEN #%d %s %.2f @ %.5f | SL %.5f | TP %s", ticket, side, lots, price, sl, tp)
        return ticket

    def _profit(self, t: dict, exit_price: float) -> float:
        move = exit_price - t["entry_price"] if t["side"] == "long" else t["entry_price"] - exit_price
        return move / self.spec.tick_size * self.spec.tick_value * t["lots"]

    def _close(self, t: dict, exit_price: float, why: str) -> None:
        profit = round(self._profit(t, exit_price), 2)
        self.log.record_close(t["ticket"], exit_price, profit)
        log.info("PAPER CLOSE #%d (%s) @ %.5f | profit %.2f", t["ticket"], why, exit_price, profit)

    def on_bar(self, o: float, h: float, l: float, c: float) -> list[int]:
        """Check every open paper trade against one newly CLOSED bar (bid prices)."""
        closed = []
        for t in self.open_trades():
            sl, tp = t["sl"], t["tp"]
            if t["side"] == "long":
                if l <= sl:
                    exit_price, why = min(o, sl), "SL"
                elif tp is not None and h >= tp:
                    exit_price, why = max(o, tp), "TP"
                else:
                    continue
            else:
                ask_o, ask_h, ask_l = o + self.spread, h + self.spread, l + self.spread
                if ask_h >= sl:
                    exit_price, why = max(ask_o, sl), "SL"
                elif tp is not None and ask_l <= tp:
                    exit_price, why = min(ask_o, tp), "TP"
                else:
                    continue
            self._close(t, exit_price, why)
            closed.append(t["ticket"])
        return closed

    def close_all(self, bid: float, ask: float, why: str = "manual") -> list[int]:
        closed = []
        for t in self.open_trades():
            self._close(t, bid if t["side"] == "long" else ask, why)
            closed.append(t["ticket"])
        return closed

    def floating_profit(self, bid: float, ask: float) -> float:
        """Unrealised P/L of open paper trades at current prices."""
        return sum(self._profit(t, bid if t["side"] == "long" else ask) for t in self.open_trades())