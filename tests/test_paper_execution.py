import pytest

from parallax_quant.data.models import OrderBook, OrderBookLevel
from parallax_quant.execution.paper import PaperExecutionAdapter


class FakeOrderBookProvider:
    def __init__(self, book: OrderBook) -> None:
        self.book = book

    async def get_order_book(self, exchange: str, symbol: str) -> OrderBook:
        return self.book


def make_book(ask_price=100.0, ask_volume=10.0, bid_price=99.5, bid_volume=10.0) -> OrderBook:
    return OrderBook(
        exchange="binance",
        symbol="BTC/USDT",
        bids=(OrderBookLevel(price=bid_price, volume=bid_volume),),
        asks=(OrderBookLevel(price=ask_price, volume=ask_volume),),
        timestamp=0.0,
    )


@pytest.mark.asyncio
async def test_market_buy_fills_at_ask_price():
    provider = FakeOrderBookProvider(make_book(ask_price=100.0, ask_volume=10.0))
    adapter = PaperExecutionAdapter(starting_capital_usd=100_000, max_slippage_pct=1.0, orderbook_provider=provider)

    result = await adapter.place_order("binance", "BTC/USDT", "buy", "market", amount=5.0)

    assert result.filled is True
    assert result.filled_amount == 5.0
    assert result.avg_fill_price == 100.0


@pytest.mark.asyncio
async def test_buy_deducts_cash_including_fee():
    provider = FakeOrderBookProvider(make_book(ask_price=100.0, ask_volume=10.0))
    adapter = PaperExecutionAdapter(starting_capital_usd=100_000, max_slippage_pct=1.0, orderbook_provider=provider, taker_fee_bps=10.0)

    await adapter.place_order("binance", "BTC/USDT", "buy", "market", amount=10.0)

    # notional = 1000, fee = 1000 * 0.001 = 1.0
    assert adapter.account.cash_usd == pytest.approx(100_000 - 1000 - 1.0)
    assert adapter.account.fees_paid_usd == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_order_exceeding_book_depth_partially_fills():
    provider = FakeOrderBookProvider(make_book(ask_price=100.0, ask_volume=5.0))
    adapter = PaperExecutionAdapter(starting_capital_usd=100_000, max_slippage_pct=100.0, orderbook_provider=provider)

    result = await adapter.place_order("binance", "BTC/USDT", "buy", "market", amount=20.0)

    assert result.filled is True
    assert result.filled_amount == 5.0  # capped at available book depth


@pytest.mark.asyncio
async def test_empty_orderbook_does_not_fill():
    empty_book = OrderBook(exchange="binance", symbol="BTC/USDT", bids=(), asks=(), timestamp=0.0)
    provider = FakeOrderBookProvider(empty_book)
    adapter = PaperExecutionAdapter(starting_capital_usd=100_000, max_slippage_pct=1.0, orderbook_provider=provider)

    result = await adapter.place_order("binance", "BTC/USDT", "buy", "market", amount=1.0)
    assert result.filled is False
    assert result.error == "empty_orderbook"


@pytest.mark.asyncio
async def test_limit_order_not_marketable_does_not_fill():
    provider = FakeOrderBookProvider(make_book(ask_price=100.0))
    adapter = PaperExecutionAdapter(starting_capital_usd=100_000, max_slippage_pct=1.0, orderbook_provider=provider)

    # Limit buy at 95 when ask is 100 -> not marketable
    result = await adapter.place_order("binance", "BTC/USDT", "buy", "limit", amount=1.0, price=95.0)
    assert result.filled is False
    assert result.error == "not_marketable"


@pytest.mark.asyncio
async def test_sell_credits_cash():
    provider = FakeOrderBookProvider(make_book(bid_price=99.0, bid_volume=10.0))
    adapter = PaperExecutionAdapter(starting_capital_usd=0, max_slippage_pct=1.0, orderbook_provider=provider, taker_fee_bps=0.0)

    result = await adapter.place_order("binance", "BTC/USDT", "sell", "market", amount=2.0)
    assert result.filled is True
    assert adapter.account.cash_usd == pytest.approx(198.0)
