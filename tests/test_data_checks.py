from datetime import datetime, timezone

import pandas as pd

from src.data_checks import validate_bars


def _bars(times: list[str], closes: list[float]) -> pd.DataFrame:
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({
        "time": pd.to_datetime(times, utc=True),
        "open": c, "high": c + 0.001, "low": c - 0.001, "close": c,
        "tick_volume": 100, "spread": 10,
    })


def test_drops_duplicates_invalid_and_unclosed_bar():
    df = _bars(
        ["2026-09-22 00:00", "2026-09-22 04:00", "2026-09-22 04:00",
         "2026-09-22 08:00", "2026-09-23 08:00"],
        [1.10, 1.11, 1.11, 1.12, 1.13],
    )
    df.loc[3, "high"] = 1.0  # high below close -> invalid
    now = datetime(2026, 9, 23, 11, tzinfo=timezone.utc)  # last bar closes at 12:00

    clean, rep = validate_bars(df, "H4", now_utc=now)

    assert rep.dropped_duplicates == 1
    assert rep.dropped_invalid_ohlc == 1
    assert rep.dropped_unclosed_bar == 1
    assert len(clean) == 2


def test_weekend_gap_ignored_but_midweek_gap_flagged():
    df = _bars(
        ["2026-09-22 00:00", "2026-09-23 00:00",   # Tue -> Wed: 24h gap
         "2026-09-25 16:00", "2026-09-27 21:00"],  # Fri -> Sun: weekend
        [1.10, 1.11, 1.12, 1.13],
    )
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)

    _, rep = validate_bars(df, "H4", now_utc=now)

    assert len(rep.unexpected_gaps) == 2  # Tue->Wed and Wed->Fri; weekend not flagged
    assert not any("2026-09-27" in g for g in rep.unexpected_gaps)


def test_drops_weekend_bars():
    df = _bars(
        ["2022-04-08 13:00", "2022-04-09 21:00", "2022-04-11 01:00"],  # Fri, Sat (junk), Mon
        [1.09, 1.09, 1.10],
    )
    now = datetime(2022, 4, 12, tzinfo=timezone.utc)

    clean, rep = validate_bars(df, "H4", now_utc=now)

    assert rep.dropped_weekend == 1
    assert len(clean) == 2