"""Fee models for the backtester. `exchange_tiered` looks up a per-exchange
taker fee (reusing execution.base's fee table); `fixed_bps` is a flat
override for quick sensitivity analysis."""

from __future__ import annotations

from typing import Protocol

from arbitragex_engine.execution.base import DEFAULT_TAKER_FEE_BPS


class FeeModel(Protocol):
    def fee_usd(self, exchange: str, notional_usd: float) -> float: ...


class ExchangeTieredFees:
    def fee_usd(self, exchange: str, notional_usd: float) -> float:
        bps = DEFAULT_TAKER_FEE_BPS.get(exchange, 20.0)
        return notional_usd * (bps / 10_000)


class FixedBpsFees:
    def __init__(self, bps: float) -> None:
        self.bps = bps

    def fee_usd(self, exchange: str, notional_usd: float) -> float:
        return notional_usd * (self.bps / 10_000)
