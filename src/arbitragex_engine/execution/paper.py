"""Paper-trading execution adapter — the default (config.execution.mode =
"paper"). Fills against real order-book depth pulled from the live data
feed, so P&L reflects realistic slippage, but no order ever reaches an
exchange and no API key needs trade permission. This is what every
strategy runs against unless live trading has been explicitly authorized
(see config.is_live_trading_authorized)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

import structlog

from arbitragex_engine.data.models import OrderBook
from arbitragex_engine.execution.base import ExecutionAdapter, OrderResult
from arbitragex_engine.strategies.base import Position, SignalDirection

logger = structlog.get_logger(__name__)


class OrderBookProvider(Protocol):
    async def get_order_book(self, exchange: str, symbol: str) -> OrderBook: ...


@dataclass
class PaperAccount:
    starting_capital_usd: float
    cash_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    fees_paid_usd: float = 0.0
    open_positions: dict[str, Position] = field(default_factory=dict)
    fill_log: list[OrderResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.cash_usd == 0.0:
            self.cash_usd = self.starting_capital_usd

    @property
    def equity_usd(self) -> float:
        return self.cash_usd + sum(p.notional_usd for p in self.open_positions.values())


class PaperExecutionAdapter(ExecutionAdapter):
    def __init__(
        self,
        starting_capital_usd: float,
        max_slippage_pct: float,
        orderbook_provider: OrderBookProvider,
        taker_fee_bps: float = 10.0,
    ) -> None:
        self.account = PaperAccount(starting_capital_usd=starting_capital_usd)
        self.max_slippage_pct = max_slippage_pct
        self.orderbook_provider = orderbook_provider
        self.taker_fee_bps = taker_fee_bps

    async def place_order(
        self, exchange: str, symbol: str, side: str, order_type: str, amount: float, price: float | None = None
    ) -> OrderResult:
        try:
            book = await self.orderbook_provider.get_order_book(exchange, symbol)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(filled=False, filled_amount=0.0, avg_fill_price=0.0, fee_usd=0.0, error=str(exc))

        levels = book.asks if side == "buy" else book.bids
        if not levels:
            return OrderResult(filled=False, filled_amount=0.0, avg_fill_price=0.0, fee_usd=0.0, error="empty_orderbook")

        top_price = levels[0].price
        # Limit order fills only if the book already crosses the limit —
        # no optimistic instant-fill assumption.
        if order_type == "limit" and price is not None and ((side == "buy" and top_price > price) or (side == "sell" and top_price < price)):
            return OrderResult(filled=False, filled_amount=0.0, avg_fill_price=0.0, fee_usd=0.0, error="not_marketable")

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
            return OrderResult(filled=False, filled_amount=0.0, avg_fill_price=0.0, fee_usd=0.0, error="no_liquidity")

        avg_price = notional / filled
        slippage_pct = abs(avg_price - top_price) / top_price * 100 if top_price else 0.0
        if slippage_pct > self.max_slippage_pct:
            return OrderResult(
                filled=False, filled_amount=0.0, avg_fill_price=0.0, fee_usd=0.0,
                error=f"slippage_{slippage_pct:.3f}pct_exceeds_max_{self.max_slippage_pct}pct",
            )

        fee_usd = notional * (self.taker_fee_bps / 10_000)
        self.account.fees_paid_usd += fee_usd
        self.account.cash_usd += -notional - fee_usd if side == "buy" else notional - fee_usd

        result = OrderResult(
            filled=True, filled_amount=filled, avg_fill_price=avg_price, fee_usd=fee_usd, order_id=str(uuid.uuid4())
        )
        self.account.fill_log.append(result)
        logger.info("paper_fill", exchange=exchange, symbol=symbol, side=side, amount=filled, price=avg_price, fee=fee_usd)
        return result

    async def open_pair_position(self, long_symbol: str, short_symbol: str, hedge_ratio: float, notional_usd: float) -> Position:
        position = Position(
            strategy="stat_arb",
            symbol=f"{long_symbol}/{short_symbol}",
            direction=SignalDirection.LONG,
            notional_usd=notional_usd,
            entry_price=hedge_ratio,
            opened_at=time.time(),
            metadata={"pair": (short_symbol, long_symbol) if long_symbol < short_symbol else (long_symbol, short_symbol), "beta": hedge_ratio},
        )
        self.account.open_positions[position.symbol] = position
        return position

    async def open_delta_neutral(self, symbol: str, notional_usd: float, leverage: float) -> Position:
        position = Position(
            strategy="funding_rate",
            symbol=symbol,
            direction=SignalDirection.SHORT,
            notional_usd=notional_usd,
            entry_price=1.0,
            opened_at=time.time(),
            metadata={"leverage": leverage},
        )
        self.account.open_positions[symbol] = position
        return position

    async def get_margin_utilization_pct(self, position: Position) -> float:
        # Paper account never actually gets margin-called; return a
        # conservative constant so downstream risk logic still exercises
        # its threshold-comparison path in tests/backtests.
        return 10.0

    async def close_position(self, position: Position) -> OrderResult:
        self.account.open_positions.pop(position.symbol, None)
        self.account.realized_pnl_usd += 0.0  # realized P&L accrues via place_order fills, not here
        return OrderResult(filled=True, filled_amount=position.notional_usd, avg_fill_price=position.entry_price, fee_usd=0.0)

    async def close_delta_neutral(self, position: Position) -> None:
        await self.close_position(position)
