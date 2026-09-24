"""Main loop. Currently supports mode=dry_run ONLY (paper trading, no orders sent).

Run:
  python -m src.executor --paper-equity 1100          # loop forever
  python -m src.executor --paper-equity 1100 --once   # process latest closed bar, then exit
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pandas as pd

from src.config_schema import AppConfig, load_config, load_secrets
from src.data_checks import validate_bars
from src.db import TradeLog
from src.features import build_features
from src.mt5_client import MT5Client, MT5Error
from src.notifier import HELP_TEXT, TelegramNotifier
from src.paper import MODE as PAPER_MODE
from src.paper import PaperBroker
from src.risk import SymbolSpec, check_entry
from src.state import ControlFlags, EquityTracker, foreign_positions, write_heartbeat
from src.strategy import Signal, TrendPullback
from src.timeutils import is_past_friday_cutoff

log = logging.getLogger("executor")

BARS_TO_LOAD = 1500
POLL_SECONDS = 10
DRY_RUN_STATE = Path("state/equity_dry_run.json")


def order_prices(signal: Signal, bid: float, ask: float, digits: int) -> tuple[float, float, float]:
    """(entry, sl, tp) for a market order. Longs fill at ask, shorts at bid."""
    if signal.side == "long":
        entry = ask
        sl, tp = entry - signal.sl_distance, entry + signal.tp_distance
    else:
        entry = bid
        sl, tp = entry + signal.sl_distance, entry - signal.tp_distance
    return round(entry, digits), round(sl, digits), round(tp, digits)


class Executor:
    def __init__(self, cfg: AppConfig, paper_equity: float) -> None:
        if cfg.mode != "dry_run":
            raise RuntimeError(f"mode={cfg.mode} is not supported yet: only dry_run is implemented")
        self.cfg = cfg
        self.symbol = cfg.broker.symbol
        self.tf = cfg.broker.timeframe
        self.paper_equity = paper_equity

        self.client = MT5Client(load_secrets(), cfg.broker.terminal_path)
        self.trade_log = TradeLog()
        self.tracker = EquityTracker(DRY_RUN_STATE)
        self.control = ControlFlags()
        self.notifier = TelegramNotifier.from_env()
        self.strategy = TrendPullback()

        self.spec: SymbolSpec | None = None
        self.paper: PaperBroker | None = None
        self.digits = 5
        self.typical_spread = 0.0

        last = self.trade_log.recent_decisions(1)
        self.last_bar = pd.Timestamp(last[0]["bar_time_utc"]) if last else None

    # --- lifecycle ---------------------------------------------------------
    def startup(self) -> None:
        self.client.connect()
        self.spec = SymbolSpec.from_mt5(self.client.symbol(self.symbol))
        self.digits = round(-math.log10(self.spec.point))

        clean = self._load_bars()
        self.typical_spread = float(clean["spread"].tail(500).median())
        self.paper = PaperBroker(self.trade_log, self.spec, self.typical_spread)

        foreign = foreign_positions(self.client.positions(self.symbol), self.symbol, self.cfg.broker.magic_number)
        if foreign:
            log.warning("%d manual position(s) on %s will be ignored", len(foreign), self.symbol)

        msg = (f"mode={self.cfg.mode} strategy={self.strategy.name} paper_equity={self.paper_equity} "
               f"typical_spread={self.typical_spread:.0f} last_bar={self.last_bar}")
        self.trade_log.log_event("INFO", "startup", msg)
        log.info("Startup: %s", msg)

        self.notifier.skip_pending()
        self.notifier.send(f"🟢 Bot started ({self.cfg.mode})\nStrategy: {self.strategy.name}\n"
                           f"Paused: {self.control.paused}\nSend /help for commands")

    def reconnect(self) -> None:
        self.client.shutdown()
        time.sleep(5)
        self.client.connect()

    def _load_bars(self) -> pd.DataFrame:
        raw = self.client.get_rates(self.symbol, self.tf, BARS_TO_LOAD)
        clean, report = validate_bars(raw, self.tf)
        if not report.ok:
            raise RuntimeError(f"Data check failed: {report.summary()}")
        return clean

    def _equity(self, bid: float, ask: float) -> float:
        return (self.paper_equity + self.trade_log.realized_profit(PAPER_MODE)
                + self.paper.floating_profit(bid, ask))

    # --- telegram ------------------------------------------------------------
    def _status_text(self, bid: float, ask: float) -> str:
        last = self.trade_log.recent_decisions(1)
        last_txt = f"{last[0]['bar_time_utc']} -> {last[0]['action']}" if last else "none"
        trades = self.paper.open_trades()
        pos = "\n".join(f"  #{t['ticket']} {t['side']} {t['lots']} @ {t['entry_price']} "
                        f"SL {t['sl']} TP {t['tp']}" for t in trades) or "  none"
        return (f"📊 Status ({self.cfg.mode})\nEquity: {self._equity(bid, ask):.2f}\n"
                f"Paused: {self.control.paused} | Kill switch: {self.tracker.killed}\n"
                f"Open positions:\n{pos}\nLast decision: {last_txt}")

    def handle_commands(self, bid: float, ask: float) -> None:
        for cmd in self.notifier.poll_commands():
            log.info("Telegram command: %s", cmd)
            self.trade_log.log_event("INFO", "command", cmd)
            if cmd == "/status":
                self.notifier.send(self._status_text(bid, ask))
            elif cmd == "/pause":
                self.control.set_paused(True)
                self.notifier.send("⏸️ Paused: no new entries. Open positions keep their SL/TP. /resume to continue.")
            elif cmd == "/resume":
                if self.tracker.killed:
                    self.notifier.send("⛔ Kill switch is latched. Review the account and reset it manually; "
                                       "/resume cannot override it.")
                else:
                    self.control.set_paused(False)
                    self.notifier.send("▶️ Resumed: new entries allowed.")
            elif cmd == "/closeall":
                closed = self.paper.close_all(bid, ask, "telegram")
                self.control.set_paused(True)
                self.notifier.send(f"🛑 Closed {len(closed)} position(s) and paused. /resume to continue.")
            else:
                self.notifier.send(HELP_TEXT)

    def _notify_close(self, ticket: int) -> None:
        t = self.trade_log.trade(ticket)
        r = f"{t['r_multiple']:+.2f}R" if t["r_multiple"] is not None else "n/a"
        emoji = "✅" if t["profit"] > 0 else "❌"
        self.notifier.send(f"{emoji} PAPER CLOSE #{ticket} {t['side']}\n"
                           f"Exit {t['exit_price']} | P/L {t['profit']:.2f} ({r})")

    # --- one cycle -----------------------------------------------------------
    def run_once(self) -> bool:
        """Returns True if a new closed bar was processed."""
        clean = self._load_bars()
        tick = self.client.tick(self.symbol)
        bid, ask = tick.bid, tick.ask
        now = datetime.now(timezone.utc)

        self.handle_commands(bid, ask)

        # Friday: flatten before the weekend, checked every poll (not only at bar close).
        if is_past_friday_cutoff(self.cfg.risk.friday_close_hours_before, now) and self.paper.open_trades():
            closed = self.paper.close_all(bid, ask, "friday")
            self.trade_log.log_event("INFO", "friday_close", f"closed {len(closed)} paper trade(s)")
            self.notifier.send(f"🗓️ Friday cutoff: closed {len(closed)} position(s) before the weekend")

        bar_time = clean["time"].iloc[-1]
        if self.last_bar is not None and bar_time <= self.last_bar:
            return False

        # Check paper SL/TP on every bar closed since the last cycle (catches up after downtime).
        if self.last_bar is not None:
            for b in clean[clean["time"] > self.last_bar].itertuples():
                for ticket in self.paper.on_bar(b.open, b.high, b.low, b.close):
                    self._notify_close(ticket)

        spread_pts = (ask - bid) / self.spec.point
        equity = self._equity(bid, ask)
        was_killed = self.tracker.killed
        acct = self.tracker.update(equity, len(self.paper.open_trades()),
                                   self.cfg.risk.max_drawdown_pct, now)
        if self.tracker.killed and not was_killed:
            self.notifier.send(f"🚨 KILL SWITCH: {self.tracker.state.killed_reason}\n"
                               "Bot stopped opening trades. Manual review required.")

        base = dict(bar_time_utc=bar_time.isoformat(), mode=self.cfg.mode,
                    strategy=self.strategy.name, spread_points=round(spread_pts, 1))

        if self.tracker.killed:
            return self._decide(base, "blocked_kill_switch", [self.tracker.state.killed_reason], bar_time)
        if self.control.paused:
            return self._decide(base, "paused", ["paused via Telegram"], bar_time)

        feats = build_features(clean)
        if feats.empty or feats["time"].iloc[-1] != bar_time:
            return self._decide(base, "skip", ["features unavailable for latest bar"], bar_time)

        signal = self.strategy.evaluate(feats)
        if signal is None:
            return self._decide(base, "no_signal", [f"equity {equity:.2f}"], bar_time)

        sig = dict(side=signal.side, sl_distance=signal.sl_distance, tp_distance=signal.tp_distance)
        decision = check_entry(self.cfg.risk, acct, self.spec, signal.sl_distance,
                               spread_pts, self.typical_spread, now)
        if not decision.allowed:
            return self._decide({**base, **sig}, "blocked_risk", decision.reasons, bar_time)

        entry, sl, tp = order_prices(signal, bid, ask, self.digits)
        self.paper.open(signal.side, decision.lots, bid, ask, sl, tp, signal.strategy, spread_pts)
        self.notifier.send(f"📈 PAPER {signal.side.upper()} {decision.lots} {self.symbol}\n"
                           f"Entry {entry}\nSL {sl} | TP {tp}\nRisk {decision.risk_amount:.2f}\n"
                           f"{signal.reason}")
        return self._decide({**base, **sig, "lots": decision.lots}, "paper_open",
                            [signal.reason, f"entry {entry} sl {sl} tp {tp}",
                             f"risk {decision.risk_amount:.2f}"], bar_time)

    def _decide(self, fields: dict, action: str, reasons: list[str], bar_time: pd.Timestamp) -> bool:
        self.trade_log.log_decision(action=action, reasons=reasons, **fields)
        self.last_bar = bar_time
        log.info("Bar %s -> %s | %s", fields["bar_time_utc"], action, "; ".join(reasons))
        return True


def _setup_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(),
                    RotatingFileHandler("logs/bot.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    # These log on every poll; keep them quiet unless something is wrong.
    logging.getLogger("src.mt5_client").setLevel(logging.WARNING)
    logging.getLogger("src.data_checks").setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(description="Forex bot executor (dry_run only)")
    parser.add_argument("--paper-equity", type=float, required=True, help="simulated starting equity")
    parser.add_argument("--once", action="store_true", help="process the latest closed bar and exit")
    args = parser.parse_args()

    _setup_logging()
    ex = Executor(load_config(), args.paper_equity)
    ex.startup()
    try:
        if args.once:
            log.info("once: new bar processed = %s", ex.run_once())
            return 0
        while True:
            try:
                ex.run_once()
            except MT5Error as e:
                log.error("MT5 error: %s (reconnecting)", e)
                ex.trade_log.log_event("ERROR", "mt5", str(e))
                ex.notifier.send_throttled("mt5", f"⚠️ MT5 error: {e}\nReconnecting...")
                try:
                    ex.reconnect()
                except MT5Error as e2:
                    log.error("Reconnect failed: %s", e2)
            except Exception as e:  # never let one bad cycle kill the bot
                log.exception("Unexpected error")
                ex.trade_log.log_event("ERROR", "exception", repr(e))
                ex.notifier.send_throttled("exception", f"⚠️ Unexpected error: {e!r}")
            write_heartbeat(ex.last_bar, ex.cfg.mode)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        ex.client.shutdown()
        ex.trade_log.log_event("INFO", "shutdown", "executor stopped")
        ex.notifier.send("🔴 Bot stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())