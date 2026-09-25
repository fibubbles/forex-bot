"""Activity summary from the bot log: decisions, veto behaviour, trades, API cost, errors."""
import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.notifier import TelegramNotifier

DB = Path("state/bot.db")
VETO_COST_USD = 0.06  # approximate Claude Haiku + web search cost per veto call


def build_report(days: int, mode: str | None) -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    mode_sql = " AND mode = ?" if mode else ""
    params = (since, mode) if mode else (since,)

    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True)) as conn:
        actions = conn.execute(
            f"SELECT action, COUNT(*) FROM decisions WHERE created_utc >= ?{mode_sql} "
            "GROUP BY action ORDER BY 2 DESC", params).fetchall()
        vetoes = [r[0] for r in conn.execute(
            f"SELECT veto FROM decisions WHERE created_utc >= ?{mode_sql} "
            "AND veto IS NOT NULL AND veto != 'disabled'", params).fetchall()]
        closed = conn.execute(
            f"SELECT profit, r_multiple FROM trades WHERE close_utc >= ?{mode_sql}", params).fetchall()
        open_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE close_utc IS NULL" + (" AND mode = ?" if mode else ""),
            (mode,) if mode else ()).fetchone()[0]
        errors = conn.execute(
            "SELECT kind, COUNT(*) FROM events WHERE created_utc >= ? AND level = 'ERROR' GROUP BY kind",
            (since,)).fetchall()

    title = f"📋 Report: last {days} days" + (f" ({mode})" if mode else "")
    lines = [title, "", "Decisions:"]
    lines += [f"  {action}: {n}" for action, n in actions] or ["  none"]

    blocks = [v for v in vetoes if v.startswith("block")]
    lines += ["", f"Veto: {len(vetoes)} calls | {len(vetoes) - len(blocks)} allow | {len(blocks)} block",
              f"  est. API cost: ~${len(vetoes) * VETO_COST_USD:.2f}"]
    lines += [f"  - {v[len('block: '):]}" for v in blocks[-5:]]

    profits = [p for p, _ in closed if p is not None]
    total = sum(profits)
    wins = sum(1 for p in profits if p > 0)
    rs = [r for _, r in closed if r is not None]
    avg_r = f"{sum(rs) / len(rs):+.2f}R" if rs else "n/a"
    lines += ["", f"Trades closed: {len(profits)} | wins {wins} | P/L {total:+.2f} | avg {avg_r}",
              "  (cent account: P/L is in USC, divide by 100 for USD)"]
    if total > 0:
        lines.append(f"  largest single trade: {max(profits) / total * 100:.0f}% of total profit")
    lines.append(f"Open trades: {open_count}")

    lines += ["", "Errors:"] + ([f"  {kind}: {n}" for kind, n in errors] or ["  none ✅"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise recent bot activity")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--mode", choices=["dry_run", "demo", "live"], default=None,
                        help="only include this mode")
    parser.add_argument("--telegram", action="store_true", help="also send the report to Telegram")
    args = parser.parse_args()

    if not DB.exists():
        print(f"No database at {DB}: run the executor first")
        return 1
    report = build_report(args.days, args.mode)
    print(report)
    if args.telegram:
        TelegramNotifier.from_env().send(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())