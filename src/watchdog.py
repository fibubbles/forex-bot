"""Separate-process watchdog: Telegram alert if the executor stops or stalls.

Runs every 10 minutes from Windows Task Scheduler (scripts/watchdog_task.py).
It must be a separate process: a crashed bot cannot report its own crash.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.notifier import TelegramNotifier
from src.state import read_heartbeat
from src.timeutils import hours_since_week_open, is_market_open

log = logging.getLogger("watchdog")

HEARTBEAT_STALE = timedelta(minutes=5)
BAR_STALE = timedelta(hours=9)       # latest closed H4 bar opened at most ~8h ago
REOPEN_GRACE_HOURS = 9               # after Sunday open, Friday's bar is still the latest
REALERT_EVERY = timedelta(minutes=60)
WATCHDOG_STATE = Path("state/watchdog.json")


def evaluate(heartbeat: dict | None, now_utc: datetime) -> str | None:
    """Return a problem description, or None if healthy (or market closed)."""
    if not is_market_open(now_utc):
        return None
    if heartbeat is None:
        return "No heartbeat: executor has never run, or state was deleted"

    age = now_utc - datetime.fromisoformat(heartbeat["ts"])
    if age > HEARTBEAT_STALE:
        return f"Executor NOT running: last heartbeat {int(age.total_seconds() // 60)} min ago"

    last_bar = heartbeat.get("last_bar")
    if last_bar and hours_since_week_open(now_utc) > REOPEN_GRACE_HOURS:
        bar_age = now_utc - datetime.fromisoformat(last_bar)
        if bar_age > BAR_STALE:
            return f"Executor alive but STUCK: last processed bar {bar_age} ago"
    return None


def main(now_utc: datetime | None = None) -> int:
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(filename="logs/watchdog.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    now_utc = now_utc or datetime.now(timezone.utc)
    problem = evaluate(read_heartbeat(), now_utc)
    state = json.loads(WATCHDOG_STATE.read_text(encoding="utf-8")) if WATCHDOG_STATE.exists() else {}
    notifier = TelegramNotifier.from_env()

    if problem:
        log.warning(problem)
        last = state.get("last_alert")
        if not last or now_utc - datetime.fromisoformat(last) >= REALERT_EVERY:
            notifier.send(f"🐕 WATCHDOG: {problem}")
            state["last_alert"] = now_utc.isoformat()
        state["alerting"] = True
    else:
        log.info("healthy")
        if state.get("alerting"):
            notifier.send("🐕 WATCHDOG: executor healthy again ✅")
        state = {}

    WATCHDOG_STATE.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_STATE.write_text(json.dumps(state), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())