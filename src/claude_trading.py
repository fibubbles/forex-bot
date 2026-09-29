"""Order tools for Claude Code (via src/mcp_server.py), added on the user's explicit decision.

Claude may choose side, SL and TP. Everything else is fixed here and cannot be changed by the
model: the symbol, the lot size (micro_live.fixed_lot), the account, and these hard rules:
- SL and TP are mandatory and must be on the correct side of the price.
- One position at a time on the symbol (any magic: Claude never stacks on the bot's trade).
- The trade's worst-case loss may not take equity below the equity floor, and may not risk
  more than MAX_RISK_PCT of equity.
- No entries when spread > MAX_SPREAD_POINTS, after the Friday cutoff, or after
  MAX_ORDERS_PER_DAY Claude orders today (stops a runaway loop).
- Close/modify only Claude's own positions (CLAUDE magic); SL may only be moved to reduce risk.
Orders go through LiveBroker (order_check first, requote retry, close immediately if SL missing).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.broker import LiveBroker, OrderError, assert_live_account_allowed
from src.timeutils import is_past_friday_cutoff

log = logging.getLogger(__name__)

CLAUDE_MAGIC_OFFSET = 1          # Claude's positions use bot magic + 1
MAX_RISK_PCT = 25.0              # of equity, per trade
MAX_SPREAD_POINTS = 60           # ~2x the usual 29 points on XAUUSD.vxc
MAX_ORDERS_PER_DAY = 5


@dataclass(frozen=True)
class Market:
    bid: float
    ask: float
    point: float
    tick_size: float
    tick_value: float
    stops_level_points: int
    digits: int


@dataclass(frozen=True)
class Limits:
    lots: float
    equity_floor: float
    friday_hours: float


@dataclass
class Check:
    ok: bool
    side: str = ""
    entry: float = 0.0
    sl_distance: float = 0.0
    tp_distance: float = 0.0
    risk: float = 0.0
    reward: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def text(self, symbol: str, lots: float) -> str:
        head = (f"{self.side.upper()} {symbol} {lots} lot @ ~{self.entry} | SL dist {self.sl_distance:.2f} "
                f"(risk ~{self.risk:.2f}) | TP dist {self.tp_distance:.2f} (reward ~{self.reward:.2f})")
        return head if self.ok else head + "\nREFUSED:\n- " + "\n- ".join(self.reasons)


def validate(side: str, sl: float, tp: float, m: Market, lim: Limits, equity: float,
             open_positions: int, orders_today: int, now_utc: datetime) -> Check:
    """Pure function: every hard rule, all failures reported together."""
    reasons: list[str] = []
    side = str(side).lower()
    if side not in ("buy", "sell"):
        return Check(False, reasons=[f"side must be 'buy' or 'sell', got {side!r}"])
    try:
        sl, tp = float(sl), float(tp)
    except (TypeError, ValueError):
        return Check(False, side=side, reasons=["sl and tp must be prices (numbers)"])

    entry = m.ask if side == "buy" else m.bid
    sl_dist = entry - sl if side == "buy" else sl - entry
    tp_dist = tp - entry if side == "buy" else entry - tp
    value_per_price = lim.lots / m.tick_size * m.tick_value  # account currency per 1.00 move
    risk, reward = max(sl_dist, 0) * value_per_price, max(tp_dist, 0) * value_per_price
    spread = (m.ask - m.bid) / m.point
    min_dist = m.stops_level_points * m.point

    if sl_dist <= 0:
        reasons.append(f"SL {sl} is on the wrong side of the entry {entry}")
    elif sl_dist <= min_dist:
        reasons.append(f"SL too close: {sl_dist:.2f} <= broker stops level {min_dist:.2f}")
    if tp_dist <= 0:
        reasons.append(f"TP {tp} is on the wrong side of the entry {entry}")
    if open_positions > 0:
        reasons.append(f"{open_positions} position(s) already open on the symbol: one at a time")
    if equity < lim.equity_floor:
        reasons.append(f"equity {equity:.2f} is below the floor {lim.equity_floor:.2f}")
    elif equity - risk < lim.equity_floor:
        reasons.append(f"a stop-out would leave {equity - risk:.2f} < floor {lim.equity_floor:.2f}")
    if equity > 0 and risk / equity * 100 > MAX_RISK_PCT:
        reasons.append(f"risk {risk:.2f} is {risk / equity * 100:.0f}% of equity (max {MAX_RISK_PCT:.0f}%)")
    if spread > MAX_SPREAD_POINTS:
        reasons.append(f"spread {spread:.0f} pts > {MAX_SPREAD_POINTS}")
    if is_past_friday_cutoff(lim.friday_hours, now_utc):
        reasons.append("Friday cutoff: no new entries before the weekend")
    if orders_today >= MAX_ORDERS_PER_DAY:
        reasons.append(f"daily limit reached: {orders_today}/{MAX_ORDERS_PER_DAY} Claude orders today")

    return Check(not reasons, side, round(entry, m.digits), sl_dist, tp_dist, risk, reward, reasons)


class ClaudeTrader:
    """Wraps LiveBroker with Claude's magic number and the rules above."""

    def __init__(self, api, cfg: dict, notify=None, audit=None) -> None:
        self.api, self.cfg = api, cfg
        self.magic = cfg["magic"] + CLAUDE_MAGIC_OFFSET
        self.symbol = cfg["symbol"]
        self.limits = Limits(cfg["fixed_lot"], cfg["equity_floor"], cfg["friday_hours"])
        self._notify = notify or (lambda text: None)
        self._audit = audit or (lambda kind, text: None)

    def _guard(self):
        acc = self.api.account_info()
        if acc is None:
            raise OrderError(f"account_info failed: {self.api.last_error()}")
        assert_live_account_allowed(acc, self.cfg["account_login"], self.cfg["server"])
        term = self.api.terminal_info()
        if not acc.trade_allowed or term is None or not term.trade_allowed:
            raise OrderError("Algo Trading is OFF in the MT5 terminal")
        return acc

    def _market(self) -> Market:
        self.api.symbol_select(self.symbol, True)
        info, tick = self.api.symbol_info(self.symbol), self.api.symbol_info_tick(self.symbol)
        if info is None or tick is None:
            raise OrderError(f"no symbol data for {self.symbol}: {self.api.last_error()}")
        return Market(tick.bid, tick.ask, info.point, info.trade_tick_size, info.trade_tick_value,
                      info.trade_stops_level, info.digits)

    def _orders_today(self, now: datetime) -> int:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        deals = self.api.history_deals_get(start - timedelta(hours=12), now + timedelta(days=1)) or ()
        return sum(1 for d in deals if d.magic == self.magic and d.entry == 0)  # 0 = DEAL_ENTRY_IN

    def check(self, side: str, sl: float, tp: float, now: datetime | None = None) -> Check:
        now = now or datetime.now(timezone.utc)
        acc = self._guard()
        open_n = len(self.api.positions_get(symbol=self.symbol) or ())
        return validate(side, sl, tp, self._market(), self.limits, acc.equity, open_n,
                        self._orders_today(now), now)

    def place(self, side: str, sl: float, tp: float, reason: str) -> str:
        c = self.check(side, sl, tp)
        lots = self.limits.lots
        if not c.ok:
            self._audit("claude_refused", c.text(self.symbol, lots))
            return c.text(self.symbol, lots)
        broker = LiveBroker(self.api, self.symbol, self.magic, self._market().digits, max_lot=lots)
        fill = broker.open("long" if c.side == "buy" else "short", lots, c.sl_distance, c.tp_distance,
                           comment="claude")
        msg = (f"🤖 CLAUDE {c.side.upper()} {lots} {self.symbol} #{fill.ticket}\nEntry {fill.price}\n"
               f"SL {fill.sl} | TP {fill.tp}\nRisk ~{c.risk:.2f}\nWhy: {reason[:300]}")
        self._audit("claude_open", msg)
        self._notify(msg)
        return msg

    def _own(self, ticket: int):
        for p in self.api.positions_get(symbol=self.symbol) or ():
            if p.ticket == int(ticket):
                if p.magic != self.magic:
                    raise OrderError(f"#{ticket} is not Claude's position (bot or manual): not touched")
                return p
        raise OrderError(f"no open position #{ticket} on {self.symbol}")

    def close(self, ticket: int, reason: str) -> str:
        self._guard()
        p = self._own(ticket)
        price = LiveBroker(self.api, self.symbol, self.magic, self._market().digits).close(p, "claude")
        msg = f"🤖 CLAUDE CLOSE #{ticket} @ {price} (floating P/L was {p.profit:.2f})\nWhy: {reason[:300]}"
        self._audit("claude_close", msg)
        self._notify(msg)
        return msg

    def modify(self, ticket: int, sl: float | None, tp: float | None, reason: str) -> str:
        self._guard()
        p = self._own(ticket)
        m = self._market()
        is_buy = p.type == self.api.POSITION_TYPE_BUY
        new_sl = p.sl if sl is None else round(float(sl), m.digits)
        new_tp = p.tp if tp is None else round(float(tp), m.digits)
        if not new_sl:
            raise OrderError("SL cannot be removed")
        if (is_buy and new_sl < p.sl) or (not is_buy and new_sl > p.sl):
            raise OrderError(f"SL may only move to reduce risk (current {p.sl}, asked {new_sl})")
        price = m.bid if is_buy else m.ask
        if (is_buy and new_sl >= price) or (not is_buy and new_sl <= price):
            raise OrderError(f"SL {new_sl} would be on the wrong side of the current price {price}")
        if new_tp and ((is_buy and new_tp <= price) or (not is_buy and new_tp >= price)):
            raise OrderError(f"TP {new_tp} would be on the wrong side of the current price {price}")
        res = self.api.order_send({"action": self.api.TRADE_ACTION_SLTP, "symbol": self.symbol,
                                   "position": p.ticket, "sl": new_sl, "tp": new_tp, "magic": self.magic})
        if res is None or res.retcode != self.api.TRADE_RETCODE_DONE:
            raise OrderError(f"modify failed: {getattr(res, 'retcode', None)} {getattr(res, 'comment', '')}")
        msg = f"🤖 CLAUDE MODIFY #{ticket}: SL {p.sl} -> {new_sl} | TP {p.tp} -> {new_tp}\nWhy: {reason[:300]}"
        self._audit("claude_modify", msg)
        self._notify(msg)
        return msg
