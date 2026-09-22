"""Normalized market data shapes. Every feed (CEX websocket, DEX oracle)
converts into these before anything downstream touches it, so strategies
never need to know which exchange or venue type a tick came from."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VenueKind(str, Enum):
    CEX = "cex"
    DEX = "dex"


@dataclass(slots=True, frozen=True)
class Tick:
    exchange: str
    symbol: str
    bid: float
    ask: float
    bid_volume: float
    ask_volume: float
    timestamp: float  # unix epoch seconds, NTP-corrected

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        if self.mid == 0:
            return 0.0
        return (self.ask - self.bid) / self.mid * 10_000


@dataclass(slots=True, frozen=True)
class OrderBookLevel:
    price: float
    volume: float


@dataclass(slots=True, frozen=True)
class OrderBook:
    exchange: str
    symbol: str
    bids: tuple[OrderBookLevel, ...]  # descending by price
    asks: tuple[OrderBookLevel, ...]  # ascending by price
    timestamp: float

    def depth_to_consume(self, side: str, target_pct_of_top: float) -> tuple[float, float]:
        """Average fill price and total volume consuming up to
        `target_pct_of_top` percent of the top-of-book volume on `side`.
        Used by strategies to keep slippage bounded to a configured limit."""
        levels = self.bids if side == "sell" else self.asks
        if not levels:
            return 0.0, 0.0
        budget = levels[0].volume * (target_pct_of_top / 100)
        filled, notional = 0.0, 0.0
        for level in levels:
            take = min(level.volume, budget - filled)
            if take <= 0:
                break
            filled += take
            notional += take * level.price
        avg_price = notional / filled if filled > 0 else levels[0].price
        return avg_price, filled


@dataclass(slots=True, frozen=True)
class FundingRate:
    exchange: str
    symbol: str
    rate: float  # per-interval rate, e.g. 0.0001 = 0.01% per 8h
    interval_hours: float
    next_funding_timestamp: float
    timestamp: float


@dataclass(slots=True, frozen=True)
class OpenInterest:
    exchange: str
    symbol: str
    open_interest: float
    open_interest_usd: float
    timestamp: float
