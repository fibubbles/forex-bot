"""Config validation with hard safety caps.

Hard caps live in code, not config: a typo in config.yaml can never
push risk beyond these limits — the bot refuses to start instead.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

HARD_MAX_RISK_PER_TRADE_PCT = 2.0
HARD_MAX_OPEN_POSITIONS = 2
HARD_MAX_DAILY_LOSS_PCT = 5.0
HARD_MAX_WEEKLY_LOSS_PCT = 10.0
HARD_MAX_DRAWDOWN_PCT = 25.0
HARD_MAX_MICRO_LOT = 0.01


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BrokerConfig(_Strict):
    symbol: str = Field(min_length=3)
    timeframe: Literal["M15", "H1", "H4", "D1"]
    magic_number: int = Field(gt=0)
    terminal_path: str | None = None


class RiskConfig(_Strict):
    risk_per_trade_pct: float = Field(gt=0, le=HARD_MAX_RISK_PER_TRADE_PCT)
    max_open_positions: int = Field(ge=1, le=HARD_MAX_OPEN_POSITIONS)
    daily_loss_limit_pct: float = Field(gt=0, le=HARD_MAX_DAILY_LOSS_PCT)
    weekly_loss_limit_pct: float = Field(gt=0, le=HARD_MAX_WEEKLY_LOSS_PCT)
    max_drawdown_pct: float = Field(gt=0, le=HARD_MAX_DRAWDOWN_PCT)
    max_spread_multiplier: float = Field(ge=1.0, le=5.0)
    friday_close_hours_before: float = Field(ge=0, le=12)

    @model_validator(mode="after")
    def _limits_are_ordered(self) -> "RiskConfig":
        if not (
            self.risk_per_trade_pct
            <= self.daily_loss_limit_pct
            <= self.weekly_loss_limit_pct
            <= self.max_drawdown_pct
        ):
            raise ValueError(
                "Expected risk_per_trade <= daily_loss <= weekly_loss <= max_drawdown"
            )
        return self


class ModelConfig(_Strict):
    prob_threshold: float = Field(ge=0.5, lt=1.0)


class DataConfig(_Strict):
    history_start: date


class MicroLiveConfig(_Strict):
    """Live experiment on a tiny account: fixed minimum lot and an equity floor instead of % risk."""
    account_login: int = Field(gt=0)
    server: str = Field(min_length=1)
    fixed_lot: float = Field(gt=0, le=HARD_MAX_MICRO_LOT)
    equity_floor: float = Field(gt=0)


class AppConfig(_Strict):
    mode: Literal["dry_run", "demo", "live"]
    broker: BrokerConfig
    risk: RiskConfig
    model: ModelConfig
    data: DataConfig
    micro_live: MicroLiveConfig | None = None

    @model_validator(mode="after")
    def _live_needs_micro(self) -> "AppConfig":
        if self.mode == "live" and self.micro_live is None:
            raise ValueError("mode=live requires a micro_live section")
        if self.mode != "live" and self.micro_live is not None:
            raise ValueError("micro_live is only allowed with mode=live")
        return self


class Secrets(_Strict):
    mt5_login: int
    mt5_password: SecretStr
    mt5_server: str = Field(min_length=1)


def load_config(path: str | Path = "config.yaml", env_file: str | None = None) -> AppConfig:
    load_dotenv()
    if env_file:
        load_dotenv(env_file, override=True)
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cfg = AppConfig.model_validate(raw)
    if cfg.mode == "live" and os.getenv("LIVE_TRADING_CONFIRMED") != "YES":
        raise RuntimeError("mode=live requires LIVE_TRADING_CONFIRMED=YES in the env file")
    return cfg


def load_secrets(env_file: str | None = None) -> Secrets:
    """MT5 credentials. env_file (e.g. '.env.demo') overrides values from .env."""
    load_dotenv()
    if env_file:
        if not Path(env_file).exists():
            raise FileNotFoundError(f"{env_file} not found")
        load_dotenv(env_file, override=True)
    return Secrets(
        mt5_login=os.getenv("MT5_LOGIN"),
        mt5_password=os.getenv("MT5_PASSWORD"),
        mt5_server=os.getenv("MT5_SERVER"),
    )