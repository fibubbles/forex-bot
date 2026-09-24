"""SQLite log of every decision, trade and event. Append-only by design.

Nothing is ever deleted: the log is the evidence for reviewing the bot later.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path("state/bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc TEXT NOT NULL,
    bar_time_utc TEXT NOT NULL,
    mode TEXT NOT NULL,
    action TEXT NOT NULL,
    strategy TEXT,
    side TEXT,
    lots REAL,
    sl_distance REAL,
    tp_distance REAL,
    spread_points REAL,
    veto TEXT,
    reasons TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    ticket INTEGER PRIMARY KEY,
    mode TEXT NOT NULL,
    strategy TEXT,
    side TEXT NOT NULL,
    lots REAL NOT NULL,
    open_utc TEXT NOT NULL,
    requested_price REAL,
    entry_price REAL NOT NULL,
    sl REAL NOT NULL,
    tp REAL,
    spread_points REAL,
    slippage_points REAL,
    close_utc TEXT,
    exit_price REAL,
    profit REAL,
    r_multiple REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc TEXT NOT NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TradeLog:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH, point: float = 0.00001) -> None:
        self.path = Path(path)
        self.point = point
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript(SCHEMA)

    # --- low-level helpers ---------------------------------------------
    def _write(self, sql: str, params: tuple = ()) -> int:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            return conn.execute(sql, params).lastrowid

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # --- decisions & events --------------------------------------------
    def log_decision(
        self,
        bar_time_utc: str,
        mode: str,
        action: str,
        strategy: str | None = None,
        side: str | None = None,
        lots: float | None = None,
        sl_distance: float | None = None,
        tp_distance: float | None = None,
        spread_points: float | None = None,
        veto: str | None = None,
        reasons: list[str] | tuple = (),
    ) -> int:
        return self._write(
            "INSERT INTO decisions (created_utc, bar_time_utc, mode, action, strategy, side, lots, "
            "sl_distance, tp_distance, spread_points, veto, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), bar_time_utc, mode, action, strategy, side, lots,
             sl_distance, tp_distance, spread_points, veto, json.dumps(list(reasons))),
        )

    def log_event(self, level: str, kind: str, message: str) -> int:
        return self._write(
            "INSERT INTO events (created_utc, level, kind, message) VALUES (?,?,?,?)",
            (_now(), level, kind, message),
        )

    def recent_decisions(self, limit: int = 5) -> list[dict]:
        return self._query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))

    # --- trades ----------------------------------------------------------
    def record_open(
        self,
        ticket: int,
        mode: str,
        strategy: str,
        side: str,
        lots: float,
        requested_price: float,
        entry_price: float,
        sl: float,
        tp: float | None,
        spread_points: float,
    ) -> None:
        # Positive slippage = filled at a worse price than requested.
        diff = entry_price - requested_price if side == "long" else requested_price - entry_price
        self._write(
            "INSERT INTO trades (ticket, mode, strategy, side, lots, open_utc, requested_price, "
            "entry_price, sl, tp, spread_points, slippage_points) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticket, mode, strategy, side, lots, _now(), requested_price,
             entry_price, sl, tp, spread_points, round(diff / self.point, 1)),
        )

    def record_close(self, ticket: int, exit_price: float, profit: float) -> None:
        t = self.trade(ticket)
        if t is None:
            raise KeyError(f"Unknown ticket {ticket}")
        risk = abs(t["entry_price"] - t["sl"])
        move = exit_price - t["entry_price"] if t["side"] == "long" else t["entry_price"] - exit_price
        r = move / risk if risk > 0 else None
        self._write(
            "UPDATE trades SET close_utc=?, exit_price=?, profit=?, r_multiple=? WHERE ticket=?",
            (_now(), exit_price, profit, r, ticket),
        )

    def trade(self, ticket: int) -> dict | None:
        rows = self._query("SELECT * FROM trades WHERE ticket=?", (ticket,))
        return rows[0] if rows else None

    def open_trades(self) -> list[dict]:
        return self._query("SELECT * FROM trades WHERE close_utc IS NULL ORDER BY open_utc")

    def next_paper_ticket(self) -> int:
        """Paper tickets are negative so they can never collide with real MT5 tickets."""
        row = self._query("SELECT MIN(ticket) AS t FROM trades")[0]
        return min(row["t"] or 0, 0) - 1

    def realized_profit(self, mode: str) -> float:
        row = self._query(
            "SELECT COALESCE(SUM(profit), 0) AS p FROM trades WHERE mode=? AND close_utc IS NOT NULL",
            (mode,),
        )[0]
        return float(row["p"])