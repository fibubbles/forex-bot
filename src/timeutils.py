"""Time helpers: broker server time <-> UTC, bar close checks, Friday cutoff.

Assumption (standard for most MT5 brokers, verified for Valetax: +3 in Sept):
server time = New York time + 7h, so the NY 17:00 close is 00:00 server time.
This gives UTC+2 in winter and UTC+3 in summer, switching with US DST.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

NY_TZ = ZoneInfo("America/New_York")
SERVER_MINUS_NY = timedelta(hours=7)
NY_CLOSE_HOUR = 17  # forex week closes Friday 17:00 New York time

TIMEFRAME_DURATION: dict[str, timedelta] = {
    "M15": timedelta(minutes=15),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
}


def server_offset_hours(at_utc: datetime | None = None) -> int:
    """Expected broker server offset from UTC at a given moment."""
    at_utc = at_utc or datetime.now(timezone.utc)
    ny_offset = at_utc.astimezone(NY_TZ).utcoffset()
    return int((ny_offset + SERVER_MINUS_NY) / timedelta(hours=1))


def server_epoch_to_utc(epoch_seconds: pd.Series) -> pd.Series:
    """Convert MT5 bar times (server wall-clock encoded as epoch) to tz-aware UTC.

    Times that fall into a DST gap/overlap happen only while the market is
    closed, so they become NaT and are removed later by data_checks.
    """
    server_wall = pd.to_datetime(epoch_seconds, unit="s")
    ny_wall = server_wall - SERVER_MINUS_NY
    return (
        ny_wall.dt.tz_localize(NY_TZ, ambiguous="NaT", nonexistent="NaT")
        .dt.tz_convert("UTC")
    )


def bar_close_utc(bar_open_utc: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    return bar_open_utc + TIMEFRAME_DURATION[timeframe]


def is_bar_closed(bar_open_utc: pd.Timestamp, timeframe: str, now_utc: datetime | None = None) -> bool:
    now_utc = now_utc or datetime.now(timezone.utc)
    return bar_close_utc(bar_open_utc, timeframe) <= pd.Timestamp(now_utc)


def is_past_friday_cutoff(hours_before_close: float, now_utc: datetime | None = None) -> bool:
    """True on Friday once we are within `hours_before_close` of the NY 17:00 close."""
    now_ny = (now_utc or datetime.now(timezone.utc)).astimezone(NY_TZ)
    if now_ny.weekday() != 4:  # 4 = Friday
        return False
    close_ny = now_ny.replace(hour=NY_CLOSE_HOUR, minute=0, second=0, microsecond=0)
    return now_ny >= close_ny - timedelta(hours=hours_before_close)


def utc_to_server_wall(time_utc: pd.Series) -> pd.Series:
    """Broker server wall-clock time (NY + 7h) as naive timestamps."""
    return time_utc.dt.tz_convert(NY_TZ).dt.tz_localize(None) + SERVER_MINUS_NY


def is_market_open(now_utc: datetime | None = None) -> bool:
    """Forex week: Sunday 17:00 to Friday 17:00 New York time (holidays not handled)."""
    ny = (now_utc or datetime.now(timezone.utc)).astimezone(NY_TZ)
    wd, hour = ny.weekday(), ny.hour
    if wd == 5:                       # Saturday
        return False
    if wd == 4:                       # Friday
        return hour < NY_CLOSE_HOUR
    if wd == 6:                       # Sunday
        return hour >= NY_CLOSE_HOUR
    return True


def hours_since_week_open(now_utc: datetime | None = None) -> float:
    """Hours since the most recent Sunday 17:00 New York open."""
    ny = (now_utc or datetime.now(timezone.utc)).astimezone(NY_TZ)
    days_since_sunday = (ny.weekday() - 6) % 7
    sunday_open = (ny - timedelta(days=days_since_sunday)).replace(
        hour=NY_CLOSE_HOUR, minute=0, second=0, microsecond=0
    )
    return (ny - sunday_open).total_seconds() / 3600