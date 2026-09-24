import json

import pytest

from src.db import TradeLog


def test_decision_and_event_roundtrip(tmp_path):
    log = TradeLog(tmp_path / "bot.db")
    log.log_decision("2026-09-23T08:00:00+00:00", "dry_run", "skip", reasons=["no signal"])
    log.log_event("INFO", "startup", "bot started")

    d = log.recent_decisions(1)[0]
    assert d["action"] == "skip"
    assert json.loads(d["reasons"]) == ["no signal"]


def test_long_trade_slippage_and_r(tmp_path):
    log = TradeLog(tmp_path / "bot.db")
    log.record_open(ticket=101, mode="demo", strategy="s", side="long", lots=0.02,
                    requested_price=1.10000, entry_price=1.10003,
                    sl=1.09700, tp=1.10400, spread_points=13)
    assert log.open_trades()[0]["slippage_points"] == pytest.approx(3.0)

    log.record_close(101, exit_price=1.10403, profit=8.0)
    assert log.open_trades() == []
    assert log.trade(101)["r_multiple"] == pytest.approx(0.00400 / 0.00303)


def test_short_trade_slippage_sign(tmp_path):
    log = TradeLog(tmp_path / "bot.db")
    log.record_open(ticket=202, mode="demo", strategy="s", side="short", lots=0.02,
                    requested_price=1.10000, entry_price=1.09998,
                    sl=1.10300, tp=1.09600, spread_points=13)
    assert log.trade(202)["slippage_points"] == pytest.approx(2.0)  # sold lower = worse


def test_last_bar_time_is_per_mode(tmp_path):
    log = TradeLog(tmp_path / "bot.db")
    log.log_decision("2026-09-24T01:00:00+00:00", "dry_run", "no_signal")
    log.log_decision("2026-09-24T05:00:00+00:00", "demo", "no_signal")
    assert log.last_bar_time("dry_run") == "2026-09-24T01:00:00+00:00"
    assert log.last_bar_time("demo") == "2026-09-24T05:00:00+00:00"
    assert log.last_bar_time("live") is None