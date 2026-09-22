"""DEX-CEX flash-loan arbitrage: borrow via Aave/dYdX, buy on the cheaper
venue, sell on the other, repay the loan, keep the difference — all
atomic within one transaction (or reverts, so there's no partial-fill risk
the way there is for the CEX-only strategies).

    profit = (P_cex - P_dex) * amount - gas - flashloan_fee

Submitted via Flashbots Protect (private mempool) when configured, so the
arbitrage transaction itself isn't visible to other searchers/MEV bots
before inclusion — this protects *this* strategy from being front-run, and
is a materially different use of MEV infrastructure from the sandwich
strategy this build deliberately does not implement (see docs/LIMITATIONS.md).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar, Protocol

from arbitragex_engine.config import DexCexFlashLoanConfig
from arbitragex_engine.strategies.base import (
    BaseStrategy,
    EvaluatedOpportunity,
    Opportunity,
    Position,
    SignalDirection,
)


class PriceSource(Protocol):
    async def get_cex_price(self, exchange: str, symbol: str) -> float: ...
    async def get_dex_price(self, pool_id: str, symbol: str) -> float: ...


class GasEstimator(Protocol):
    async def estimate_gas_units(self, protocol: str) -> int: ...
    async def current_base_fee_gwei(self) -> float: ...


class FlashLoanExecutionAdapter(Protocol):
    async def submit_flashloan_bundle(
        self,
        protocol: str,
        pool_id: str,
        cex_exchange: str,
        symbol: str,
        amount: float,
        direction: str,
        max_gas_price_gwei: float,
        priority_fee_gwei: float,
        use_flashbots: bool,
    ) -> FlashLoanResult: ...


@dataclass
class FlashLoanResult:
    success: bool
    tx_hash: str | None
    realized_profit_usd: float
    gas_used_usd: float


def flashloan_fee_usd(amount_usd: float, protocol_fee_bps: float) -> float:
    return amount_usd * (protocol_fee_bps / 10_000)


def estimate_gas_cost_usd(gas_units: int, base_fee_gwei: float, priority_fee_gwei: float, eth_price_usd: float) -> float:
    total_gwei_per_gas = base_fee_gwei + priority_fee_gwei
    total_eth = (gas_units * total_gwei_per_gas) / 1e9
    return total_eth * eth_price_usd


def expected_profit_usd(
    cex_price: float, dex_price: float, amount: float, gas_cost_usd: float, flashloan_fee_usd_: float
) -> float:
    """profit = (P_cex - P_dex) * amount - gas - flashloan_fee, using the
    absolute spread regardless of direction (buy the cheap side, sell the
    expensive side — the caller determines direction from the sign)."""
    gross = abs(cex_price - dex_price) * amount
    return gross - gas_cost_usd - flashloan_fee_usd_


class DexCexFlashLoanStrategy(BaseStrategy):
    name = "dex_cex_flashloan"

    # Aave v3 flash loan fee is 0.05% (5 bps) at the time of writing —
    # configurable per protocol since fee schedules change.
    PROTOCOL_FEE_BPS: ClassVar[dict[str, float]] = {"aave_v3": 5.0, "dydx": 0.0}

    def __init__(
        self,
        config: DexCexFlashLoanConfig,
        cex_exchange: str,
        pool_id: str,
        symbol: str,
        price_source: PriceSource,
        gas_estimator: GasEstimator,
        execution: FlashLoanExecutionAdapter,
        capital_usd: float,
        eth_price_usd_source,  # async callable -> float
        clock=time.time,
    ) -> None:
        self.config = config
        self.cex_exchange = cex_exchange
        self.pool_id = pool_id
        self.symbol = symbol
        self.price_source = price_source
        self.gas_estimator = gas_estimator
        self.execution = execution
        self.capital_usd = capital_usd
        self.eth_price_usd_source = eth_price_usd_source
        self.clock = clock

    async def scan(self) -> list[Opportunity]:
        cex_price = await self.price_source.get_cex_price(self.cex_exchange, self.symbol)
        dex_price = await self.price_source.get_dex_price(self.pool_id, self.symbol)
        if cex_price <= 0 or dex_price <= 0:
            return []

        spread_pct = abs(cex_price - dex_price) / min(cex_price, dex_price) * 100
        if spread_pct <= 0:
            return []

        direction = SignalDirection.LONG if dex_price < cex_price else SignalDirection.SHORT
        return [
            Opportunity(
                strategy=self.name,
                symbol=self.symbol,
                direction=direction,
                raw_edge_pct=spread_pct,
                metadata={"cex_price": cex_price, "dex_price": dex_price},
                detected_at=self.clock(),
            )
        ]

    async def evaluate(self, opportunity: Opportunity) -> EvaluatedOpportunity:
        cex_price = opportunity.metadata["cex_price"]
        dex_price = opportunity.metadata["dex_price"]

        notional = min(self.capital_usd * (self.config.allocation_pct_max / 100), self.capital_usd)
        amount = notional / dex_price if dex_price > 0 else 0.0

        protocol = self.config.protocols[0] if self.config.protocols else "aave_v3"
        fee_bps = self.PROTOCOL_FEE_BPS.get(protocol, 5.0)
        loan_fee = flashloan_fee_usd(notional, fee_bps)

        gas_units = await self.gas_estimator.estimate_gas_units(protocol)
        base_fee = await self.gas_estimator.current_base_fee_gwei()
        eth_price = await self.eth_price_usd_source()
        gas_cost = estimate_gas_cost_usd(gas_units, base_fee, self.config.eip1559_priority_fee_gwei, eth_price)
        gas_cost *= 1 + (self.config.gas_estimate_safety_margin_pct / 100)

        profit = expected_profit_usd(cex_price, dex_price, amount, gas_cost, loan_fee)
        net_edge_pct = (profit / notional * 100) if notional > 0 else 0.0

        reject_reason = None
        if base_fee > self.config.max_gas_price_gwei:
            reject_reason = f"gas_price_{base_fee:.1f}gwei_exceeds_max_{self.config.max_gas_price_gwei}gwei"
        elif profit < self.config.min_profit_after_gas_and_fees_usd:
            reject_reason = "profit_below_minimum_after_gas_and_fees"

        return EvaluatedOpportunity(
            opportunity=opportunity,
            net_edge_pct=net_edge_pct,
            fees_pct=fee_bps / 100,
            slippage_pct=0.0,  # atomic tx either fills at simulated price or reverts entirely
            recommended_notional_usd=notional,
            confidence=min(1.0, profit / max(1.0, self.config.min_profit_after_gas_and_fees_usd * 3)),
            reject_reason=reject_reason,
        )

    async def execute(self, evaluated: EvaluatedOpportunity) -> Position | None:
        if not evaluated.is_actionable:
            return None
        protocol = self.config.protocols[0] if self.config.protocols else "aave_v3"
        result = await self.execution.submit_flashloan_bundle(
            protocol=protocol,
            pool_id=self.pool_id,
            cex_exchange=self.cex_exchange,
            symbol=self.symbol,
            amount=evaluated.recommended_notional_usd,
            direction=evaluated.opportunity.direction.value,
            max_gas_price_gwei=self.config.max_gas_price_gwei,
            priority_fee_gwei=self.config.eip1559_priority_fee_gwei,
            use_flashbots=self.config.use_flashbots_protect,
        )
        if not result.success:
            return None
        return Position(
            strategy=self.name,
            symbol=self.symbol,
            direction=evaluated.opportunity.direction,
            notional_usd=evaluated.recommended_notional_usd,
            entry_price=evaluated.opportunity.metadata["dex_price"],
            opened_at=self.clock(),
            metadata={"tx_hash": result.tx_hash, "realized_profit_usd": result.realized_profit_usd},
        )

    async def manage_risk(self, position: Position) -> bool:
        # Flash-loan arbitrage is atomic — the position (if any) is already
        # closed by the time execute() returns (profit taken or the whole
        # transaction reverted). Nothing to manage post-fill.
        return True
