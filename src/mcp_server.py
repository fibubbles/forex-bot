"""MCP server: lets Claude Code look at MT5 and the bot's records, and (opt-in) trade.

Register once (PowerShell, from anywhere). Read-only:
  claude mcp add mt5 -- C:\\Users\\apizy\\forex-bot\\.venv\\Scripts\\python.exe C:\\Users\\apizy\\forex-bot\\src\\mcp_server.py
With order tools (user decision 2026-09-30), add:  -e MT5_MCP_TRADING=YES  before the --

Safety (see CLAUDE.md): this file itself never sends orders. Order tools exist only when
MT5_MCP_TRADING=YES, and all of them go through src/claude_trading.py (fixed lot, SL/TP
mandatory, equity floor, one position, daily limit, Claude's own magic number). It never logs
in (it attaches to the running terminal, so it never sees a password) and reads the bot
database read-only. Protocol: MCP over stdio, newline-delimited JSON-RPC 2.0, stdlib only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)                      # state/, logs/ and config files are relative to the repo
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

SERVER_NAME, SERVER_VERSION = "mt5", "1.2.0"
ORDER_RULES = ("Order tools are enabled. Before any order: look at account, positions, bars and "
               "strategy_view, then call preview_order and show the user the risk. Never place an order "
               "the user did not ask for in this conversation. Lot size is fixed by the server; SL and TP "
               "are mandatory.")


def instructions(mode: str) -> str:
    if mode == "demo":
        return "MT5 DEMO account (no real money; orders are refused on a real account). " + ORDER_RULES
    return "MT5 REAL-MONEY account. " + ORDER_RULES
DEFAULT_PROTOCOL = "2025-06-18"
CONFIG_PATH = Path(os.getenv("MT5_MCP_CONFIG", "config.live.yaml"))
DB_PATH = Path("state/bot.db")
LOG_PATH = Path("logs/bot.log")
TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")
MAX_BARS, MAX_ROWS, MAX_LOG_LINES, MAX_DAYS = 500, 50, 200, 90


class ToolError(Exception):
    """Shown to Claude as a tool error (isError=true), not a protocol error."""


def _err(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)  # stdout is reserved for the protocol


# --- configuration (plain YAML read: no env/secrets needed) --------------------------------
def load_settings(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        raise ToolError(f"{path} not found; set MT5_MCP_CONFIG to another config file")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    b, micro = raw["broker"], raw.get("micro_live") or {}
    demo = raw.get("claude_demo") or {}  # only in config.claude-demo.yaml (MCP-only file)
    return {"terminal_path": b.get("terminal_path"), "symbol": b["symbol"],
            "magic": int(b["magic_number"]), "mode": raw.get("mode", "?"),
            "account_login": micro.get("account_login"), "server": micro.get("server"),
            "fixed_lot": micro.get("fixed_lot") or demo.get("lot"),
            "equity_floor": micro.get("equity_floor", demo.get("equity_floor", 0.0)),
            "friday_hours": raw.get("risk", {}).get("friday_close_hours_before", 3),
            "max_spread_points": demo.get("max_spread_points"),
            "login_env": demo.get("login_env")}


def _source(magic: int, bot_magic: int) -> str:
    from src.claude_trading import CLAUDE_MAGIC_OFFSET
    return {bot_magic: "bot", bot_magic + CLAUDE_MAGIC_OFFSET: "claude"}.get(magic, "manual/other")


# --- MT5 (attach only) -----------------------------------------------------------------------
class MT5:
    def __init__(self, settings_loader: Callable[[], dict] = load_settings) -> None:
        self._settings_loader = settings_loader
        self._mt5 = None

    @property
    def settings(self) -> dict:
        return self._settings_loader()

    def api(self):
        if self._mt5 is None:
            import MetaTrader5 as mt5  # Windows-only package, imported lazily
            s = self.settings
            kwargs: dict[str, Any] = {"timeout": 60000}
            if s["terminal_path"]:
                kwargs["path"] = s["terminal_path"]
            if s["mode"] == "demo" and s.get("login_env"):
                # DEMO only: log the terminal into the demo account so it can never sit on a real one.
                # A real-account password is never read by this server.
                from src.config_schema import load_secrets
                sec = load_secrets(s["login_env"])
                kwargs.update(login=sec.mt5_login, password=sec.mt5_password.get_secret_value(),
                              server=sec.mt5_server)
            if not mt5.initialize(**kwargs):
                raise ToolError(f"MT5 attach failed: {mt5.last_error()}. Is the terminal open and logged in?")
            if s["mode"] == "demo":
                acc = mt5.account_info()
                if (acc is None or acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO
                        or "demo" not in (acc.server or "").lower()):
                    who = f"{acc.login}@{acc.server}" if acc else "unknown"
                    mt5.shutdown()
                    raise ToolError(f"This is the DEMO server but the terminal is on {who}, not a demo "
                                    f"account. Refusing everything; log the terminal into the demo account.")
            self._mt5 = mt5
        return self._mt5

    def call(self, fn_name: str, *args, **kwargs):
        """Call an mt5 function; on a None result re-attach once (IPC hiccups while the bot runs)."""
        for attempt in (1, 2):
            mt5 = self.api()
            result = getattr(mt5, fn_name)(*args, **kwargs)
            if result is not None:
                return result
            err = mt5.last_error()
            mt5.shutdown()  # closes THIS process's connection only
            self._mt5 = None
            if attempt == 2:
                raise ToolError(f"{fn_name} failed: {err}")


def _time(epoch: int) -> str:
    from src.timeutils import server_epoch_to_utc
    return server_epoch_to_utc(pd.Series([epoch])).iloc[0].isoformat()


# --- read-only database ------------------------------------------------------------------------
def db_query(sql: str, params: tuple = ()) -> list[dict]:
    if not DB_PATH.exists():
        raise ToolError(f"{DB_PATH} not found: the bot has not run from this folder yet")
    uri = f"file:{DB_PATH.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _clamp(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value if value is not None else default)))
    except (TypeError, ValueError):
        raise ToolError(f"expected an integer, got {value!r}")


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# --- tools --------------------------------------------------------------------------------------
class Tools:
    def __init__(self, mt5: MT5) -> None:
        self.mt5 = mt5

    def account(self, args: dict) -> dict:
        a = self.mt5.call("account_info")
        return {"login": a.login, "server": a.server, "currency": a.currency,
                "balance": a.balance, "equity": a.equity, "margin": a.margin,
                "free_margin": a.margin_free, "leverage": a.leverage,
                "account_type": {0: "demo", 1: "contest", 2: "real"}.get(a.trade_mode, a.trade_mode),
                "algo_trading_allowed": bool(a.trade_allowed),
                "terminal": getattr(self.mt5.call("terminal_info"), "path", None),
                "config": str(CONFIG_PATH)}

    def positions(self, args: dict) -> list[dict]:
        magic = self.mt5.settings["magic"]
        return [{"ticket": p.ticket, "symbol": p.symbol, "side": "buy" if p.type == 0 else "sell",
                 "lots": p.volume, "open_price": p.price_open, "sl": p.sl, "tp": p.tp,
                 "current_price": p.price_current, "profit": p.profit, "opened_utc": _time(p.time),
                 "source": _source(p.magic, magic)}
                for p in self.mt5.call("positions_get")]

    def price(self, args: dict) -> dict:
        symbol = args.get("symbol") or self.mt5.settings["symbol"]
        self.mt5.call("symbol_select", symbol, True)
        info = self.mt5.call("symbol_info", symbol)
        t = self.mt5.call("symbol_info_tick", symbol)
        return {"symbol": symbol, "bid": t.bid, "ask": t.ask,
                "spread_points": round((t.ask - t.bid) / info.point), "time_utc": _time(t.time)}

    def _bars(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        from src.data_checks import validate_bars
        from src.mt5_client import TIMEFRAMES as MT5_TF
        from src.timeutils import server_epoch_to_utc
        self.mt5.call("symbol_select", symbol, True)
        rates = self.mt5.call("copy_rates_from_pos", symbol, MT5_TF[tf], 0, count + 1)
        df = pd.DataFrame(rates)
        df["time"] = server_epoch_to_utc(df["time"])
        clean, _ = validate_bars(df, tf)  # drops the bar that is still forming
        return clean.tail(count)

    def bars(self, args: dict) -> str:
        tf = str(args.get("timeframe", "H1")).upper()
        if tf not in TIMEFRAMES:
            raise ToolError(f"timeframe must be one of {TIMEFRAMES}")
        symbol = args.get("symbol") or self.mt5.settings["symbol"]
        df = self._bars(symbol, tf, _clamp(args.get("count"), 100, 1, MAX_BARS))
        cols = ["time", "open", "high", "low", "close", "spread"]
        return f"{symbol} {tf}, closed bars only, times UTC\n" + df[cols].to_csv(index=False)

    def strategy_view(self, args: dict) -> dict:
        from src.mtf import MultiTimeframe
        symbol = self.mt5.settings["symbol"]
        view = MultiTimeframe().analyse(self._bars(symbol, "H1", 400), self._bars(symbol, "M15", 400),
                                        self._bars(symbol, "M5", 300))
        sig = view.signal
        return {"symbol": symbol, "strategy": "mtf_v1", "summary": view.summary,
                "h1": view.h1, "m15": view.m15, "m5": view.m5, "atr_m15": view.atr_m15,
                "signal": None if sig is None else {"side": sig.side, "sl_distance": sig.sl_distance,
                                                    "tp_distance": sig.tp_distance, "reason": sig.reason},
                "note": "What mtf_v1 sees on the latest closed bars. The executor decides; this tool cannot trade."}

    def deal_history(self, args: dict) -> list[dict]:
        days = _clamp(args.get("days"), 7, 1, MAX_DAYS)
        now = datetime.now(timezone.utc)
        magic = self.mt5.settings["magic"]
        deals = self.mt5.call("history_deals_get", now - timedelta(days=days), now + timedelta(days=1))
        kinds = {0: "buy", 1: "sell", 2: "balance", 3: "credit"}
        return [{"ticket": d.ticket, "position": d.position_id, "time_utc": _time(d.time),
                 "symbol": d.symbol, "type": kinds.get(d.type, d.type),
                 "entry": {0: "in", 1: "out", 2: "inout", 3: "out_by"}.get(d.entry, d.entry),
                 "lots": d.volume, "price": d.price, "profit": d.profit, "swap": d.swap,
                 "commission": d.commission, "source": _source(d.magic, magic),
                 "comment": d.comment} for d in deals]

    def bot_decisions(self, args: dict) -> list[dict]:
        limit = _clamp(args.get("limit"), 20, 1, MAX_ROWS)
        return db_query("SELECT created_utc, bar_time_utc, mode, action, strategy, side, lots, "
                        "spread_points, veto, reasons FROM decisions ORDER BY id DESC LIMIT ?", (limit,))

    def bot_trades(self, args: dict) -> dict:
        days = _clamp(args.get("days"), 30, 1, MAX_DAYS)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = db_query("SELECT * FROM trades WHERE open_utc >= ? ORDER BY open_utc DESC", (since,))
        closed = [r for r in rows if r["close_utc"]]
        rs = [r["r_multiple"] for r in closed if r["r_multiple"] is not None]
        summary = {"trades": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
                   "wins": sum(1 for r in closed if (r["profit"] or 0) > 0),
                   "net_profit": round(sum(r["profit"] or 0 for r in closed), 2),
                   "mean_r": round(sum(rs) / len(rs), 3) if rs else None}
        return {"summary": summary, "trades": rows[:MAX_ROWS]}

    def bot_status(self, args: dict) -> dict:
        s = self.mt5.settings
        equity = _read_json(Path(f"state/equity_{s['mode']}.json")) or {}
        control = _read_json(Path(f"state/control_{s['mode']}.json")) or {}
        return {"config": str(CONFIG_PATH), "mode": s["mode"], "symbol": s["symbol"],
                "heartbeat": _read_json(Path("state/heartbeat.json")),
                "paused": control.get("paused"), "kill_switch": equity.get("killed"),
                "kill_reason": equity.get("killed_reason")}

    # --- order tools (only registered when MT5_MCP_TRADING=YES) --------------------------------
    def _trader(self):
        from src.claude_trading import ClaudeTrader
        s = self.mt5.settings
        if s["mode"] == "live" and not (s["account_login"] and s["fixed_lot"]):
            raise ToolError("live order tools need a micro_live section in the config")
        if s["mode"] == "demo" and not s["fixed_lot"]:
            raise ToolError("demo order tools need a claude_demo section (use config.claude-demo.yaml)")
        if s["mode"] not in ("live", "demo"):
            raise ToolError(f"order tools are not available in mode {s['mode']!r}")

        def notify(text: str) -> None:
            try:
                from src.notifier import TelegramNotifier
                TelegramNotifier.from_env().send(text)
            except Exception as e:
                _err(f"telegram failed: {e}")

        def audit(kind: str, text: str) -> None:
            try:
                from src.db import TradeLog
                TradeLog().log_event("INFO", kind, text)
            except Exception as e:
                _err(f"audit log failed: {e}")

        return ClaudeTrader(self.mt5.api(), s, notify=notify, audit=audit)

    def _order_error(self, fn):
        from src.broker import OrderError
        try:
            return fn()
        except OrderError as e:
            raise ToolError(f"REFUSED: {e}")

    def preview_order(self, args: dict) -> str:
        t = self._trader()
        return self._order_error(lambda: t.check(args.get("side"), args.get("sl"), args.get("tp"))
                                 .text(t.symbol, t.limits.lots))

    def place_order(self, args: dict) -> str:
        t = self._trader()
        reason = str(args.get("reason") or "").strip()
        if not reason:
            raise ToolError("reason is required: explain the setup in one or two sentences")
        return self._order_error(lambda: t.place(args.get("side"), args.get("sl"), args.get("tp"), reason))

    def close_position(self, args: dict) -> str:
        t = self._trader()
        return self._order_error(lambda: t.close(args.get("ticket"), str(args.get("reason") or "")))

    def modify_position(self, args: dict) -> str:
        t = self._trader()
        return self._order_error(lambda: t.modify(args.get("ticket"), args.get("sl"), args.get("tp"),
                                                  str(args.get("reason") or "")))

    def bot_log(self, args: dict) -> str:
        n = _clamp(args.get("lines"), 50, 1, MAX_LOG_LINES)
        if not LOG_PATH.exists():
            raise ToolError(f"{LOG_PATH} not found")
        with LOG_PATH.open(encoding="utf-8", errors="replace") as f:
            return "".join(deque(f, maxlen=n))


def _schema(props: dict | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "additionalProperties": False}


_INT = lambda desc, lo, hi: {"type": "integer", "minimum": lo, "maximum": hi, "description": desc}  # noqa: E731
TOOL_SPECS: list[tuple[str, str, dict]] = [
    ("account", "Account balance, equity, margin, leverage and account type (demo/real).", _schema()),
    ("positions", "All open positions, each marked as the bot's or manual/other.", _schema()),
    ("price", "Current bid/ask/spread. Default symbol: the bot's symbol.",
     _schema({"symbol": {"type": "string"}})),
    ("bars", "Recent CLOSED OHLC bars as CSV (UTC).",
     _schema({"symbol": {"type": "string"}, "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
              "count": _INT("bars to return", 1, MAX_BARS)})),
    ("strategy_view", "What the bot's mtf_v1 strategy sees right now: H1 structure, M15 setup, "
                      "M5 breakout, and whether that is a signal.", _schema()),
    ("deal_history", "Closed deals, deposits and withdrawals from MT5 history.",
     _schema({"days": _INT("look-back in days", 1, MAX_DAYS)})),
    ("bot_decisions", "The bot's latest decisions from its own log (newest first).",
     _schema({"limit": _INT("rows", 1, MAX_ROWS)})),
    ("bot_trades", "The bot's trades with a summary (count, wins, net profit, mean R).",
     _schema({"days": _INT("look-back in days", 1, MAX_DAYS)})),
    ("bot_status", "Heartbeat, paused flag and kill switch of the running bot.", _schema()),
    ("bot_log", "Last lines of logs/bot.log.", _schema({"lines": _INT("lines", 1, MAX_LOG_LINES)})),
]
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

_PRICE = {"type": "number"}
ORDER_SPECS: list[tuple[str, str, dict]] = [
    ("preview_order", "Check a trade idea against every hard rule WITHOUT sending it. Shows risk and "
                      "reward in account currency. Always call this before place_order.",
     _schema({"side": {"type": "string", "enum": ["buy", "sell"]}, "sl": _PRICE, "tp": _PRICE})),
    ("place_order", "REAL MONEY. Market order on the bot's symbol with a fixed lot. SL and TP prices are "
                    "mandatory; the server refuses anything that breaks the hard rules.",
     {**_schema({"side": {"type": "string", "enum": ["buy", "sell"]}, "sl": _PRICE, "tp": _PRICE,
                 "reason": {"type": "string", "description": "why this trade, 1-2 sentences"}}),
      "required": ["side", "sl", "tp", "reason"]}),
    ("close_position", "REAL MONEY. Close one of Claude's own positions at market.",
     {**_schema({"ticket": {"type": "integer"}, "reason": {"type": "string"}}), "required": ["ticket", "reason"]}),
    ("modify_position", "REAL MONEY. Change SL/TP of one of Claude's positions. SL may only move to reduce risk.",
     {**_schema({"ticket": {"type": "integer"}, "sl": _PRICE, "tp": _PRICE, "reason": {"type": "string"}}),
      "required": ["ticket", "reason"]}),
]
ORDER_TOOL = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}


def trading_enabled() -> bool:
    return os.getenv("MT5_MCP_TRADING") == "YES"


def active_specs(mode: str = "live") -> list[tuple[str, str, dict, dict]]:
    specs = [(n, d, s, READ_ONLY) for n, d, s in TOOL_SPECS]
    if trading_enabled():
        label = "DEMO account (no real money)." if mode == "demo" else "REAL MONEY."
        specs += [(n, d.replace("REAL MONEY.", label), s, READ_ONLY if n == "preview_order" else ORDER_TOOL)
                  for n, d, s in ORDER_SPECS]
    return specs


# --- JSON-RPC over stdio -------------------------------------------------------------------------
class Server:
    def __init__(self, tools: Tools) -> None:
        self.tools = tools

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:          # notification (e.g. notifications/initialized): never answered
            return None
        try:
            if method == "initialize":
                result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", DEFAULT_PROTOCOL),
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                          "instructions": instructions(self._mode()) if trading_enabled() else
                                          "Read-only view of MT5 and the forex bot. It cannot place, "
                                          "modify or close orders."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": n, "description": d, "inputSchema": s, "annotations": a}
                                    for n, d, s, a in active_specs(self._mode())]}
            elif method == "tools/call":
                result = self._call(msg.get("params") or {})
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
        except Exception as e:  # never crash the server on one bad request
            _err(traceback.format_exc())
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _mode(self) -> str:
        try:
            return self.tools.mt5.settings["mode"]
        except Exception:
            return "?"

    def _call(self, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {n for n, _, _, _ in active_specs()}:
            return {"content": [{"type": "text", "text": f"Unknown tool {name}"}], "isError": True}
        try:
            out = getattr(self.tools, name)(args)
            text = out if isinstance(out, str) else json.dumps(out, default=str, indent=1)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:
            _err(traceback.format_exc())
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}

    def serve(self, stdin=sys.stdin, stdout=sys.stdout) -> None:
        for line in stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            else:
                reply = self.handle(msg)
            if reply is not None:
                stdout.write(json.dumps(reply, default=str) + "\n")
                stdout.flush()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # Windows: no \r\n, no cp1252
        sys.stdin.reconfigure(encoding="utf-8")
    _err(f"{SERVER_NAME} {SERVER_VERSION} ready ({'ORDER TOOLS ON' if trading_enabled() else 'read-only'}), "
         f"config={CONFIG_PATH}")
    Server(Tools(MT5())).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
