from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from src.broker import OrderError
from src.claude_trading import (CLAUDE_MAGIC_OFFSET, MAX_ORDERS_PER_DAY, ClaudeTrader, Limits, Market,
                                validate)
from tests.test_broker import FakeMT5

WED = datetime(2026, 9, 30, 6, tzinfo=timezone.utc)
FRI_LATE = datetime(2026, 10, 2, 19, tzinfo=timezone.utc)   # 15:00 NY, inside a 3h cutoff
# XAUUSD.vxc cent: 1 lot = 1 USC per 0.01 tick, so 0.1 lot moves 10 USC per $1
M = Market(bid=4300.00, ask=4300.29, point=0.01, tick_size=0.01, tick_value=1.0, stops_level_points=0, digits=2)
LIM = Limits(lots=0.1, equity_floor=500.0, friday_hours=3)


def _v(side="buy", sl=4290.0, tp=4320.0, equity=826.0, open_positions=0, orders=0, now=WED, m=M):
    return validate(side, sl, tp, m, LIM, equity, open_positions, orders, now)


def test_valid_buy_reports_risk_in_account_currency():
    c = _v()
    assert c.ok, c.reasons
    assert c.entry == 4300.29 and c.risk == pytest.approx(102.9) and c.reward == pytest.approx(197.1)


def test_valid_sell():
    assert _v("sell", sl=4310.0, tp=4280.0).ok


@pytest.mark.parametrize("kw, reason", [
    (dict(sl=4305.0), "wrong side"),                       # buy with SL above entry
    (dict(tp=4295.0), "wrong side"),                       # buy with TP below entry
    (dict(side="sell", sl=4290.0, tp=4280.0), "wrong side"),
    (dict(open_positions=1), "one at a time"),
    (dict(equity=590.0), "floor"),                          # stop-out would leave < 500
    (dict(equity=480.0), "below the floor"),
    (dict(sl=4270.0), "% of equity"),                   # 303 USC risk on 1000 equity = 30%
    (dict(orders=MAX_ORDERS_PER_DAY), "daily limit"),
    (dict(now=FRI_LATE), "Friday"),
    (dict(m=Market(4300.0, 4300.9, 0.01, 0.01, 1.0, 0, 2)), "spread"),
    (dict(side="long"), "side must be"),
    (dict(sl="abc"), "numbers"),
])
def test_refusals(kw, reason):
    if kw.get("sl") == 4270.0:           # risk 302.9 on equity 1000 -> 30% > 25%
        kw = {**kw, "equity": 1000.0}
    c = _v(**kw)
    assert not c.ok and any(reason in r for r in c.reasons), c.reasons


def test_all_failures_are_reported_together():
    c = _v(sl=4305.0, open_positions=1, orders=MAX_ORDERS_PER_DAY)
    assert len(c.reasons) >= 3


# --- ClaudeTrader against a fake MT5 --------------------------------------------------------------
CFG = {"magic": 20260923, "symbol": "XAUUSD.vxc", "fixed_lot": 0.1, "equity_floor": 500.0,
       "friday_hours": 3, "account_login": 2511011205, "server": "ValetaxIntl-Live8"}


class GoldMT5(FakeMT5):
    TRADE_ACTION_SLTP = 6
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2

    def __init__(self, equity=826.0, login=2511011205, server="ValetaxIntl-Live8", trade_mode=2, **kw):
        super().__init__(**kw)
        self.equity, self.login, self.server, self.trade_mode, self.deals = equity, login, server, trade_mode, []

    def account_info(self):
        return NS(login=self.login, server=self.server, equity=self.equity, trade_allowed=True,
                  trade_mode=self.trade_mode)

    def terminal_info(self):
        return NS(trade_allowed=True)

    def symbol_select(self, s, v):
        return True

    def symbol_info(self, symbol):
        return NS(filling_mode=1, point=0.01, trade_tick_size=0.01, trade_tick_value=1.0,
                  trade_stops_level=0, digits=2)

    def symbol_info_tick(self, symbol):
        return NS(bid=4300.00, ask=4300.29)

    def history_deals_get(self, *a, **k):
        return tuple(self.deals)

    def order_send(self, req):
        if req["action"] == self.TRADE_ACTION_SLTP:
            self.sent.append(req)
            return NS(retcode=self.TRADE_RETCODE_DONE, comment="done")
        return super().order_send(req)


def _trader(api):
    sent = []
    return ClaudeTrader(api, CFG, notify=sent.append), sent


def test_place_uses_claude_magic_fixed_lot_and_notifies():
    api = GoldMT5()
    t, sent = _trader(api)
    msg = t.place("buy", 4290.0, 4320.0, "H1 up, pullback held")
    req = api.sent[-1]
    assert req["magic"] == CFG["magic"] + CLAUDE_MAGIC_OFFSET and req["volume"] == 0.1
    assert req["sl"] == pytest.approx(4290.0) and req["tp"] == pytest.approx(4320.0)
    assert "CLAUDE BUY" in msg and sent == [msg]


