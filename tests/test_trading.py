from types import SimpleNamespace as NS

import pandas as pd
import pytest

from src.broker import LiveBroker
from src.db import TradeLog
from src.paper import PaperBroker
from src.risk import SymbolSpec
from src.strategy import Signal
from src.trading import LiveTrading, PaperTrading
from tests.test_broker import MAGIC, FakeMT5

SIGNAL = Signal("long", sl_distance=0.0030, tp_distance=0.0040, reason="r", strategy="s")


class FakeMT5WithHistory(FakeMT5):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.deals = []

    def order_send(self, req):
        res = super().order_send(req)
        if res.retcode == self.TRADE_RETCODE_DONE:
            if "position" in req:  # our close -> closing deal in history
                self.deals.append(NS(position_id=req["position"], entry=1, price=req["price"],
                                     profit=1.6, swap=0.0, commission=0.0, fee=0.0))
            else:
                self.positions_list[-1].profit = 0.0
        return res

    def history_deals_get(self, position=None):
        return tuple(d for d in self.deals if d.position_id == position)

    def server_closes(self, ticket, price, profit, with_history=True):
        """Simulate the broker's server hitting SL/TP."""
        self.positions_list = [p for p in self.positions_list if p.ticket != ticket]
        if with_history:
            self.deals.append(NS(position_id=ticket, entry=1, price=price,
                                 profit=profit, swap=0.0, commission=0.0, fee=0.0))


def _live(tmp_path):
    api = FakeMT5WithHistory()
    broker = LiveBroker(api, "EURUSD", MAGIC, digits=5, sleep=lambda s: None)
    return api, LiveTrading(broker, TradeLog(tmp_path / "bot.db"))


def test_live_open_records_trade(tmp_path):
    api, lt = _live(tmp_path)
    opened = lt.open(SIGNAL, 0.02, 0, 0, 0, 1.10000, 1.10013, 13)
    t = lt.open_trades()[0]
    assert t["ticket"] == opened.ticket and t["mode"] == "demo"
    assert (t["entry_price"], t["sl"], t["tp"]) == (1.10013, 1.09713, 1.10413)
    assert lt.open_count() == 1


def test_sl_hit_on_server_is_recorded(tmp_path):
    api, lt = _live(tmp_path)
    opened = lt.open(SIGNAL, 0.02, 0, 0, 0, 1.10000, 1.10013, 13)
    api.server_closes(opened.ticket, price=1.09713, profit=-6.0)

    assert lt.check_exits() == [opened.ticket]
    t = lt.log.trade(opened.ticket)
    assert t["exit_price"] == 1.09713 and t["profit"] == -6.0
    assert t["r_multiple"] == pytest.approx(-1.0)


def test_missing_history_is_retried_later(tmp_path):
    api, lt = _live(tmp_path)
    opened = lt.open(SIGNAL, 0.02, 0, 0, 0, 1.10000, 1.10013, 13)
    api.server_closes(opened.ticket, price=1.09713, profit=-6.0, with_history=False)

    assert lt.check_exits() == []
    assert len(lt.open_trades()) == 1  # still open in the DB until the deal appears


def test_close_all_records_exits(tmp_path):
    api, lt = _live(tmp_path)
    lt.open(SIGNAL, 0.02, 0, 0, 0, 1.10000, 1.10013, 13)
    assert len(lt.close_all(1.10000, 1.10013, "friday")) == 1
    assert lt.open_trades() == []


def test_orphan_position_is_adopted(tmp_path):
    api, lt = _live(tmp_path)
    api.positions_list.append(NS(ticket=777, identifier=777, magic=MAGIC, symbol="EURUSD", volume=0.02,
                                 type=api.POSITION_TYPE_BUY, price_open=1.1, sl=1.097, tp=1.104, profit=0.0))
    assert lt.adopt_orphans() == [777]
    assert lt.open_trades()[0]["ticket"] == 777


def test_paper_adapter_roundtrip(tmp_path):
    spec = SymbolSpec(0.00001, 0.00001, 1.0, 0.01, 100.0, 0.01)
    pt = PaperTrading(PaperBroker(TradeLog(tmp_path / "bot.db"), spec, 13))
    opened = pt.open(SIGNAL, 0.02, 1.10013, 1.09713, 1.10413, 1.10000, 1.10013, 13)
    bars = pd.DataFrame([{"open": 1.10010, "high": 1.10420, "low": 1.10000, "close": 1.10400}])
    assert pt.check_exits(bars) == [opened.ticket]