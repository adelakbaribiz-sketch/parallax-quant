import pytest

from parallax_quant.data.models import OrderBook, OrderBookLevel, Tick


def test_tick_mid_and_spread_bps():
    tick = Tick(exchange="binance", symbol="BTC/USDT", bid=100.0, ask=100.2, bid_volume=1, ask_volume=1, timestamp=0)
    assert tick.mid == pytest.approx(100.1)
    assert tick.spread_bps == pytest.approx((0.2 / 100.1) * 10_000, rel=1e-9)


def test_tick_zero_mid_does_not_divide_by_zero():
    tick = Tick(exchange="x", symbol="y", bid=0.0, ask=0.0, bid_volume=0, ask_volume=0, timestamp=0)
    assert tick.spread_bps == 0.0


def test_depth_to_consume_single_level():
    book = OrderBook(
        exchange="binance",
        symbol="BTC/USDT",
        bids=(),
        asks=(OrderBookLevel(price=100.0, volume=10.0),),
        timestamp=0,
    )
    avg_price, filled = book.depth_to_consume("buy", target_pct_of_top=50)
    assert filled == 5.0
    assert avg_price == 100.0


def test_depth_to_consume_across_multiple_levels():
    book = OrderBook(
        exchange="binance",
        symbol="BTC/USDT",
        bids=(),
        asks=(
            OrderBookLevel(price=100.0, volume=2.0),
            OrderBookLevel(price=101.0, volume=10.0),
        ),
        timestamp=0,
    )
    # target = 50% of top level (2.0) = 1.0, entirely within first level
    avg_price, filled = book.depth_to_consume("buy", target_pct_of_top=50)
    assert filled == 1.0
    assert avg_price == 100.0


def test_depth_to_consume_empty_book():
    book = OrderBook(exchange="x", symbol="y", bids=(), asks=(), timestamp=0)
    avg_price, filled = book.depth_to_consume("buy", target_pct_of_top=50)
    assert (avg_price, filled) == (0.0, 0.0)
