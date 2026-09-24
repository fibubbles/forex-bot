from datetime import datetime, timedelta, timezone

import pytest

from src.timeutils import is_market_open
from src.watchdog import evaluate

WED = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def _hb(ts: datetime, last_bar: datetime | None) -> dict:
    return {"ts": ts.isoformat(), "last_bar": last_bar.isoformat() if last_bar else None}


@pytest.mark.parametrize("now, expected", [
    (WED, True),
    (datetime(2026, 9, 26, 12, tzinfo=timezone.utc), False),   # Saturday
    (datetime(2026, 9, 25, 22, tzinfo=timezone.utc), False),   # Friday 18:00 NY
    (datetime(2026, 9, 27, 22, tzinfo=timezone.utc), True),    # Sunday 18:00 NY
])
def test_is_market_open(now, expected):
    assert is_market_open(now) is expected


def test_market_closed_is_never_a_problem():
    assert evaluate(None, datetime(2026, 9, 26, 12, tzinfo=timezone.utc)) is None


def test_missing_heartbeat_is_a_problem():
    assert "No heartbeat" in evaluate(None, WED)


def test_stale_heartbeat_means_not_running():
    hb = _hb(WED - timedelta(minutes=20), WED - timedelta(hours=5))
    assert "NOT running" in evaluate(hb, WED)


def test_healthy():
    hb = _hb(WED - timedelta(seconds=30), WED - timedelta(hours=5))
    assert evaluate(hb, WED) is None


def test_alive_but_stuck_on_old_bar():
    hb = _hb(WED - timedelta(seconds=30), WED - timedelta(hours=12))
    assert "STUCK" in evaluate(hb, WED)


def test_no_false_alarm_right_after_sunday_open():
    monday_0000_utc = datetime(2026, 9, 28, 0, tzinfo=timezone.utc)      # Sunday 20:00 NY
    friday_last_bar = datetime(2026, 9, 25, 17, tzinfo=timezone.utc)
    hb = _hb(monday_0000_utc - timedelta(seconds=30), friday_last_bar)
    assert evaluate(hb, monday_0000_utc) is None