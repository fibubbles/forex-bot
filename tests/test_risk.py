from datetime import datetime, timezone

import pytest

from src.config_schema import RiskConfig
from src.risk import AccountState, SymbolSpec, check_entry, position_size

# EURUSD, 5 digits. On a cent account the same spec applies with equity/tick_value in USC.
# Real values are always read from MT5 via SymbolSpec.from_mt5().
SPEC = SymbolSpec(point=0.00001, tick_size=0.00001, tick_value=1.0,
                  volume_min=0.01, volume_max=100.0, volume_step=0.01)
CFG = RiskConfig(risk_per_trade_pct=1.0, max_open_positions=1, daily_loss_limit_pct=2.0,
                 weekly_loss_limit_pct=4.0, max_drawdown_pct=15.0, max_spread_multiplier=2.0,
                 friday_close_hours_before=3)
WEDNESDAY = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
SL_50_PIPS = 0.0050


def _acct(equity: float = 10_000.0, **overrides) -> AccountState:
    base = dict(equity=equity, peak_equity=equity, day_start_equity=equity,
                week_start_equity=equity, open_positions=0)
    base.update(overrides)
    return AccountState(**base)


def _check(acct, sl=SL_50_PIPS, spread=13, now=WEDNESDAY):
    return check_entry(CFG, acct, SPEC, sl, spread, 13, now)


# --- position sizing -------------------------------------------------------
def test_position_size_basic():
    assert position_size(10_000, 1.0, SL_50_PIPS, SPEC) == pytest.approx(0.20)


def test_position_size_rounds_down_never_up():
    assert position_size(10_000, 1.0, 0.0033, SPEC) == pytest.approx(0.30)  # raw 0.303


def test_standard_account_too_small_returns_zero():
    assert position_size(11.0, 1.0, SL_50_PIPS, SPEC) == 0.0  # ~$11 cannot risk 1% at min lot


def test_cent_account_is_tradable():
    assert position_size(1_100.0, 1.0, SL_50_PIPS, SPEC) == pytest.approx(0.02)  # ~$11 = 1100 USC


def test_risk_above_hard_cap_raises():
    with pytest.raises(ValueError):
        position_size(10_000, 5.0, SL_50_PIPS, SPEC)


# --- entry checks ----------------------------------------------------------
def test_allowed_entry():
    d = _check(_acct())
    assert d.allowed
    assert d.lots == pytest.approx(0.20)
    assert d.risk_amount == pytest.approx(100.0)


@pytest.mark.parametrize("overrides, expected", [
    (dict(peak_equity=12_000.0), "KILL SWITCH"),       # 16.7% below peak
    (dict(day_start_equity=10_300.0), "Daily loss"),   # 2.9% down today
    (dict(week_start_equity=10_500.0), "Weekly loss"), # 4.8% down this week
    (dict(open_positions=1), "Max open positions"),
])
def test_blocking_rules(overrides, expected):
    d = _check(_acct(**overrides))
    assert not d.allowed
    assert d.lots == 0
    assert any(expected in r for r in d.reasons)


def test_wide_spread_blocks():
    d = _check(_acct(), spread=30)
    assert not d.allowed and any("Spread" in r for r in d.reasons)


def test_friday_cutoff_blocks():
    friday = datetime(2026, 9, 25, 19, tzinfo=timezone.utc)  # 15:00 New York, 2h before close
    d = _check(_acct(), now=friday)
    assert not d.allowed and any("Friday" in r for r in d.reasons)


def test_missing_sl_blocks():
    d = _check(_acct(), sl=0.0)
    assert not d.allowed