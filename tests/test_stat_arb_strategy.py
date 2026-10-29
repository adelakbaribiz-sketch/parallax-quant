"""Exercises StatArbStrategy's scan/evaluate/execute/manage_risk against
fake in-memory implementations of its Protocol dependencies — no network,
no exchange, no database, per the Protocol-based design in strategies/base.py
that exists specifically to make this possible."""

import time

import numpy as np
import pandas as pd
import pytest

from parallax_quant.config import StatArbConfig
from parallax_quant.strategies.base import SignalDirection
from parallax_quant.strategies.stat_arb import StatArbStrategy


class FakePriceSource:
    def __init__(self, series: dict[str, pd.Series]) -> None:
        self.series = series
        self.cursor: dict[str, int] = {s: len(v) for s, v in series.items()}

    async def get_price_series(self, symbol: str, periods: int) -> pd.Series:
        s = self.series[symbol]
        c = self.cursor[symbol]
        return s.iloc[max(0, c - periods) : c]

    async def get_latest_price(self, symbol: str) -> float:
        s = self.series[symbol]
        c = self.cursor[symbol]
        return float(s.iloc[c - 1])

    def advance(self) -> None:
        for s in self.cursor:
            if self.cursor[s] < len(self.series[s]):
                self.cursor[s] += 1


class FakeExecutionAdapter:
    def __init__(self) -> None:
        self.opened = []

    async def open_pair_position(self, long_symbol, short_symbol, hedge_ratio, notional_usd):
        from parallax_quant.strategies.base import Position

        pos = Position(
            strategy="stat_arb",
            symbol=f"{long_symbol}/{short_symbol}",
            direction=SignalDirection.LONG,
            notional_usd=notional_usd,
            entry_price=hedge_ratio,
            opened_at=time.time(),
            metadata={"pair": tuple(sorted((long_symbol, short_symbol))), "beta": hedge_ratio},
        )
        self.opened.append(pos)
        return pos

    async def close_position(self, position):
        return None


@pytest.fixture
def cointegrated_series():
    rng = np.random.default_rng(77)
    n = 400
    common = np.cumsum(rng.normal(0, 1, n)) + 200
    a = pd.Series(common + rng.normal(0, 0.3, n))
    b = pd.Series(1.5 * common + rng.normal(0, 0.3, n))
    return {"A/USDT": a, "B/USDT": b}


@pytest.mark.asyncio
async def test_select_cointegrated_pairs_finds_the_pair(cointegrated_series):
    source = FakePriceSource(cointegrated_series)
    strategy = StatArbStrategy(
        config=StatArbConfig(zscore_lookback_periods=200),
        symbols=["A/USDT", "B/USDT"],
        price_source=source,
        execution=FakeExecutionAdapter(),
        capital_usd=100_000,
    )
    pairs = await strategy.select_cointegrated_pairs()
    assert ("A/USDT", "B/USDT") in pairs
    assert ("A/USDT", "B/USDT") in strategy._pairs


@pytest.mark.asyncio
async def test_scan_produces_no_opportunity_with_insufficient_history(cointegrated_series):
    source = FakePriceSource(cointegrated_series)
    strategy = StatArbStrategy(
        config=StatArbConfig(),
        symbols=["A/USDT", "B/USDT"],
        price_source=source,
        execution=FakeExecutionAdapter(),
        capital_usd=100_000,
    )
    await strategy.select_cointegrated_pairs()
    # Only one scan() call so far -> spread_history has 1 point, below the 30-point minimum
    opportunities = await strategy.scan()
    assert opportunities == []


@pytest.mark.asyncio
async def test_full_cycle_scan_evaluate_execute(cointegrated_series):
    source = FakePriceSource(cointegrated_series)
    execution = FakeExecutionAdapter()
    config = StatArbConfig(entry_zscore=0.01, zscore_lookback_periods=50)  # tiny threshold so a signal reliably fires
    strategy = StatArbStrategy(
        config=config, symbols=["A/USDT", "B/USDT"], price_source=source, execution=execution, capital_usd=100_000
    )
    await strategy.select_cointegrated_pairs()

    opportunity = None
    for _ in range(40):
        opps = await strategy.scan()
        if opps:
            opportunity = opps[0]
            break

    assert opportunity is not None
    assert opportunity.direction in (SignalDirection.LONG, SignalDirection.SHORT)

    evaluated = await strategy.evaluate(opportunity)
    assert evaluated.recommended_notional_usd <= 100_000 * (config.max_pct_per_pair / 100)

    if evaluated.is_actionable:
        position = await strategy.execute(evaluated)
        assert position is not None
        assert len(execution.opened) == 1


@pytest.mark.asyncio
async def test_manage_risk_closes_on_time_stop(cointegrated_series):
    source = FakePriceSource(cointegrated_series)
    config = StatArbConfig(time_stop_hours=0.001)  # ~3.6s — any realistic held duration triggers the time-stop
    strategy = StatArbStrategy(
        config=config, symbols=["A/USDT", "B/USDT"], price_source=source, execution=FakeExecutionAdapter(), capital_usd=100_000
    )
    await strategy.select_cointegrated_pairs()
    pair = next(iter(strategy._pairs))
    strategy._pairs[pair].spread_history = [1.0] * 40  # enough history to compute a Z-score

    from parallax_quant.strategies.base import Position

    position = Position(
        strategy="stat_arb", symbol="A/USDT/B/USDT", direction=SignalDirection.LONG,
        notional_usd=1000, entry_price=1.0, opened_at=time.time() - 3600, metadata={"pair": pair},
    )
    should_close = await strategy.manage_risk(position)
    assert should_close is True


@pytest.mark.asyncio
async def test_manage_risk_unknown_pair_closes_defensively(cointegrated_series):
    source = FakePriceSource(cointegrated_series)
    strategy = StatArbStrategy(
        config=StatArbConfig(), symbols=["A/USDT", "B/USDT"], price_source=source, execution=FakeExecutionAdapter(), capital_usd=100_000
    )
    from parallax_quant.strategies.base import Position

    position = Position(
        strategy="stat_arb", symbol="X/Y", direction=SignalDirection.LONG,
        notional_usd=1000, entry_price=1.0, opened_at=time.time(), metadata={"pair": ("X", "Y")},
    )
    assert await strategy.manage_risk(position) is True
