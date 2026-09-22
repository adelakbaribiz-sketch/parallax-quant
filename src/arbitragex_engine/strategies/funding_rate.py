"""Funding-rate arbitrage: spot-long + perpetual-short delta-neutral
position that collects the funding rate when it's positive (shorts get
paid). All formulas per the spec:

    R = sum(f_funding * L * V) - (fees_spot + fees_futures + slippage)
    T_breakeven = (fees + slippage) / avg(funding_rate_per_period)

Liquidation price uses a standard isolated-margin approximation
(initial margin rate - maintenance margin rate applied to entry price);
exact liquidation math is exchange-specific and should be cross-checked
against the venue's own API in any live deployment — see
docs/LIMITATIONS.md.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from arbitragex_engine.config import FundingRateArbConfig
from arbitragex_engine.data.models import FundingRate
from arbitragex_engine.strategies.base import (
    BaseStrategy,
    EvaluatedOpportunity,
    Opportunity,
    Position,
    SignalDirection,
)


class FundingRateSource(Protocol):
    async def get_funding_rate(self, exchange: str, symbol: str) -> FundingRate: ...
    async def get_spot_price(self, exchange: str, symbol: str) -> float: ...
    async def get_mark_price(self, exchange: str, symbol: str) -> float: ...


class DeltaNeutralExecutionAdapter(Protocol):
    async def open_delta_neutral(
        self, symbol: str, notional_usd: float, leverage: float
    ) -> Position: ...
    async def close_delta_neutral(self, position: Position) -> None: ...
    async def get_margin_utilization_pct(self, position: Position) -> float: ...


def liquidation_price_short(entry_price: float, leverage: float, maintenance_margin_rate: float) -> float:
    """Isolated-margin short liquidation price approximation:
    liq = entry * (1 + 1/leverage - maintenance_margin_rate)."""
    initial_margin_rate = 1.0 / leverage
    return entry_price * (1 + initial_margin_rate - maintenance_margin_rate)


def safe_margin_distance_pct(entry_price: float, mark_price: float, liq_price: float) -> float:
    """% distance the current mark price has moved toward liquidation,
    from the entry side — 0% = at entry, 100% = at liquidation."""
    total_range = abs(liq_price - entry_price)
    if total_range == 0:
        return 0.0
    moved = abs(mark_price - entry_price)
    return min(100.0, (moved / total_range) * 100)


def breakeven_hours(total_fees_pct: float, slippage_pct: float, avg_funding_rate_pct_per_period: float, interval_hours: float) -> float:
    if avg_funding_rate_pct_per_period <= 0:
        return float("inf")
    periods_needed = (total_fees_pct + slippage_pct) / avg_funding_rate_pct_per_period
    return periods_needed * interval_hours


def expected_funding_income(
    funding_rate_pct_per_period: float, leverage: float, notional_usd: float, periods: int
) -> float:
    """R (gross, before costs) = sum over periods of (f_funding * L * V)."""
    return funding_rate_pct_per_period / 100 * leverage * notional_usd * periods


@dataclass
class _FundingPosition:
    symbol: str
    position: Position
    entry_price: float
    leverage: float
    maintenance_margin_rate: float
    funding_rate_history: list[float]


class FundingRateArbStrategy(BaseStrategy):
    name = "funding_rate"

    def __init__(
        self,
        config: FundingRateArbConfig,
        exchange: str,
        symbols: list[str],
        source: FundingRateSource,
        execution: DeltaNeutralExecutionAdapter,
        capital_usd: float,
        spot_fee_pct: float,
        futures_fee_pct: float,
        maintenance_margin_rate: float = 0.005,
        clock=time.time,
    ) -> None:
        self.config = config
        self.exchange = exchange
        self.symbols = symbols
        self.source = source
        self.execution = execution
        self.capital_usd = capital_usd
        self.spot_fee_pct = spot_fee_pct
        self.futures_fee_pct = futures_fee_pct
        self.maintenance_margin_rate = maintenance_margin_rate
        self.clock = clock
        self._positions: dict[str, _FundingPosition] = {}

    async def scan(self) -> list[Opportunity]:
        opportunities = []
        for symbol in self.symbols:
            if symbol in self._positions:
                continue
            funding = await self.source.get_funding_rate(self.exchange, symbol)
            if funding.rate <= 0:
                continue  # only collect funding when shorts get paid
            opportunities.append(
                Opportunity(
                    strategy=self.name,
                    symbol=symbol,
                    direction=SignalDirection.SHORT,  # short the perp leg
                    raw_edge_pct=funding.rate * 100,
                    metadata={"funding_rate_pct": funding.rate * 100, "interval_hours": funding.interval_hours},
                    detected_at=self.clock(),
                )
            )
        return opportunities

    async def evaluate(self, opportunity: Opportunity) -> EvaluatedOpportunity:
        funding_pct = opportunity.metadata["funding_rate_pct"]
        interval_hours = opportunity.metadata["interval_hours"]
        total_fees_pct = self.spot_fee_pct + self.futures_fee_pct
        slippage_pct = 0.1  # conservative flat estimate; refined by execution layer at fill time

        notional = min(
            self.capital_usd * (self.config.allocation_pct_max / 100),
            self.capital_usd,  # never over-allocate past total capital regardless of config
        )

        be_hours = breakeven_hours(total_fees_pct, slippage_pct, funding_pct, interval_hours)
        net_edge_pct = funding_pct - (total_fees_pct + slippage_pct) / max(1, (24 / interval_hours))

        reject_reason = None
        if be_hours > self.config.breakeven_warning_hours:
            reject_reason = f"breakeven_{be_hours:.1f}h_exceeds_warning_threshold_{self.config.breakeven_warning_hours}h"
        elif net_edge_pct <= 0:
            reject_reason = "net_edge_non_positive"

        return EvaluatedOpportunity(
            opportunity=opportunity,
            net_edge_pct=net_edge_pct,
            fees_pct=total_fees_pct,
            slippage_pct=slippage_pct,
            recommended_notional_usd=notional,
            confidence=min(1.0, funding_pct / 0.05) if funding_pct > 0 else 0.0,
            reject_reason=reject_reason,
        )

    async def execute(self, evaluated: EvaluatedOpportunity) -> Position | None:
        if not evaluated.is_actionable:
            return None
        symbol = evaluated.opportunity.symbol
        leverage = self.config.leverage_min
        position = await self.execution.open_delta_neutral(symbol, evaluated.recommended_notional_usd, leverage)

        entry_price = await self.source.get_mark_price(self.exchange, symbol)
        self._positions[symbol] = _FundingPosition(
            symbol=symbol,
            position=position,
            entry_price=entry_price,
            leverage=leverage,
            maintenance_margin_rate=self.maintenance_margin_rate,
            funding_rate_history=[evaluated.opportunity.metadata["funding_rate_pct"]],
        )
        return position

    async def manage_risk(self, position: Position) -> bool:
        state = self._positions.get(position.symbol)
        if state is None:
            return True

        mark_price = await self.source.get_mark_price(self.exchange, position.symbol)
        liq_price = liquidation_price_short(state.entry_price, state.leverage, state.maintenance_margin_rate)
        distance_pct = safe_margin_distance_pct(state.entry_price, mark_price, liq_price)

        if distance_pct >= (100 - self.config.safe_margin_distance_pct):
            return True  # inside the unsafe margin zone — close now

        funding = await self.source.get_funding_rate(self.exchange, position.symbol)
        state.funding_rate_history.append(funding.rate * 100)
        if funding.rate <= 0:
            return True  # funding flipped negative — thesis no longer holds

        margin_util = await self.execution.get_margin_utilization_pct(position)
        return margin_util >= self.config.safe_margin_distance_pct
