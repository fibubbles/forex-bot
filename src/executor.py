"""Main loop: mode dry_run (paper), demo (real orders on a DEMO account) or live (micro experiment).

Run:
  python -m src.executor --start-equity 1100                                              # dry_run
  python -m src.executor --config config.demo.yaml --env .env.demo --start-equity 1100    # demo
  python -m src.executor --config config.live.yaml --env .env.valetax                     # live (micro)
  add --once to process the latest closed bar and exit
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from src.broker import LiveBroker, OrderError, assert_demo_account, assert_live_account_allowed
from src.config_schema import AppConfig, load_config, load_secrets
from src.data_checks import validate_bars
from src.db import TradeLog
from src.features import build_features
from src.mt5_client import MT5Client, MT5Error
from src.notifier import HELP_TEXT, TelegramNotifier
from src.paper import PaperBroker
from src.risk import SymbolSpec, check_entry, check_micro_entry
from src.state import ControlFlags, EquityTracker, foreign_positions, write_heartbeat
from src.strategy import Signal, TrendPullback
from src.timeutils import is_past_friday_cutoff
from src.trading import LiveTrading, PaperTrading
from src.veto import NewsVeto

log = logging.getLogger("executor")

BARS_TO_LOAD = 2500
POLL_SECONDS = 10
MIN_TYPICAL_SPREAD_POINTS = 10.0  # some demo servers report 0 spread; keep the spread filter active
LABELS = {"dry_run": "PAPER", "demo": "DEMO", "live": "LIVE"}
NO_DRAWDOWN_LATCH = 100.0  # live micro uses the equity floor instead of % drawdown


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
    def __init__(self, cfg: AppConfig, start_equity: float | None, env_file: str | None = None) -> None:
        if cfg.mode not in LABELS:
            raise RuntimeError(f"Unsupported mode: {cfg.mode}")
        if cfg.mode != "live" and start_equity is None:
            raise RuntimeError("--start-equity is required for dry_run and demo")
        self.cfg = cfg
        self.mode = cfg.mode
        self.label = LABELS[cfg.mode]
        self.micro = cfg.micro_live
        self.symbol = cfg.broker.symbol
        self.tf = cfg.broker.timeframe
        self.start_equity = start_equity

        self.client = MT5Client(load_secrets(env_file), cfg.broker.terminal_path)
        self.trade_log = TradeLog()
        self.tracker = EquityTracker(Path(f"state/equity_{cfg.mode}.json"))
        self.control = ControlFlags(Path(f"state/control_{cfg.mode}.json"))
        self.notifier = TelegramNotifier.from_env()
        self.veto = NewsVeto.from_env(cfg.broker.symbol)
        self.strategy = TrendPullback()

        self.spec: SymbolSpec | None = None
        self.trading: PaperTrading | LiveTrading | None = None
        self.digits = 5
        self.typical_spread = 0.0

        last = self.trade_log.last_bar_time(cfg.mode)
        self.last_bar = pd.Timestamp(last) if last else None

    # --- lifecycle ---------------------------------------------------------
    def startup(self) -> None:
        self.client.connect()
        acc = self.client.account()
        info = self.client.symbol(self.symbol)
        for _ in range(20):  # symbol data can be incomplete right after a (re)login
            self.spec = SymbolSpec.from_mt5(info)
            if self.spec.valid:
                break
            time.sleep(0.5)
            info = self.client.symbol(self.symbol)
        else:
            raise RuntimeError(f"Invalid symbol spec for {self.symbol} (broker data not loaded): {self.spec}")
        self.digits = info.digits

        clean = self._load_bars()
        self.typical_spread = max(float(clean["spread"].tail(500).median()), MIN_TYPICAL_SPREAD_POINTS)

        if self.mode == "dry_run":
            self.trading = PaperTrading(PaperBroker(self.trade_log, self.spec, self.typical_spread))
        else:
            max_lot = None
            if self.mode == "demo":
                assert_demo_account(acc, mt5.ACCOUNT_TRADE_MODE_DEMO)
            else:  # live micro experiment
                assert_live_account_allowed(acc, self.micro.account_login, self.micro.server)
                term = mt5.terminal_info()
                if not acc.trade_allowed or term is None or not term.trade_allowed:
                    raise RuntimeError("Trading not allowed: log in with the MASTER password "
                                       "and turn ON Algo Trading in this terminal")
                max_lot = self.micro.fixed_lot
            broker = LiveBroker(mt5, self.symbol, self.cfg.broker.magic_number, self.digits, max_lot=max_lot)
            self.trading = LiveTrading(broker, self.trade_log, mode=self.mode)
            for ticket in self.trading.adopt_orphans():
                self.notifier.send(f"⚠️ Adopted untracked bot position #{ticket}")
            self.trading.check_exits()  # record anything closed while the bot was off

        foreign = foreign_positions(self.client.positions(self.symbol), self.symbol, self.cfg.broker.magic_number)
        if foreign:
            log.warning("%d manual position(s) on %s will be ignored", len(foreign), self.symbol)

        msg = (f"mode={self.mode} account={acc.login}@{acc.server} strategy={self.strategy.name} "
               f"start_equity={self.start_equity} typical_spread={self.typical_spread:.0f} "
               f"last_bar={self.last_bar}")
        self.trade_log.log_event("INFO", "startup", msg)
        log.info("Startup: %s", msg)

        extra = (f"\n⚠️ REAL MONEY: fixed lot {self.micro.fixed_lot}, "
                 f"equity floor {self.micro.equity_floor}" if self.mode == "live" else "")
        self.notifier.skip_pending()
        self.notifier.send(f"🟢 Bot started ({self.mode})\nAccount: {acc.server}\n"
                           f"Strategy: {self.strategy.name}\n"
                           f"News veto: {'ON' if self.veto else 'OFF'}\n"
                           f"Paused: {self.control.paused}{extra}\nSend /help for commands")

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
        if self.mode == "live":
            return float(self.client.account().equity)  # the real account balance
        return (self.start_equity + self.trade_log.realized_profit(self.trading.mode)
                + self.trading.floating_profit(bid, ask))

    # --- telegram ------------------------------------------------------------
    def _status_text(self, bid: float, ask: float) -> str:
        last = self.trade_log.recent_decisions(1)
        last_txt = f"{last[0]['bar_time_utc']} -> {last[0]['action']}" if last else "none"
        pos = "\n".join(f"  #{t['ticket']} {t['side']} {t['lots']} @ {t['entry_price']} "
                        f"SL {t['sl']} TP {t['tp']}" for t in self.trading.open_trades()) or "  none"
        floor = f" (floor {self.micro.equity_floor})" if self.mode == "live" else ""
        return (f"📊 Status ({self.mode})\nEquity: {self._equity(bid, ask):.2f}{floor}\n"
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
                self.control.set_paused(True)
                try:
                    closed = self.trading.close_all(bid, ask, "telegram")
                    self.notifier.send(f"🛑 Closed {len(closed)} position(s) and paused. /resume to continue.")
                except OrderError as e:
                    self.notifier.send(f"⚠️ /closeall FAILED: {e}\nCheck MT5 manually!")
            else:
                self.notifier.send(HELP_TEXT)

    def _notify_close(self, ticket: int) -> None:
        t = self.trade_log.trade(ticket)
        r = f"{t['r_multiple']:+.2f}R" if t["r_multiple"] is not None else "n/a"
        emoji = "✅" if (t["profit"] or 0) > 0 else "❌"
        self.notifier.send(f"{emoji} {self.label} CLOSE #{ticket} {t['side']}\n"
                           f"Exit {t['exit_price']} | P/L {t['profit']:.2f} ({r})")

    # --- one cycle -----------------------------------------------------------
    def run_once(self) -> bool:
        """Returns True if a new closed bar was processed."""
        clean = self._load_bars()
        tick = self.client.tick(self.symbol)
        bid, ask = tick.bid, tick.ask
        now = datetime.now(timezone.utc)

        self.handle_commands(bid, ask)

        # Demo/live: the broker's server closes positions at SL/TP at any time, so check every poll.
        if self.mode != "dry_run":
            for ticket in self.trading.check_exits():
                self._notify_close(ticket)

        # Friday: flatten before the weekend, checked every poll (not only at bar close).
        if is_past_friday_cutoff(self.cfg.risk.friday_close_hours_before, now) and self.trading.open_count():
            try:
                closed = self.trading.close_all(bid, ask, "friday")
                self.trade_log.log_event("INFO", "friday_close", f"closed {len(closed)} position(s)")
                self.notifier.send(f"🗓️ Friday cutoff: closed {len(closed)} position(s) before the weekend")
            except OrderError as e:
                self.notifier.send_throttled("friday", f"⚠️ Friday close FAILED: {e}\nCheck MT5 manually!")

        bar_time = clean["time"].iloc[-1]
        if self.last_bar is not None and bar_time <= self.last_bar:
            return False

        # Dry run: simulate SL/TP on every bar closed since the last cycle.
        if self.mode == "dry_run" and self.last_bar is not None:
            for ticket in self.trading.check_exits(clean[clean["time"] > self.last_bar]):
                self._notify_close(ticket)

        spread_pts = (ask - bid) / self.spec.point
        equity = self._equity(bid, ask)
        open_count = self.trading.open_count()
        was_killed = self.tracker.killed
        max_dd = NO_DRAWDOWN_LATCH if self.mode == "live" else self.cfg.risk.max_drawdown_pct
        acct = self.tracker.update(equity, open_count, max_dd, now)
        if self.mode == "live" and equity < self.micro.equity_floor:
            self.tracker.latch(f"equity floor: {equity:.2f} < {self.micro.equity_floor:.2f}")
        if self.tracker.killed and not was_killed:
            self.notifier.send(f"🚨 KILL SWITCH: {self.tracker.state.killed_reason}\n"
                               "Bot stopped opening trades. Manual review required.")

        base = dict(bar_time_utc=bar_time.isoformat(), mode=self.mode,
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
        if self.mode == "live":
            decision = check_micro_entry(self.micro, self.cfg.risk, equity, open_count, self.spec,
                                         signal.sl_distance, spread_pts, self.typical_spread, now)
        else:
            decision = check_entry(self.cfg.risk, acct, self.spec, signal.sl_distance,
                                   spread_pts, self.typical_spread, now)
        if not decision.allowed:
            return self._decide({**base, **sig}, "blocked_risk", decision.reasons, bar_time)

        entry, sl, tp = order_prices(signal, bid, ask, self.digits)

        veto_txt = "disabled"
        if self.veto is not None:
            v = self.veto.check(signal.side, entry, sl, tp, now)
            veto_txt = f"{v.action}: {v.reason}"
            if not v.allowed:
                self.notifier.send(f"🛡️ VETO blocked {signal.side.upper()} {self.symbol}\n{v.reason}"
                                   + (f"\nEvents: {', '.join(v.events)}" if v.events else ""))
                return self._decide({**base, **sig, "veto": veto_txt}, "blocked_veto",
                                    [v.reason, *v.events], bar_time)

        try:
            opened = self.trading.open(signal, decision.lots, entry, sl, tp, bid, ask, spread_pts)
        except OrderError as e:
            self.notifier.send(f"⚠️ {self.label} order FAILED: {e}")
            return self._decide({**base, **sig, "veto": veto_txt}, "order_failed", [str(e)], bar_time)

        arrow = "📈" if signal.side == "long" else "📉"
        self.notifier.send(f"{arrow} {self.label} {signal.side.upper()} {decision.lots} {self.symbol}\n"
                           f"Entry {opened.entry}\nSL {opened.sl} | TP {opened.tp}\n"
                           f"Risk {decision.risk_amount:.2f}\n{signal.reason}\nVeto: {veto_txt}")
        action = {"dry_run": "paper_open", "demo": "order_open", "live": "live_open"}[self.mode]
        return self._decide({**base, **sig, "lots": decision.lots, "veto": veto_txt}, action,
                            [signal.reason, f"#{opened.ticket} entry {opened.entry} sl {opened.sl} tp {opened.tp}",
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
    parser = argparse.ArgumentParser(description="Forex bot executor (dry_run, demo or live micro)")
    parser.add_argument("--config", default="config.yaml", help="config file")
    parser.add_argument("--env", default=None, help="extra env file with MT5 credentials, e.g. .env.demo")
    parser.add_argument("--start-equity", "--paper-equity", dest="start_equity", type=float, default=None,
                        help="starting equity for sizing (dry_run and demo only)")
    parser.add_argument("--once", action="store_true", help="process the latest closed bar and exit")
    args = parser.parse_args()

    _setup_logging()
    ex = Executor(load_config(args.config, args.env), args.start_equity, args.env)
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