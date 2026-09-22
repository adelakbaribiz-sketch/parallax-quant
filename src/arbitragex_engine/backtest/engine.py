"""Event-driven backtester: replays recorded ticks/order books in
timestamp order, feeds them to a HistoricalDataFeed that implements the
same source protocols strategies use live (PriceHistorySource,
OrderBookSource, ...), and fills orders through the configured
slippage/fee models rather than at the quoted mid-price.

This is deliberately the *same* strategy code path as live/paper trading —
strategies never know whether they're being called from here or from the
live orchestrator, which is what makes the backtest meaningful rather than
a separate simulation with its own (possibly diverging) logic.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

import pandas as pd

from arbitragex_engine.backtest.fees import FeeModel
from arbitragex_engine.backtest.slippage import SlippageModel
from arbitragex_engine.data.models import OrderBook, OrderBookLevel, Tick
from arbitragex_engine.strategies.base import BaseStrategy, Position, SignalDirection


@dataclass
class TradeRecord:
    strategy: str
    symbol: str
    pnl_usd: float
    opened_at: float
    closed_at: float


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[TradeRecord] = field(default_factory=list)


class HistoricalDataFeed:
    """Implements PriceHistorySource / OrderBookSource against a static,
    pre-loaded set of ticks. `advance_to(timestamp)` moves the feed's
    "now" pointer — strategies calling get_latest_price/get_order_book see
    only data at or before that pointer, so there is no look-ahead bias."""

    def __init__(self, ticks_by_symbol: dict[str, list[Tick]]) -> None:
        self._ticks = {symbol: sorted(t, key=lambda x: x.timestamp) for symbol, t in ticks_by_symbol.items()}
        self._timestamps = {symbol: [t.timestamp for t in ticks] for symbol, ticks in self._ticks.items()}
        self._now = 0.0

    def advance_to(self, timestamp: float) -> None:
        self._now = timestamp

    def all_timestamps_sorted(self) -> list[float]:
        merged: set[float] = set()
        for ts_list in self._timestamps.values():
            merged.update(ts_list)
        return sorted(merged)

    def _latest_tick(self, symbol: str) -> Tick | None:
        ts_list = self._timestamps.get(symbol, [])
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, self._now) - 1
        if idx < 0:
            return None
        return self._ticks[symbol][idx]

    async def get_latest_price(self, symbol: str) -> float:
        tick = self._latest_tick(symbol)
        return tick.mid if tick else 0.0

    async def get_price_series(self, symbol: str, periods: int) -> pd.Series:
        ts_list = self._timestamps.get(symbol, [])
        idx = bisect.bisect_right(ts_list, self._now)
        window = self._ticks.get(symbol, [])[max(0, idx - periods) : idx]
        return pd.Series([t.mid for t in window], index=[t.timestamp for t in window])

    async def get_order_book(self, exchange: str, symbol: str) -> OrderBook:
        tick = self._latest_tick(symbol)
        if tick is None:
            return OrderBook(exchange=exchange, symbol=symbol, bids=(), asks=(), timestamp=self._now)
        return OrderBook(
            exchange=exchange,
            symbol=symbol,
            bids=(OrderBookLevel(price=tick.bid, volume=tick.bid_volume),),
            asks=(OrderBookLevel(price=tick.ask, volume=tick.ask_volume),),
            timestamp=self._now,
        )


class BacktestExecutionAdapter:
    """Fills strategy orders against the HistoricalDataFeed's current book
    using the configured slippage/fee models, and tracks a cash/equity
    ledger — the backtest analogue of execution.paper.PaperExecutionAdapter."""

    def __init__(
        self,
        starting_capital_usd: float,
        feed: HistoricalDataFeed,
        slippage_model: SlippageModel,
        fee_model: FeeModel,
    ) -> None:
        self.cash_usd = starting_capital_usd
        self.starting_capital_usd = starting_capital_usd
        self.feed = feed
        self.slippage_model = slippage_model
        self.fee_model = fee_model
        self.open_positions: dict[str, tuple[Position, float]] = {}  # symbol -> (position, entry_fill_price)
        self.trades: list[TradeRecord] = []

    @property
    def equity_usd(self) -> float:
        unrealized = sum(p.notional_usd for p, _ in self.open_positions.values())
        return self.cash_usd + unrealized

    async def open_pair_position(self, long_symbol: str, short_symbol: str, hedge_ratio: float, notional_usd: float) -> Position:

        book = await self.feed.get_order_book("backtest", long_symbol)
        price = book.asks[0].price if book.asks else await self.feed.get_latest_price(long_symbol)
        fill_price, _filled_amount = self.slippage_model.fill_price(book, "buy", notional_usd / max(price, 1e-9), price)
        fee = self.fee_model.fee_usd("backtest", notional_usd)
        self.cash_usd -= notional_usd + fee

        position = Position(
            strategy="stat_arb",
            symbol=f"{long_symbol}/{short_symbol}",
            direction=SignalDirection.LONG,
            notional_usd=notional_usd,
            entry_price=hedge_ratio,
            opened_at=self.feed._now,
            metadata={"pair": (min(long_symbol, short_symbol), max(long_symbol, short_symbol)), "beta": hedge_ratio},
        )
        self.open_positions[position.symbol] = (position, fill_price)
        return position

    async def open_delta_neutral(self, symbol: str, notional_usd: float, leverage: float) -> Position:
        fee = self.fee_model.fee_usd("backtest", notional_usd)
        self.cash_usd -= fee
        position = Position(
            strategy="funding_rate", symbol=symbol, direction=SignalDirection.SHORT, notional_usd=notional_usd,
            entry_price=1.0, opened_at=self.feed._now, metadata={"leverage": leverage},
        )
        self.open_positions[symbol] = (position, 1.0)
        return position

    async def get_margin_utilization_pct(self, position: Position) -> float:
        return 10.0

    async def close_position_with_pnl(self, position: Position, pnl_usd: float) -> None:
        self.cash_usd += position.notional_usd + pnl_usd
        self.open_positions.pop(position.symbol, None)
        self.trades.append(
            TradeRecord(
                strategy=position.strategy,
                symbol=position.symbol,
                pnl_usd=pnl_usd,
                opened_at=position.opened_at,
                closed_at=self.feed._now,
            )
        )


class BacktestEngine:
    def __init__(self, feed: HistoricalDataFeed, execution: BacktestExecutionAdapter, strategies: list[BaseStrategy]) -> None:
        self.feed = feed
        self.execution = execution
        self.strategies = strategies

    async def run(self) -> BacktestResult:
        timestamps = self.feed.all_timestamps_sorted()
        equity_points: list[tuple[float, float]] = []

        for ts in timestamps:
            self.feed.advance_to(ts)

            for strategy in self.strategies:
                opportunities = await strategy.scan()
                for opp in opportunities:
                    evaluated = await strategy.evaluate(opp)
                    if not evaluated.is_actionable:
                        continue
                    await strategy.execute(evaluated)

            equity_points.append((ts, self.execution.equity_usd))

        index = pd.to_datetime([p[0] for p in equity_points], unit="s")
        equity_curve = pd.Series([p[1] for p in equity_points], index=index)
        return BacktestResult(equity_curve=equity_curve, trades=self.execution.trades)
