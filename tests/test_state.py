from datetime import datetime, timezone
from types import SimpleNamespace

from src.config_schema import RiskConfig
from src.risk import SymbolSpec, check_entry
from src.state import EquityTracker, bot_positions, foreign_positions

MAGIC = 20260923
WED = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
THU = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
NEXT_MON = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def _pos(symbol: str = "EURUSD", magic: int = MAGIC, ticket: int = 1):
    return SimpleNamespace(symbol=symbol, magic=magic, ticket=ticket)


def test_position_filters_by_symbol_and_magic():
    positions = [_pos(ticket=1), _pos(magic=0, ticket=2), _pos(symbol="GBPUSD", ticket=3)]
    assert [p.ticket for p in bot_positions(positions, "EURUSD", MAGIC)] == [1]
    assert [p.ticket for p in foreign_positions(positions, "EURUSD", MAGIC)] == [2]
    assert bot_positions(None, "EURUSD", MAGIC) == []


def test_restart_with_open_position_blocks_new_entry(tmp_path):
    # After a restart MT5 still holds our position -> open_positions=1 -> no second entry.
    tracker = EquityTracker(tmp_path / "s.json")
    open_count = len(bot_positions([_pos()], "EURUSD", MAGIC))
    acct = tracker.update(10_000, open_count, 15.0, WED)

    cfg = RiskConfig(risk_per_trade_pct=1.0, max_open_positions=1, daily_loss_limit_pct=2.0,
                     weekly_loss_limit_pct=4.0, max_drawdown_pct=15.0, max_spread_multiplier=2.0,
                     friday_close_hours_before=3)
    spec = SymbolSpec(0.00001, 0.00001, 1.0, 0.01, 100.0, 0.01)
    d = check_entry(cfg, acct, spec, 0.005, 13, 13, WED)
    assert not d.allowed
    assert any("Max open positions" in r for r in d.reasons)


def test_day_and_week_reset(tmp_path):
    t = EquityTracker(tmp_path / "s.json")
    t.update(10_000, 0, 15.0, WED)
    a = t.update(9_900, 0, 15.0, WED)
    assert a.day_start_equity == 10_000
    a = t.update(9_900, 0, 15.0, THU)        # new server day
    assert a.day_start_equity == 9_900
    assert a.week_start_equity == 10_000
    a = t.update(9_800, 0, 15.0, NEXT_MON)   # new server week
    assert a.week_start_equity == 9_800


def test_peak_only_goes_up(tmp_path):
    t = EquityTracker(tmp_path / "s.json")
    t.update(10_000, 0, 15.0, WED)
    t.update(10_500, 0, 15.0, WED)
    assert t.update(10_200, 0, 15.0, WED).peak_equity == 10_500


def test_kill_switch_latches_and_survives_restart(tmp_path):
    path = tmp_path / "s.json"
    t = EquityTracker(path)
    t.update(10_000, 0, 15.0, WED)
    t.update(8_400, 0, 15.0, WED)       # 16% drawdown -> latch
    assert t.killed
    t.update(9_900, 0, 15.0, THU)       # equity recovers...
    assert t.killed                     # ...still latched
    assert EquityTracker(path).killed   # survives restart (reloaded from disk)

    t.reset_kill_switch(new_peak=9_900)
    assert not EquityTracker(path).killed