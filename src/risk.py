"""Risk manager: position sizing and pre-trade checks. Pure functions, no MT5 calls.

Safety principles:
- Lot size is always rounded DOWN; if even the minimum lot risks too much, don't trade.
- Every failing rule is reported; any single failure blocks the entry.
- Hard caps from config_schema are re-checked here, independent of config.yaml.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from src.config_schema import HARD_MAX_RISK_PER_TRADE_PCT, MicroLiveConfig, RiskConfig
from src.timeutils import is_past_friday_cutoff


@dataclass(frozen=True)
class SymbolSpec:
    point: float
    tick_size: float
    tick_value: float  # account currency per 1 lot per tick (USC on a cent account)
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: int = 0

    @classmethod
    def from_mt5(cls, info) -> "SymbolSpec":
        return cls(
            point=info.point,
            tick_size=info.trade_tick_size,
            tick_value=info.trade_tick_value,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            stops_level_points=info.trade_stops_level,
        )

    @property
    def valid(self) -> bool:
        """False when the broker has not delivered full symbol data yet (e.g. right after a login)."""
        return self.tick_size > 0 and self.tick_value > 0 and self.volume_min > 0 and self.volume_step > 0


@dataclass(frozen=True)
class AccountState:
    equity: float
    peak_equity: float
    day_start_equity: float
    week_start_equity: float
    open_positions: int


@dataclass
class RiskDecision:
    allowed: bool
    lots: float = 0.0
    risk_amount: float = 0.0
    reasons: list[str] = field(default_factory=list)


def _step_decimals(step: float) -> int:
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def _pct_drop(start: float, now: float) -> float:
    return 0.0 if start <= 0 else (start - now) / start * 100


def position_size(equity: float, risk_pct: float, sl_distance: float, spec: SymbolSpec) -> float:
    """Lots such that hitting the SL loses at most `risk_pct` of equity. 0.0 = don't trade."""
    if not 0 < risk_pct <= HARD_MAX_RISK_PER_TRADE_PCT:
        raise ValueError(f"risk_pct {risk_pct} outside (0, {HARD_MAX_RISK_PER_TRADE_PCT}]")
    if equity <= 0 or sl_distance <= 0 or not spec.valid:
        return 0.0

    loss_per_lot = sl_distance / spec.tick_size * spec.tick_value
    raw_lots = equity * risk_pct / 100 / loss_per_lot
    lots = math.floor(raw_lots / spec.volume_step + 1e-9) * spec.volume_step
    lots = round(min(lots, spec.volume_max), _step_decimals(spec.volume_step))
    return lots if lots >= spec.volume_min else 0.0


def check_entry(
    cfg: RiskConfig,
    acct: AccountState,
    spec: SymbolSpec,
    sl_distance: float,
    spread_points: float,
    typical_spread_points: float,
    now_utc: datetime | None = None,
) -> RiskDecision:
    """Run every pre-trade rule. Entry is allowed only if all pass."""
    reasons: list[str] = []
    if not spec.valid:
        reasons.append("Symbol spec invalid (tick value/size is 0): broker data not loaded")

    dd = _pct_drop(acct.peak_equity, acct.equity)
    if dd >= cfg.max_drawdown_pct:
        reasons.append(f"KILL SWITCH: drawdown {dd:.1f}% >= {cfg.max_drawdown_pct}% (manual review required)")

    day = _pct_drop(acct.day_start_equity, acct.equity)
    if day >= cfg.daily_loss_limit_pct:
        reasons.append(f"Daily loss {day:.1f}% >= {cfg.daily_loss_limit_pct}%")

    week = _pct_drop(acct.week_start_equity, acct.equity)
    if week >= cfg.weekly_loss_limit_pct:
        reasons.append(f"Weekly loss {week:.1f}% >= {cfg.weekly_loss_limit_pct}%")

    if acct.open_positions >= cfg.max_open_positions:
        reasons.append(f"Max open positions reached ({acct.open_positions}/{cfg.max_open_positions})")

    if typical_spread_points > 0 and spread_points > cfg.max_spread_multiplier * typical_spread_points:
        reasons.append(
            f"Spread {spread_points:.0f} pts > {cfg.max_spread_multiplier}x typical {typical_spread_points:.0f}"
        )

    if is_past_friday_cutoff(cfg.friday_close_hours_before, now_utc):
        reasons.append("Friday cutoff: no new entries before weekend")

    min_sl = spec.stops_level_points * spec.point
    if sl_distance <= min_sl:
        reasons.append(f"SL distance {sl_distance:.5f} not above broker stops level {min_sl:.5f}")

    lots = position_size(acct.equity, cfg.risk_per_trade_pct, sl_distance, spec) if sl_distance > 0 else 0.0
    if lots == 0:
        reasons.append("Position too small: even min lot exceeds risk limit")

    if reasons:
        return RiskDecision(allowed=False, reasons=reasons)

    risk_amount = lots * sl_distance / spec.tick_size * spec.tick_value
    return RiskDecision(allowed=True, lots=lots, risk_amount=risk_amount)


def check_micro_entry(
    micro: MicroLiveConfig,
    cfg: RiskConfig,
    equity: float,
    open_positions: int,
    spec: SymbolSpec,
    sl_distance: float,
    spread_points: float,
    typical_spread_points: float,
    now_utc: datetime | None = None,
) -> RiskDecision:
    """Micro live experiment: fixed lot, equity floor, max 1 position (rules agreed with the user)."""
    reasons: list[str] = []
    if not spec.valid:
        reasons.append("Symbol spec invalid (tick value/size is 0): broker data not loaded")

    if equity < micro.equity_floor:
        reasons.append(f"EQUITY FLOOR: {equity:.2f} < {micro.equity_floor:.2f} (experiment over)")
    if open_positions >= 1:
        reasons.append(f"Max open positions reached ({open_positions}/1)")
    if not spec.volume_min <= micro.fixed_lot <= spec.volume_max:
        reasons.append(f"Fixed lot {micro.fixed_lot} outside broker limits {spec.volume_min}-{spec.volume_max}")
    if typical_spread_points > 0 and spread_points > cfg.max_spread_multiplier * typical_spread_points:
        reasons.append(
            f"Spread {spread_points:.0f} pts > {cfg.max_spread_multiplier}x typical {typical_spread_points:.0f}"
        )
    if is_past_friday_cutoff(cfg.friday_close_hours_before, now_utc):
        reasons.append("Friday cutoff: no new entries before weekend")
    min_sl = spec.stops_level_points * spec.point
    if sl_distance <= min_sl:
        reasons.append(f"SL distance {sl_distance:.5f} not above broker stops level {min_sl:.5f}")

    if reasons:
        return RiskDecision(allowed=False, reasons=reasons)
    risk_amount = micro.fixed_lot * sl_distance / spec.tick_size * spec.tick_value
    return RiskDecision(allowed=True, lots=micro.fixed_lot, risk_amount=risk_amount)