"""Weekly review of the bot's log: decisions, veto behaviour, paper trades, errors."""
import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.notifier import TelegramNotifier

DB = Path("state/bot.db")


def build_report(days: int) -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True)) as conn:
        actions = conn.execute(
            "SELECT action, COUNT(*) FROM decisions WHERE created_utc >= ? GROUP BY action ORDER BY 2 DESC",
            (since,)).fetchall()
        vetoes = [r[0] for r in conn.execute(
            "SELECT veto FROM decisions WHERE created_utc >= ? AND veto IS NOT NULL AND veto != 'disabled'",
            (since,)).fetchall()]
        closed = conn.execute(
            "SELECT profit, r_multiple FROM trades WHERE close_utc >= ?", (since,)).fetchall()
        open_count = conn.execute("SELECT COUNT(*) FROM trades WHERE close_utc IS NULL").fetchone()[0]
        errors = conn.execute(
            "SELECT kind, COUNT(*) FROM events WHERE created_utc >= ? AND level = 'ERROR' GROUP BY kind",
            (since,)).fetchall()

    lines = [f"📋 Report: last {days} days", "", "Decisions:"]
    lines += [f"  {action}: {n}" for action, n in actions] or ["  none"]

    blocks = [v for v in vetoes if v.startswith("block")]
    lines += ["", f"Veto: {len(vetoes)} calls | {len(vetoes) - len(blocks)} allow | {len(blocks)} block"]
    lines += [f"  - {v[len('block: '):]}" for v in blocks[-5:]]

    wins = sum(1 for p, _ in closed if p > 0)
    total = sum(p for p, _ in closed)
    rs = [r for _, r in closed if r is not None]
    avg_r = f"{sum(rs) / len(rs):+.2f}R" if rs else "n/a"
    lines += ["", f"Paper trades closed: {len(closed)} | wins {wins} | P/L {total:+.2f} | avg {avg_r}",
              f"Open paper trades: {open_count}"]

    lines += ["", "Errors:"] + ([f"  {kind}: {n}" for kind, n in errors] or ["  none ✅"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise recent bot activity")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--telegram", action="store_true", help="also send the report to Telegram")
    args = parser.parse_args()

    if not DB.exists():
        print(f"No database at {DB}: run the executor first")
        return 1
    report = build_report(args.days)
    print(report)
    if args.telegram:
        TelegramNotifier.from_env().send(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())