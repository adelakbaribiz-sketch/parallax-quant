"""Exercises FundingRateArbStrategy against fake Protocol implementations."""

import time

import pytest

from arbitragex_engine.config import FundingRateArbConfig
from arbitragex_engine.data.models import FundingRate
from arbitragex_engine.strategies.base import Position, SignalDirection
from arbitragex_engine.strategies.funding_rate import FundingRateArbStrategy


class FakeFundingSource:
    def __init__(self, rate_pct_per_period: float, mark_price: float = 100.0) -> None:
        self.rate = rate_pct_per_period / 100
        self.mark_price = mark_price

    async def get_funding_rate(self, exchange: str, symbol: str) -> FundingRate:
        return FundingRate(exchange=exchange, symbol=symbol, rate=self.rate, interval_hours=8.0, next_funding_timestamp=0, timestamp=0)

    async def get_spot_price(self, exchange: str, symbol: str) -> float:
        return self.mark_price

    async def get_mark_price(self, exchange: str, symbol: str) -> float:
        return self.mark_price


class FakeDeltaNeutralExecution:
    def __init__(self, margin_utilization_pct: float = 10.0) -> None:
        self.margin_utilization_pct = margin_utilization_pct
        self.opened = []

    async def open_delta_neutral(self, symbol, notional_usd, leverage) -> Position:
        pos = Position(
            strategy="funding_rate", symbol=symbol, direction=SignalDirection.SHORT,
            notional_usd=notional_usd, entry_price=1.0, opened_at=time.time(), metadata={"leverage": leverage},
        )
        self.opened.append(pos)
        return pos

    async def close_delta_neutral(self, position: Position) -> None:
        return None

    async def get_margin_utilization_pct(self, position: Position) -> float:
        return self.margin_utilization_pct


@pytest.mark.asyncio
async def test_scan_skips_negative_funding():
    source = FakeFundingSource(rate_pct_per_period=-0.01)
    strategy = FundingRateArbStrategy(
        config=FundingRateArbConfig(), exchange="binance", symbols=["BTC/USDT"],
        source=source, execution=FakeDeltaNeutralExecution(), capital_usd=100_000, spot_fee_pct=0.1, futures_fee_pct=0.1,
    )
    assert await strategy.scan() == []


@pytest.mark.asyncio
async def test_scan_finds_opportunity_on_positive_funding():
    source = FakeFundingSource(rate_pct_per_period=0.05)
    strategy = FundingRateArbStrategy(
        config=FundingRateArbConfig(), exchange="binance", symbols=["BTC/USDT"],
        source=source, execution=FakeDeltaNeutralExecution(), capital_usd=100_000, spot_fee_pct=0.1, futures_fee_pct=0.1,
    )
    opportunities = await strategy.scan()
    assert len(opportunities) == 1
    assert opportunities[0].direction == SignalDirection.SHORT


@pytest.mark.asyncio
async def test_evaluate_rejects_when_breakeven_exceeds_warning_threshold():
    # Tiny funding rate -> very long breakeven time
    source = FakeFundingSource(rate_pct_per_period=0.0001)
    strategy = FundingRateArbStrategy(
        config=FundingRateArbConfig(breakeven_warning_hours=48), exchange="binance", symbols=["BTC/USDT"],
        source=source, execution=FakeDeltaNeutralExecution(), capital_usd=100_000, spot_fee_pct=0.1, futures_fee_pct=0.1,
    )
    opportunities = await strategy.scan()
    evaluated = await strategy.evaluate(opportunities[0])
    assert evaluated.is_actionable is False
    assert "breakeven" in evaluated.reject_reason


@pytest.mark.asyncio
async def test_evaluate_accepts_healthy_funding_rate():
    source = FakeFundingSource(rate_pct_per_period=0.1)
    strategy = FundingRateArbStrategy(
        config=FundingRateArbConfig(), exchange="binance", symbols=["BTC/USDT"],
        source=source, execution=FakeDeltaNeutralExecution(), capital_usd=100_000, spot_fee_pct=0.05, futures_fee_pct=0.05,
    )
    opportunities = await strategy.scan()
    evaluated = await strategy.evaluate(opportunities[0])
    assert evaluated.is_actionable is True


@pytest.mark.asyncio
async def test_execute_opens_position_and_manage_risk_holds_when_healthy():
    source = FakeFundingSource(rate_pct_per_period=0.1, mark_price=100.0)
    execution = FakeDeltaNeutralExecution(margin_utilization_pct=10.0)
    strategy = FundingRateArbStrategy(
        config=FundingRateArbConfig(leverage_min=2, safe_margin_distance_pct=20), exchange="binance", symbols=["BTC/USDT"],
        source=source, execution=execution, capital_usd=100_000, spot_fee_pct=0.05, futures_fee_pct=0.05,
    )
    opportunities = await strategy.scan()
    evaluated = await strategy.evaluate(opportunities[0])
    position = await strategy.execute(evaluated)
    assert position is not None
    assert len(execution.opened) == 1

    should_close = await strategy.manage_risk(position)
    assert should_close is False  # mark price unchanged from entry, funding still positive, margin healthy


@pytest.mark.asyncio
async def test_manage_risk_closes_when_funding_flips_negative():
    source = FakeFundingSource(rate_pct_per_period=0.1)
    execution = FakeDeltaNeutralExecution()
    strategy = FundingRateArbStrategy(
        config=FundingRateArbConfig(), exchange="binance", symbols=["BTC/USDT"],
        source=source, execution=execution, capital_usd=100_000, spot_fee_pct=0.05, futures_fee_pct=0.05,
    )
    opportunities = await strategy.scan()
    evaluated = await strategy.evaluate(opportunities[0])
    position = await strategy.execute(evaluated)

    source.rate = -0.001  # funding flips negative after entry
    should_close = await strategy.manage_risk(position)
    assert should_close is True


@pytest.mark.asyncio
async def test_manage_risk_closes_on_unknown_position():
    source = FakeFundingSource(rate_pct_per_period=0.1)
    strategy = FundingRateArbStrategy(
        config=FundingRateArbConfig(), exchange="binance", symbols=["BTC/USDT"],
        source=source, execution=FakeDeltaNeutralExecution(), capital_usd=100_000, spot_fee_pct=0.05, futures_fee_pct=0.05,
    )
    unknown_position = Position(
        strategy="funding_rate", symbol="ETH/USDT", direction=SignalDirection.SHORT,
        notional_usd=1000, entry_price=1.0, opened_at=time.time(), metadata={},
    )
    assert await strategy.manage_risk(unknown_position) is True
