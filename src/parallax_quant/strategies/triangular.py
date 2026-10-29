"""Triangular arbitrage: builds a directed rate graph per exchange each
scan, finds a negative-weight cycle (= profitable loop) via Bellman-Ford,
and executes all three legs concurrently with asyncio.gather. If any leg
after the first fails to fill, immediately flattens with a market order
rather than leaving the position partially hedged.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

import structlog

from parallax_quant.config import TriangularArbConfig
from parallax_quant.data.models import OrderBook
from parallax_quant.strategies.base import (
    BaseStrategy,
    EvaluatedOpportunity,
    Opportunity,
    Position,
    SignalDirection,
)
from parallax_quant.strategies.graph import RateGraph

logger = structlog.get_logger(__name__)


class OrderBookSource(Protocol):
    async def get_order_book(self, exchange: str, symbol: str) -> OrderBook: ...


class LegExecutionAdapter(Protocol):
    async def place_limit_order(self, exchange: str, symbol: str, side: str, price: float, amount: float) -> LegFill: ...
    async def place_market_order(self, exchange: str, symbol: str, side: str, amount: float) -> LegFill: ...


@dataclass
class LegFill:
    filled: bool
    amount: float
    avg_price: float


class TriangularArbStrategy(BaseStrategy):
    name = "triangular"

    def __init__(
        self,
        config: TriangularArbConfig,
        exchange: str,
        currencies: list[str],
        orderbook_source: OrderBookSource,
        execution: LegExecutionAdapter,
        capital_usd: float,
        taker_fee_pct: float,
        clock=time.time,
    ) -> None:
        self.config = config
        self.exchange = exchange
        self.currencies = currencies
        self.orderbook_source = orderbook_source
        self.execution = execution
        self.capital_usd = capital_usd
        self.taker_fee_pct = taker_fee_pct
        self.clock = clock
        self._books: dict[str, OrderBook] = {}

    async def _build_graph(self) -> RateGraph:
        graph = RateGraph()
        for a in self.currencies:
            for b in self.currencies:
                if a == b:
                    continue
                symbol = f"{a}/{b}"
                book = self._books.get(symbol)
                if book is None or not book.asks:
                    continue
                # Buying b with a consumes the ask side; rate = 1/ask (how
                # much b you get per unit of a).
                graph.add_edge(a, b, 1.0 / book.asks[0].price)
        return graph

    async def refresh_order_books(self) -> None:
        for a in self.currencies:
            for b in self.currencies:
                if a == b:
                    continue
                symbol = f"{a}/{b}"
                try:
                    self._books[symbol] = await self.orderbook_source.get_order_book(self.exchange, symbol)
                except Exception as exc:  # noqa: BLE001 - missing pair on this exchange, skip
                    logger.debug("orderbook_unavailable", exchange=self.exchange, symbol=symbol, error=str(exc))
                    continue

    async def scan(self) -> list[Opportunity]:
        graph = await self._build_graph()
        cycle = graph.find_negative_cycle()
        if cycle is None or len(cycle) < 4:  # closed 3-node cycle has 4 entries (first==last)
            return []

        multiplier = graph.cycle_profit_multiplier(cycle)
        total_fee_multiplier = (1 - self.taker_fee_pct / 100) ** (len(cycle) - 1)
        net_multiplier = multiplier * total_fee_multiplier

        if net_multiplier <= 1 + (self.config.min_profit_after_fees_pct / 100):
            return []

        return [
            Opportunity(
                strategy=self.name,
                symbol="->".join(cycle),
                direction=SignalDirection.LONG,
                raw_edge_pct=(multiplier - 1) * 100,
                metadata={"cycle": cycle, "net_multiplier": net_multiplier},
                detected_at=self.clock(),
            )
        ]

    async def evaluate(self, opportunity: Opportunity) -> EvaluatedOpportunity:
        net_multiplier = opportunity.metadata["net_multiplier"]
        net_edge_pct = (net_multiplier - 1) * 100
        notional = min(
            self.capital_usd * (self.config.max_pct_per_opportunity / 100),
            self._max_size_within_depth_limit(opportunity.metadata["cycle"]),
        )
        return EvaluatedOpportunity(
            opportunity=opportunity,
            net_edge_pct=net_edge_pct,
            fees_pct=(1 - (1 - self.taker_fee_pct / 100) ** (len(opportunity.metadata["cycle"]) - 1)) * 100,
            slippage_pct=0.0,  # bounded structurally via _max_size_within_depth_limit instead
            recommended_notional_usd=notional,
            confidence=min(1.0, net_edge_pct / self.config.min_profit_after_fees_pct) if self.config.min_profit_after_fees_pct else 1.0,
            reject_reason=None if net_edge_pct > 0 and notional > 0 else "no_size_within_depth_limit",
        )

    def _max_size_within_depth_limit(self, cycle: list[str]) -> float:
        """Cap notional so no leg consumes more than
        max_orderbook_depth_consumption_pct of top-of-book volume.

        Each leg's budget is priced in that leg's *base* currency, so it is
        converted back to the cycle's starting currency (dividing by the
        cumulative rate up to that leg) before taking the minimum —
        otherwise an ETH-denominated leg would be compared against a
        USDT-denominated one as if they were the same unit."""
        min_notional = float("inf")
        units_per_start = 1.0  # units of the current leg's base currency per 1 unit of cycle[0]
        for a, b in pairwise(cycle):
            book = self._books.get(f"{a}/{b}")
            if book is None or not book.asks:
                return 0.0
            ask = book.asks[0].price
            budget_volume = book.asks[0].volume * (self.config.max_orderbook_depth_consumption_pct / 100)
            leg_notional_in_start = budget_volume * ask / units_per_start
            min_notional = min(min_notional, leg_notional_in_start)
            units_per_start /= ask
        return 0.0 if min_notional == float("inf") else min_notional

    async def execute(self, evaluated: EvaluatedOpportunity) -> Position | None:
        if not evaluated.is_actionable:
            return None
        cycle = evaluated.opportunity.metadata["cycle"]
        notional = evaluated.recommended_notional_usd

        legs = list(pairwise(cycle))
        first_leg = legs[0]
        remaining_legs = legs[1:]

        try:
            first_fill = await asyncio.wait_for(
                self.execution.place_limit_order(self.exchange, f"{first_leg[0]}/{first_leg[1]}", "buy", 0.0, notional),
                timeout=self.config.leg_fill_timeout_ms / 1000,
            )
        except TimeoutError:
            return None  # nothing committed yet, safe to abandon

        if not first_fill.filled:
            return None

        try:
            remaining_fills = await asyncio.wait_for(
                asyncio.gather(
                    *[
                        self.execution.place_limit_order(self.exchange, f"{a}/{b}", "buy", 0.0, first_fill.amount)
                        for a, b in remaining_legs
                    ]
                ),
                timeout=self.config.leg_fill_timeout_ms / 1000,
            )
        except Exception as exc:  # noqa: BLE001 - any failure on the remaining legs must trigger the flatten below
            # Second/third leg failed — flatten the first leg immediately
            # at market rather than sit exposed to the first currency alone.
            logger.warning("triangular_leg_failed_flattening", exchange=self.exchange, cycle=cycle, error=str(exc))
            await self.execution.place_market_order(self.exchange, f"{first_leg[0]}/{first_leg[1]}", "sell", first_fill.amount)
            return None

        if not all(f.filled for f in remaining_fills):
            await self.execution.place_market_order(self.exchange, f"{first_leg[0]}/{first_leg[1]}", "sell", first_fill.amount)
            return None

        return Position(
            strategy=self.name,
            symbol=evaluated.opportunity.symbol,
            direction=SignalDirection.LONG,
            notional_usd=notional,
            entry_price=1.0,
            opened_at=self.clock(),
            metadata={"cycle": cycle},
        )

    async def manage_risk(self, position: Position) -> bool:
        # Triangular arbitrage is a closed, near-instantaneous round trip —
        # by the time execute() returns, the position is already flat.
        # This exists to satisfy BaseStrategy's interface uniformly.
        return True
