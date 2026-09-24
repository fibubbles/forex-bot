import pytest

from src.db import TradeLog
from src.paper import PaperBroker
from src.risk import SymbolSpec

SPEC = SymbolSpec(point=0.00001, tick_size=0.00001, tick_value=1.0,
                  volume_min=0.01, volume_max=100.0, volume_step=0.01)
SPREAD = 13  # points


def _broker(tmp_path) -> PaperBroker:
    return PaperBroker(TradeLog(tmp_path / "bot.db"), SPEC, SPREAD)


def _open_long(pb: PaperBroker) -> int:
    # bid 1.10000 / ask 1.10013 -> entry 1.10013, risk 30 pips, reward 40 pips
    return pb.open("long", 0.02, 1.10000, 1.10013, sl=1.09713, tp=1.10413,
                   strategy="t", spread_points=SPREAD)


def test_long_tp_hit(tmp_path):
    pb = _broker(tmp_path)
    ticket = _open_long(pb)
    assert pb.on_bar(1.10010, 1.10420, 1.10000, 1.10400) == [ticket]
    t = pb.log.trade(ticket)
    assert t["exit_price"] == pytest.approx(1.10413)
    assert t["profit"] == pytest.approx(8.0)          # 400 points x 0.02 lot
    assert t["r_multiple"] == pytest.approx(0.004 / 0.003)


def test_same_bar_counts_as_sl(tmp_path):
    pb = _broker(tmp_path)
    ticket = _open_long(pb)
    pb.on_bar(1.10010, 1.10500, 1.09700, 1.10000)     # both barriers inside one bar
    assert pb.log.trade(ticket)["profit"] == pytest.approx(-6.0)


def test_short_stopped_by_spread(tmp_path):
    pb = _broker(tmp_path)
    ticket = pb.open("short", 0.02, 1.10000, 1.10013, sl=1.10300, tp=1.09700,
                     strategy="t", spread_points=SPREAD)
    pb.on_bar(1.10000, 1.10290, 1.09950, 1.10200)     # bid high 1.10290 -> ask high 1.10303
    assert pb.log.trade(ticket)["profit"] == pytest.approx(-6.0)


def test_no_hit_stays_open_then_close_all(tmp_path):
    pb = _broker(tmp_path)
    ticket = _open_long(pb)
    assert pb.on_bar(1.10010, 1.10100, 1.09900, 1.10050) == []
    assert len(pb.open_trades()) == 1
    assert pb.close_all(bid=1.10050, ask=1.10063, why="friday") == [ticket]
    assert pb.open_trades() == []


def test_paper_tickets_are_negative_and_unique(tmp_path):
    pb = _broker(tmp_path)
    assert _open_long(pb) == -1
    assert _open_long(pb) == -2