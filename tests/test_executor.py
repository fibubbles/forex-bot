from src.executor import order_prices
from src.strategy import Signal


def test_long_order_prices():
    sig = Signal("long", sl_distance=0.0030, tp_distance=0.0040, reason="r", strategy="s")
    entry, sl, tp = order_prices(sig, bid=1.10000, ask=1.10013, digits=5)
    assert (entry, sl, tp) == (1.10013, 1.09713, 1.10413)
    assert sl < entry < tp


def test_short_order_prices():
    sig = Signal("short", sl_distance=0.0030, tp_distance=0.0040, reason="r", strategy="s")
    entry, sl, tp = order_prices(sig, bid=1.10000, ask=1.10013, digits=5)
    assert (entry, sl, tp) == (1.10000, 1.10300, 1.09600)
    assert tp < entry < sl