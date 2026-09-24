from types import SimpleNamespace as NS

import MetaTrader5 as mt5
import pytest

from src.broker import LiveBroker, OrderError, assert_demo_account, pick_filling

MAGIC = 20260923


class FakeMT5:
    """Minimal stand-in for the MetaTrader5 module (real constants are attached below)."""

    def __init__(self, send_codes=None, attach_sl=True, filling_flags=1):
        self.sent = []
        self.positions_list = []
        self.send_codes = list(send_codes or [])
        self.attach_sl = attach_sl
        self.filling_flags = filling_flags
        self._next_ticket = 1000

    def symbol_info(self, symbol):
        return NS(filling_mode=self.filling_flags)

    def symbol_info_tick(self, symbol):
        return NS(bid=1.10000, ask=1.10013)

    def order_check(self, req):
        return NS(retcode=0, comment="Done")

    def last_error(self):
        return (0, "ok")

    def positions_get(self, symbol=None):
        return tuple(self.positions_list)

    def order_send(self, req):
        self.sent.append(req)
        code = self.send_codes.pop(0) if self.send_codes else self.TRADE_RETCODE_DONE
        if code != self.TRADE_RETCODE_DONE:
            return NS(retcode=code, comment="not done", order=0, price=0.0)
        if "position" in req:  # closing order
            self.positions_list = [p for p in self.positions_list if p.ticket != req["position"]]
            return NS(retcode=code, comment="done", order=0, price=req["price"])
        self._next_ticket += 1
        is_buy = req["type"] == self.ORDER_TYPE_BUY
        self.positions_list.append(NS(
            ticket=self._next_ticket, identifier=self._next_ticket, magic=req["magic"],
            symbol=req["symbol"], volume=req["volume"],
            type=self.POSITION_TYPE_BUY if is_buy else self.POSITION_TYPE_SELL,
            price_open=req["price"], sl=req["sl"] if self.attach_sl else 0.0, tp=req["tp"],
        ))
        return NS(retcode=code, comment="done", order=self._next_ticket, price=req["price"])


for _name in ("TRADE_ACTION_DEAL", "ORDER_TYPE_BUY", "ORDER_TYPE_SELL", "ORDER_TIME_GTC",
              "ORDER_FILLING_FOK", "ORDER_FILLING_IOC", "ORDER_FILLING_RETURN",
              "TRADE_RETCODE_DONE", "TRADE_RETCODE_REQUOTE", "TRADE_RETCODE_PRICE_CHANGED",
              "TRADE_RETCODE_PRICE_OFF", "TRADE_RETCODE_NO_MONEY",
              "POSITION_TYPE_BUY", "POSITION_TYPE_SELL"):
    setattr(FakeMT5, _name, getattr(mt5, _name))


def _broker(api: FakeMT5) -> LiveBroker:
    return LiveBroker(api, "EURUSD", MAGIC, digits=5, sleep=lambda s: None)


@pytest.mark.parametrize("server, mode", [
    ("ValetaxIntl-Live8", mt5.ACCOUNT_TRADE_MODE_REAL),   # real account
    ("SomeBroker-Live", mt5.ACCOUNT_TRADE_MODE_DEMO),     # demo flag but live-looking server
])
def test_demo_guard_rejects(server, mode):
    with pytest.raises(OrderError):
        assert_demo_account(NS(login=1, server=server, trade_mode=mode), mt5.ACCOUNT_TRADE_MODE_DEMO)


def test_demo_guard_accepts_demo():
    assert_demo_account(NS(login=1, server="MetaQuotes-Demo", trade_mode=mt5.ACCOUNT_TRADE_MODE_DEMO),
                        mt5.ACCOUNT_TRADE_MODE_DEMO)


@pytest.mark.parametrize("flags, expected", [
    (1, mt5.ORDER_FILLING_FOK), (2, mt5.ORDER_FILLING_IOC), (0, mt5.ORDER_FILLING_RETURN),
])
def test_pick_filling(flags, expected):
    assert pick_filling(flags, FakeMT5()) == expected


def test_open_long_sends_sl_tp_and_verifies():
    api = FakeMT5()
    fill = _broker(api).open("long", 0.02, sl_distance=0.0030, tp_distance=0.0040)
    req = api.sent[-1]
    assert req["type"] == mt5.ORDER_TYPE_BUY and req["magic"] == MAGIC
    assert (req["price"], req["sl"], req["tp"]) == (1.10013, 1.09713, 1.10413)
    assert fill.price == 1.10013 and fill.lots == 0.02


def test_open_short_prices():
    api = FakeMT5()
    _broker(api).open("short", 0.02, sl_distance=0.0030, tp_distance=0.0040)
    req = api.sent[-1]
    assert req["type"] == mt5.ORDER_TYPE_SELL
    assert (req["price"], req["sl"], req["tp"]) == (1.10000, 1.10300, 1.09600)


def test_requote_is_retried():
    api = FakeMT5(send_codes=[mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_DONE])
    _broker(api).open("long", 0.02, 0.0030, 0.0040)
    assert len(api.sent) == 2


def test_hard_rejection_fails_without_retry():
    api = FakeMT5(send_codes=[mt5.TRADE_RETCODE_NO_MONEY])
    with pytest.raises(OrderError):
        _broker(api).open("long", 0.02, 0.0030, 0.0040)
    assert len(api.sent) == 1


def test_position_without_sl_is_closed_immediately():
    api = FakeMT5(attach_sl=False)
    with pytest.raises(OrderError, match="no SL"):
        _broker(api).open("long", 0.02, 0.0030, 0.0040)
    assert api.positions_list == []
    assert "position" in api.sent[-1]  # last request was the emergency close


def test_close_all_ignores_manual_positions():
    api = FakeMT5()
    broker = _broker(api)
    broker.open("long", 0.02, 0.0030, 0.0040)
    api.positions_list.append(NS(ticket=1, identifier=1, magic=0, symbol="EURUSD", volume=0.10,
                                 type=mt5.POSITION_TYPE_BUY, price_open=1.1, sl=1.09, tp=1.12))
    closed = broker.close_all("test")
    assert len(closed) == 1
    assert [p.magic for p in api.positions_list] == [0]  # manual trade untouched