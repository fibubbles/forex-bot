from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from src.broker import LiveBroker, OrderError, assert_live_account_allowed
from src.config_schema import AppConfig, MicroLiveConfig, RiskConfig
from src.risk import SymbolSpec, check_micro_entry
from src.state import EquityTracker
from tests.test_broker import MAGIC, FakeMT5

WED = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
RISK = RiskConfig(risk_per_trade_pct=1.0, max_open_positions=1, daily_loss_limit_pct=2.0,
                  weekly_loss_limit_pct=4.0, max_drawdown_pct=15.0, max_spread_multiplier=2.0,
                  friday_close_hours_before=3)
MICRO = MicroLiveConfig(account_login=2391005586, server="ValetaxIntl-Live8",
                        fixed_lot=0.01, equity_floor=5.0)
SPEC = SymbolSpec(0.00001, 0.00001, 1.0, 0.01, 100.0, 0.01)
BASE = {
    "broker": {"symbol": "EURUSD", "timeframe": "H4", "magic_number": 1},
    "risk": RISK.model_dump(),
    "model": {"prob_threshold": 0.6},
    "data": {"history_start": "2015-01-01"},
}


def test_live_requires_micro_section():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({**BASE, "mode": "live"})


def test_micro_section_only_allowed_in_live():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({**BASE, "mode": "demo", "micro_live": MICRO.model_dump()})


def test_micro_lot_hard_cap():
    with pytest.raises(ValidationError):
        MicroLiveConfig(account_login=1, server="x", fixed_lot=0.02, equity_floor=5.0)


def test_micro_entry_allowed():
    d = check_micro_entry(MICRO, RISK, 11.62, 0, SPEC, 0.0030, 15, 15, WED)
    assert d.allowed and d.lots == 0.01
    assert d.risk_amount == pytest.approx(3.0)


def test_equity_floor_blocks():
    d = check_micro_entry(MICRO, RISK, 4.90, 0, SPEC, 0.0030, 15, 15, WED)
    assert not d.allowed and any("EQUITY FLOOR" in r for r in d.reasons)


def test_only_one_position():
    d = check_micro_entry(MICRO, RISK, 11.62, 1, SPEC, 0.0030, 15, 15, WED)
    assert not d.allowed


def test_live_account_guard():
    ok = NS(login=2391005586, server="ValetaxIntl-Live8")
    assert_live_account_allowed(ok, 2391005586, "ValetaxIntl-Live8")
    with pytest.raises(OrderError):
        assert_live_account_allowed(NS(login=999, server="ValetaxIntl-Live8"), 2391005586, "ValetaxIntl-Live8")
    with pytest.raises(OrderError):
        assert_live_account_allowed(NS(login=2391005586, server="Other-Live"), 2391005586, "ValetaxIntl-Live8")


def test_broker_rejects_lot_above_max():
    api = FakeMT5()
    broker = LiveBroker(api, "EURUSD", MAGIC, 5, sleep=lambda s: None, max_lot=0.01)
    with pytest.raises(OrderError, match="hard maximum"):
        broker.open("long", 0.02, 0.0030, 0.0040)
    assert api.sent == []  # nothing reached the broker


def test_floor_latch_survives_restart(tmp_path):
    path = tmp_path / "s.json"
    t = EquityTracker(path)
    t.update(11.62, 0, 100.0, WED)
    t.latch("equity floor")
    assert EquityTracker(path).killed


def test_micro_blocks_invalid_spec():
    bad = SymbolSpec(0.01, 0.01, 0.0, 0.01, 100.0, 0.01)
    d = check_micro_entry(MICRO, RISK, 1488.79, 0, bad, 25.0, 30, 30, WED)
    assert not d.allowed and any("spec invalid" in r for r in d.reasons)