"""Slippage models for the backtester. `orderbook_depth` replays actual
recorded book levels (accurate but requires L2 history); `fixed_bps` is a
conservative fallback for periods/symbols without recorded depth."""

from __future__ import annotations

from typing import Protocol

from arbitragex_engine.data.models import OrderBook


class SlippageModel(Protocol):
    def fill_price(self, book: OrderBook | None, side: str, amount: float, reference_price: float) -> tuple[float, float]:
        """Returns (avg_fill_price, filled_amount)."""
        ...


class OrderBookDepthSlippage:
    def fill_price(self, book: OrderBook | None, side: str, amount: float, reference_price: float) -> tuple[float, float]:
        if book is None:
            return reference_price, amount  # no recorded depth for this bar — fall back to zero-impact fill
        levels = book.asks if side == "buy" else book.bids
        if not levels:
            return reference_price, amount
        filled, notional, remaining = 0.0, 0.0, amount
        for level in levels:
            take = min(level.volume, remaining)
            if take <= 0:
                break
            filled += take
            notional += take * level.price
            remaining -= take
            if remaining <= 0:
                break
        if filled == 0:
            return reference_price, 0.0
        return notional / filled, filled


class FixedBpsSlippage:
    def __init__(self, bps: float) -> None:
        self.bps = bps

    def fill_price(self, book: OrderBook | None, side: str, amount: float, reference_price: float) -> tuple[float, float]:
        adj = reference_price * (self.bps / 10_000)
        price = reference_price + adj if side == "buy" else reference_price - adj
        return price, amount
