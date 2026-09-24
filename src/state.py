"""Bot state that must survive restarts.

- Our positions are identified by magic number, never by guessing.
- Peak / day-start / week-start equity persist in a JSON file.
- The kill switch LATCHES: once triggered it stays on until manually reset,
  even if equity later recovers or the bot restarts.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.risk import AccountState
from src.timeutils import utc_to_server_wall

log = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("state/equity_state.json")


def bot_positions(positions, symbol: str, magic: int) -> list:
    return [p for p in (positions or ()) if p.symbol == symbol and p.magic == magic]


def foreign_positions(positions, symbol: str, magic: int) -> list:
    """Positions on our symbol that the bot did not open (e.g. manual trades)."""
    return [p for p in (positions or ()) if p.symbol == symbol and p.magic != magic]


def _server_day_and_week(now_utc: datetime) -> tuple[str, str]:
    wall = utc_to_server_wall(pd.Series([pd.Timestamp(now_utc)])).iloc[0]
    iso = wall.isocalendar()
    return wall.strftime("%Y-%m-%d"), f"{iso[0]}-W{iso[1]:02d}"


@dataclass
class EquityState:
    peak_equity: float
    day_start_equity: float
    week_start_equity: float
    server_day: str
    server_week: str
    killed: bool = False
    killed_reason: str = ""


class EquityTracker:
    def __init__(self, path: str | Path = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)
        self.state: EquityState | None = self._load()

    def _load(self) -> EquityState | None:
        if not self.path.exists():
            return None
        return EquityState(**json.loads(self.path.read_text(encoding="utf-8")))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.state), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: never leaves a half-written file

    @property
    def killed(self) -> bool:
        return bool(self.state and self.state.killed)

    def update(
        self,
        equity: float,
        open_positions: int,
        max_drawdown_pct: float,
        now_utc: datetime | None = None,
    ) -> AccountState:
        """Roll day/week references, track peak, latch kill switch, persist, return snapshot."""
        now_utc = now_utc or datetime.now(timezone.utc)
        day, week = _server_day_and_week(now_utc)

        s = self.state
        if s is None:
            s = EquityState(equity, equity, equity, day, week)
            log.info("New equity state initialised at %.2f", equity)
        if day != s.server_day:
            s.day_start_equity, s.server_day = equity, day
        if week != s.server_week:
            s.week_start_equity, s.server_week = equity, week
        s.peak_equity = max(s.peak_equity, equity)

        dd = (s.peak_equity - equity) / s.peak_equity * 100 if s.peak_equity > 0 else 0.0
        if not s.killed and dd >= max_drawdown_pct:
            s.killed = True
            s.killed_reason = f"drawdown {dd:.1f}% at {now_utc.isoformat()}"
            log.critical("KILL SWITCH LATCHED: %s", s.killed_reason)

        self.state = s
        self._save()
        return AccountState(
            equity=equity,
            peak_equity=s.peak_equity,
            day_start_equity=s.day_start_equity,
            week_start_equity=s.week_start_equity,
            open_positions=open_positions,
        )

    def reset_kill_switch(self, new_peak: float | None = None) -> None:
        """Manual action only, after reviewing what went wrong."""
        if self.state is None:
            return
        self.state.killed = False
        self.state.killed_reason = ""
        if new_peak is not None:
            self.state.peak_equity = new_peak
        self._save()
        log.warning("Kill switch manually reset (peak=%.2f)", self.state.peak_equity)

    def latch(self, reason: str) -> None:
        """Latch the kill switch for a reason other than % drawdown (e.g. the micro equity floor)."""
        if self.state is None or self.state.killed:
            return
        self.state.killed = True
        self.state.killed_reason = reason
        self._save()
        log.critical("KILL SWITCH LATCHED: %s", reason)


DEFAULT_CONTROL_PATH = Path("state/control.json")


class ControlFlags:
    """Operator switches that must survive restarts (e.g. /pause from Telegram)."""

    def __init__(self, path: str | Path = DEFAULT_CONTROL_PATH) -> None:
        self.path = Path(path)
        self._data = (json.loads(self.path.read_text(encoding="utf-8"))
                      if self.path.exists() else {"paused": False})

    @property
    def paused(self) -> bool:
        return bool(self._data.get("paused", False))

    def set_paused(self, value: bool) -> None:
        self._data["paused"] = bool(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data), encoding="utf-8")
        os.replace(tmp, self.path)


DEFAULT_HEARTBEAT_PATH = Path("state/heartbeat.json")


def write_heartbeat(last_bar, mode: str, path: str | Path = DEFAULT_HEARTBEAT_PATH,
                    now_utc: datetime | None = None) -> None:
    now_utc = now_utc or datetime.now(timezone.utc)
    data = {
        "ts": now_utc.isoformat(),
        "last_bar": last_bar.isoformat() if last_bar is not None else None,
        "mode": mode,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def read_heartbeat(path: str | Path = DEFAULT_HEARTBEAT_PATH) -> dict | None:
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None