"""Trading backends behind one interface, so the executor does not care which is used.

- PaperTrading: dry_run, simulated fills (PaperBroker checks SL/TP on each bar).
- LiveTrading:  real orders through MT5 (LiveBroker). The broker's server holds SL/TP;
  closes are detected by comparing our open trades with MT5 positions and are read from
  the broker's deal history, so recorded results match the broker exactly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from src.broker import LiveBroker
from src.db import TradeLog
from src.paper import PaperBroker
from src.strategy import Signal

log = logging.getLogger(__name__)

DEAL_ENTRY_OUT = 1  # MT5 deal entry type for a closing deal


@dataclass(frozen=True)
class Opened:
    ticket: int
    entry: float
    sl: float
    tp: float | None


class PaperTrading:
    mode = "dry_run"

    def __init__(self, paper: PaperBroker) -> None:
        self.paper = paper

    def open_trades(self) -> list[dict]:
        return self.paper.open_trades()

    def open_count(self) -> int:
        return len(self.paper.open_trades())

    def floating_profit(self, bid: float, ask: float) -> float:
        return self.paper.floating_profit(bid, ask)

    def check_exits(self, new_bars: pd.DataFrame) -> list[int]:
        closed: list[int] = []
        for b in new_bars.itertuples():
            closed += self.paper.on_bar(b.open, b.high, b.low, b.close)
        return closed

    def open(self, signal: Signal, lots: float, entry: float, sl: float, tp: float,
             bid: float, ask: float, spread_points: float) -> Opened:
        ticket = self.paper.open(signal.side, lots, bid, ask, sl, tp, signal.strategy, spread_points)
        return Opened(ticket, entry, sl, tp)

    def close_all(self, bid: float, ask: float, reason: str) -> list[int]:
        return self.paper.close_all(bid, ask, reason)

    def adopt_orphans(self) -> list[int]:
        return []


class LiveTrading:
    def __init__(self, broker: LiveBroker, trade_log: TradeLog, mode: str = "demo") -> None:
        self.broker = broker
        self.log = trade_log
        self.mode = mode

    def open_trades(self) -> list[dict]:
        return [t for t in self.log.open_trades() if t["mode"] == self.mode]

    def open_count(self) -> int:
        return len(self.broker.positions())  # MT5 is the source of truth

    def floating_profit(self, bid: float, ask: float) -> float:
        return sum(p.profit + getattr(p, "swap", 0.0) for p in self.broker.positions())

    def open(self, signal: Signal, lots: float, entry: float, sl: float, tp: float,
             bid: float, ask: float, spread_points: float) -> Opened:
        # Prices are recomputed from the live tick inside LiveBroker; `entry/sl/tp` are estimates only.
        fill = self.broker.open(signal.side, lots, signal.sl_distance, signal.tp_distance,
                                comment=signal.strategy)
        self.log.record_open(fill.ticket, self.mode, signal.strategy, signal.side, fill.lots,
                             fill.requested_price, fill.price, fill.sl, fill.tp, spread_points)
        return Opened(fill.ticket, fill.price, fill.sl, fill.tp)

    def check_exits(self, new_bars: pd.DataFrame | None = None) -> list[int]:
        """Record trades the broker closed (SL/TP hit, manual close) since the last check."""
        live = {p.ticket for p in self.broker.positions()}
        closed = []
        for t in self.open_trades():
            if t["ticket"] in live:
                continue
            deals = self.broker.api.history_deals_get(position=t["ticket"])
            outs = [d for d in (deals or ()) if d.entry == DEAL_ENTRY_OUT]
            if not outs:
                log.warning("Trade #%s is gone from MT5 but no closing deal yet; will retry", t["ticket"])
                continue
            profit = sum(d.profit + d.swap + d.commission + getattr(d, "fee", 0.0) for d in outs)
            self.log.record_close(t["ticket"], outs[-1].price, round(profit, 2))
            closed.append(t["ticket"])
        return closed

    def close_all(self, bid: float, ask: float, reason: str) -> list[int]:
        tickets = self.broker.close_all(reason)
        self.check_exits()  # record exits from the broker's deal history
        return tickets

    def adopt_orphans(self) -> list[int]:
        """Positions with our magic number that the DB does not know (e.g. crash right after a fill)."""
        known = {t["ticket"] for t in self.open_trades()}
        adopted = []
        for p in self.broker.positions():
            if p.ticket in known:
                continue
            side = "long" if p.type == self.broker.api.POSITION_TYPE_BUY else "short"
            self.log.record_open(p.ticket, self.mode, "adopted", side, p.volume,
                                 p.price_open, p.price_open, p.sl, p.tp, 0.0)
            log.warning("Adopted orphan position #%s", p.ticket)
            adopted.append(p.ticket)
        return adopted