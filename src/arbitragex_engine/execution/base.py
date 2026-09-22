"""Execution adapter base + fee-schedule cost estimation shared by every
concrete adapter (paper/sandbox/live). Strategies depend only on the
Protocol interfaces declared in strategies/*.py — swapping paper for live
is a wiring change in agents/orchestrator.py, never a strategy-code change."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from arbitragex_engine.strategies.base import Position


@dataclass
class OrderResult:
    filled: bool
    filled_amount: float
    avg_fill_price: float
    fee_usd: float
    order_id: str | None = None
    error: str | None = None


class ExecutionAdapter(ABC):
    """Every concrete adapter must implement these. Live adapters raise
    RuntimeError from __init__ unless config.is_live_trading_authorized()
    returned True at construction time — see execution/live_cex.py."""

    @abstractmethod
    async def place_order(
        self, exchange: str, symbol: str, side: str, order_type: str, amount: float, price: float | None = None
    ) -> OrderResult: ...

    @abstractmethod
    async def close_position(self, position: Position) -> OrderResult: ...


# Representative maker/taker fee schedules (bps) for cost-netting when a
# strategy doesn't have a live per-exchange quote yet. Real per-account
# fee tiers should come from the exchange API where available — this is
# a conservative fallback, not a source of truth.
DEFAULT_TAKER_FEE_BPS: dict[str, float] = {
    "binance": 10.0,
    "okx": 10.0,
    "kraken": 26.0,
    "coinbase": 60.0,
    "kucoin": 10.0,
    "bybit": 10.0,
}


def estimate_round_trip_fee_pct(exchange_a: str, exchange_b: str) -> float:
    fee_a = DEFAULT_TAKER_FEE_BPS.get(exchange_a, 20.0)
    fee_b = DEFAULT_TAKER_FEE_BPS.get(exchange_b, 20.0)
    return (fee_a + fee_b) / 100