def test_place_refused_sends_nothing():
    api = GoldMT5(equity=550.0)
    t, sent = _trader(api)
    out = t.place("buy", 4290.0, 4320.0, "x")
    assert "REFUSED" in out and api.sent == [] and sent == []


def test_wrong_account_is_refused():
    t, _ = _trader(GoldMT5(login=123))
    with pytest.raises(OrderError, match="Refusing"):
        t.place("buy", 4290.0, 4320.0, "x")


def test_bot_position_blocks_claude_and_cannot_be_closed_by_claude():
    api = GoldMT5()
    api.positions_list.append(NS(ticket=5, magic=CFG["magic"], symbol="XAUUSD.vxc", volume=0.1,
                                 type=api.POSITION_TYPE_BUY, price_open=4300.0, sl=4290.0, tp=4320.0, profit=0))
    t, _ = _trader(api)
    assert "one at a time" in t.place("buy", 4290.0, 4320.0, "x")
    with pytest.raises(OrderError, match="not Claude's"):
        t.close(5, "x")


def test_daily_limit_counts_only_claude_entries():
    api = GoldMT5()
    magic = CFG["magic"] + CLAUDE_MAGIC_OFFSET
    api.deals = [NS(magic=magic, entry=0)] * MAX_ORDERS_PER_DAY + [NS(magic=CFG["magic"], entry=0)]
    t, _ = _trader(api)
    assert "daily limit" in t.place("buy", 4290.0, 4320.0, "x")


def test_modify_only_tightens_sl():
    api = GoldMT5()
    t, _ = _trader(api)
    t.place("buy", 4290.0, 4320.0, "x")
    ticket = api.positions_list[-1].ticket
    with pytest.raises(OrderError, match="reduce risk"):
        t.modify(ticket, 4280.0, None, "widen")
    with pytest.raises(OrderError, match="wrong side"):
        t.modify(ticket, 4300.5, None, "above price")
    t.modify(ticket, 4295.0, None, "lock in")
    assert api.sent[-1]["sl"] == 4295.0 and api.sent[-1]["action"] == api.TRADE_ACTION_SLTP


DEMO_CFG = {**CFG, "mode": "demo", "symbol": "XAUUSD", "equity_floor": 0.0, "account_login": None, "server": None}


def test_demo_mode_trades_a_demo_account():
    api = GoldMT5(equity=100000.0, login=5056424854, server="MetaQuotes-Demo", trade_mode=0)
    out = ClaudeTrader(api, DEMO_CFG).place("sell", 4310.0, 4280.0, "test")
    assert "CLAUDE SELL" in out and api.sent[-1]["magic"] == CFG["magic"] + CLAUDE_MAGIC_OFFSET


def test_demo_mode_refuses_a_real_account():
    with pytest.raises(OrderError, match="not a demo"):
        ClaudeTrader(GoldMT5(), DEMO_CFG).place("buy", 4290.0, 4320.0, "x")


@pytest.mark.parametrize("mode, lot", [("live", 0.2), ("demo", 1.5), ("live", None)])
def test_lot_caps_hold_even_though_the_mcp_reads_raw_yaml(mode, lot):
    with pytest.raises(OrderError, match="maximum"):
        ClaudeTrader(GoldMT5(), {**CFG, "mode": mode, "fixed_lot": lot})


def test_zero_price_is_refused():
    c = _v(m=Market(0.0, 0.0, 0.01, 0.01, 1.0, 0, 2))
    assert not c.ok and "no live price" in c.reasons[0]


def test_spread_limit_is_configurable():
    wide = Market(4300.0, 4300.61, 0.01, 0.01, 1.0, 0, 2)          # 61 points
    assert not validate("buy", 4290.0, 4320.0, wide, LIM, 826.0, 0, 0, WED).ok
    loose = Limits(LIM.lots, LIM.equity_floor, LIM.friday_hours, max_spread_points=120)
    assert validate("buy", 4290.0, 4320.0, wide, loose, 826.0, 0, 0, WED).ok


def test_risk_uses_mt5_profit_calculation_when_available():
    # e.g. GBP demo account: MT5 says 0.1 lot moves 7.40 GBP per 1.00, tick_value alone would say 10
    m = Market(4300.00, 4300.29, 0.01, 0.01, 1.0, 0, 2, value_per_price=7.4, currency="GBP")
    c = _v(m=m, sl=4290.29, equity=100000.0)
    assert c.risk == pytest.approx(74.0) and "GBP" in c.text("XAUUSD", 0.1)
    assert "ALL HARD RULES PASSED" in c.text("XAUUSD", 0.1)


def test_trader_asks_mt5_for_the_value_of_a_move():
    class CalcMT5(GoldMT5):
        def order_calc_profit(self, action, symbol, volume, open_price, close_price):
            return round((close_price - open_price) * volume * 74, 2)   # 7.40 per 1.00 at 0.1 lot
    t, _ = _trader(CalcMT5(equity=100000.0))
    c = t.check("buy", 4290.29, 4320.0)
    assert c.risk == pytest.approx(74.0)
