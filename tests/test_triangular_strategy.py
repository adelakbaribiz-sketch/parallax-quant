"""Exercises TriangularArbStrategy against fake order-book and execution
Protocol implementations."""

import time

import pytest

from arbitragex_engine.config import TriangularArbConfig
from arbitragex_engine.data.models import OrderBook, OrderBookLevel
from arbitragex_engine.strategies.triangular import LegFill, TriangularArbStrategy


class FakeOrderBookSource:
    def __init__(self, books: dict[str, OrderBook]) -> None:
        self.books = books

    async def get_order_book(self, exchange: str, symbol: str) -> OrderBook:
        if symbol not in self.books:
            raise KeyError(symbol)
        return self.books[symbol]


class FakeLegExecutionAdapter:
    def __init__(self, always_fill: bool = True, fail_after_n_fills: int | None = None) -> None:
        self.always_fill = always_fill
        self.fail_after_n_fills = fail_after_n_fills
        self.fill_count = 0
        self.market_orders = []

    async def place_limit_order(self, exchange, symbol, side, price, amount) -> LegFill:
        if self.fail_after_n_fills is not None and self.fill_count >= self.fail_after_n_fills:
            return LegFill(filled=False, amount=0.0, avg_price=0.0)
        if not self.always_fill:
            return LegFill(filled=False, amount=0.0, avg_price=0.0)
        self.fill_count += 1
        return LegFill(filled=True, amount=amount, avg_price=1.0)

    async def place_market_order(self, exchange, symbol, side, amount) -> LegFill:
        self.market_orders.append((symbol, side, amount))
        return LegFill(filled=True, amount=amount, avg_price=1.0)


def make_book(ask_price: float, ask_volume: float = 1000.0) -> OrderBook:
    return OrderBook(exchange="binance", symbol="x", bids=(), asks=(OrderBookLevel(price=ask_price, volume=ask_volume),), timestamp=0)


@pytest.fixture
def profitable_triangle_books():
    # USD -> EUR -> GBP -> USD with a mispriced final leg (see test_graph_triangular.py)
    return {
        "USD/EUR": make_book(ask_price=1 / 0.90),  # 1/rate convention: buying EUR with USD
        "EUR/GBP": make_book(ask_price=1 / 0.85),
        "GBP/USD": make_book(ask_price=1 / 1.40),
    }


@pytest.mark.asyncio
async def test_scan_detects_profitable_triangle(profitable_triangle_books):
    strategy = TriangularArbStrategy(
        config=TriangularArbConfig(min_profit_after_fees_pct=0.01),
        exchange="binance",
        currencies=["USD", "EUR", "GBP"],
        orderbook_source=FakeOrderBookSource(profitable_triangle_books),
        execution=FakeLegExecutionAdapter(),
        capital_usd=100_000,
        taker_fee_pct=0.01,
    )
    await strategy.refresh_order_books()
    opportunities = await strategy.scan()
    assert len(opportunities) == 1
    assert opportunities[0].metadata["net_multiplier"] > 1.0


@pytest.mark.asyncio
async def test_scan_finds_nothing_when_rates_consistent():
    consistent_books = {
        "USD/EUR": make_book(ask_price=1 / 0.90),
        "EUR/GBP": make_book(ask_price=1 / 0.85),
        "GBP/USD": make_book(ask_price=1 / (1 / (0.90 * 0.85))),  # exactly consistent
    }
    strategy = TriangularArbStrategy(
        config=TriangularArbConfig(),
        exchange="binance",
        currencies=["USD", "EUR", "GBP"],
        orderbook_source=FakeOrderBookSource(consistent_books),
        execution=FakeLegExecutionAdapter(),
        capital_usd=100_000,
        taker_fee_pct=0.1,  # fees alone push net multiplier below 1
    )
    await strategy.refresh_order_books()
    assert await strategy.scan() == []


@pytest.mark.asyncio
async def test_execute_succeeds_when_all_legs_fill(profitable_triangle_books):
    strategy = TriangularArbStrategy(
        config=TriangularArbConfig(min_profit_after_fees_pct=0.01, leg_fill_timeout_ms=2000),
        exchange="binance",
        currencies=["USD", "EUR", "GBP"],
        orderbook_source=FakeOrderBookSource(profitable_triangle_books),
        execution=FakeLegExecutionAdapter(always_fill=True),
        capital_usd=100_000,
        taker_fee_pct=0.01,
    )
    await strategy.refresh_order_books()
    opportunities = await strategy.scan()
    evaluated = await strategy.evaluate(opportunities[0])
    assert evaluated.is_actionable

    position = await strategy.execute(evaluated)
    assert position is not None


@pytest.mark.asyncio
async def test_execute_flattens_first_leg_when_later_leg_fails(profitable_triangle_books):
    # First leg fills; the two remaining legs (fired concurrently) fail —
    # execute() must flatten the first leg with a market sell rather than
    # leave the position exposed on a single currency.
    adapter = FakeLegExecutionAdapter(fail_after_n_fills=1)
    strategy = TriangularArbStrategy(
        config=TriangularArbConfig(min_profit_after_fees_pct=0.01, leg_fill_timeout_ms=2000),
        exchange="binance",
        currencies=["USD", "EUR", "GBP"],
        orderbook_source=FakeOrderBookSource(profitable_triangle_books),
        execution=adapter,
        capital_usd=100_000,
        taker_fee_pct=0.01,
    )
    await strategy.refresh_order_books()
    opportunities = await strategy.scan()
    evaluated = await strategy.evaluate(opportunities[0])

    position = await strategy.execute(evaluated)
    assert position is None
    assert len(adapter.market_orders) == 1  # first leg was flattened at market
    assert adapter.market_orders[0][1] == "sell"


@pytest.mark.asyncio
async def test_execute_returns_none_when_first_leg_never_fills(profitable_triangle_books):
    adapter = FakeLegExecutionAdapter(always_fill=False)
    strategy = TriangularArbStrategy(
        config=TriangularArbConfig(min_profit_after_fees_pct=0.01, leg_fill_timeout_ms=2000),
        exchange="binance",
        currencies=["USD", "EUR", "GBP"],
        orderbook_source=FakeOrderBookSource(profitable_triangle_books),
        execution=adapter,
        capital_usd=100_000,
        taker_fee_pct=0.01,
    )
    await strategy.refresh_order_books()
    opportunities = await strategy.scan()
    evaluated = await strategy.evaluate(opportunities[0])

    position = await strategy.execute(evaluated)
    assert position is None
    assert adapter.market_orders == []  # nothing was ever committed, so no flatten was needed


@pytest.mark.asyncio
async def test_manage_risk_always_returns_true(profitable_triangle_books):
    from arbitragex_engine.strategies.base import Position, SignalDirection

    strategy = TriangularArbStrategy(
        config=TriangularArbConfig(),
        exchange="binance",
        currencies=["USD", "EUR", "GBP"],
        orderbook_source=FakeOrderBookSource(profitable_triangle_books),
        execution=FakeLegExecutionAdapter(),
        capital_usd=100_000,
        taker_fee_pct=0.01,
    )
    position = Position(
        strategy="triangular", symbol="USD->EUR->GBP->USD", direction=SignalDirection.LONG,
        notional_usd=1000, entry_price=1.0, opened_at=time.time(), metadata={},
    )
    assert await strategy.manage_risk(position) is True
