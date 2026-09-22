"""CEX market data ingestion via ccxt's unified async websocket API
(``watch_order_book`` / ``watch_funding_rate``, available in ccxt>=4.x
without a separate paid ccxt.pro key for most exchanges — check
ccxt's per-exchange feature matrix for the venues you enable).

Reconnects with jittered exponential backoff, rate-limits REST fallback
calls with a token bucket, and normalizes everything to data.models.Tick /
OrderBook / FundingRate before publishing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, Self

import ccxt.async_support as ccxt
import structlog

from arbitragex_engine.config import ExchangeConfig
from arbitragex_engine.data.models import FundingRate, OrderBook, OrderBookLevel, Tick
from arbitragex_engine.data.ratelimit import ExponentialBackoff, TokenBucket
from arbitragex_engine.data.timesync import TimeSync

logger = structlog.get_logger(__name__)


class CexFeed:
    def __init__(self, config: ExchangeConfig, timesync: TimeSync) -> None:
        self.config = config
        self.timesync = timesync
        self._exchange_cls = getattr(ccxt, config.id, None)
        if self._exchange_cls is None:
            raise ValueError(f"ccxt has no exchange named '{config.id}'")
        self.rate_limiter = TokenBucket(config.rate_limit_requests_per_second, config.rate_limit_burst)
        self._backoff = ExponentialBackoff(
            config.websocket_reconnect_backoff_base_seconds,
            config.websocket_reconnect_backoff_max_seconds,
        )
        self._client: ccxt.Exchange | None = None

    async def __aenter__(self) -> Self:
        assert self._exchange_cls is not None  # guaranteed by the __init__ check above
        self._client = self._exchange_cls(
            {
                "apiKey": self._resolve_env(self.config.api_key_env),
                "secret": self._resolve_env(self.config.api_secret_env),
                "enableRateLimit": True,
            }
        )
        if self.config.sandbox and hasattr(self._client, "set_sandbox_mode"):
            self._client.set_sandbox_mode(True)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.close()

    @staticmethod
    def _resolve_env(env_name: str | None) -> str | None:
        import os

        return os.environ.get(env_name) if env_name else None

    async def watch_order_book(self, symbol: str, depth: int, stop_event: asyncio.Event) -> AsyncIterator[OrderBook]:
        assert self._client is not None
        while not stop_event.is_set():
            try:
                raw = await self._client.watch_order_book(symbol, limit=depth)
                self._backoff.reset()
                yield self._normalize_orderbook(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport error triggers reconnect
                delay = self._backoff.next_delay()
                logger.warning(
                    "orderbook_stream_error", exchange=self.config.id, symbol=symbol, error=str(exc), retry_in_s=delay
                )
                await asyncio.sleep(delay)

    def _normalize_orderbook(self, raw: dict[str, Any]) -> OrderBook:
        bids = tuple(OrderBookLevel(price=p, volume=v) for p, v in raw.get("bids", []))
        asks = tuple(OrderBookLevel(price=p, volume=v) for p, v in raw.get("asks", []))
        return OrderBook(
            exchange=self.config.id,
            symbol=raw.get("symbol", ""),
            bids=bids,
            asks=asks,
            timestamp=self.timesync.now(),
        )

    async def fetch_tick(self, symbol: str) -> Tick:
        """REST fallback (rate-limited) for venues/plans without WS ticker
        streaming, or for a one-off cross-check against the WS feed."""
        assert self._client is not None
        await self.rate_limiter.acquire()
        ticker = await self._client.fetch_ticker(symbol)
        return Tick(
            exchange=self.config.id,
            symbol=symbol,
            bid=float(ticker["bid"] or 0.0),
            ask=float(ticker["ask"] or 0.0),
            bid_volume=float(ticker.get("bidVolume") or 0.0),
            ask_volume=float(ticker.get("askVolume") or 0.0),
            timestamp=self.timesync.now(),
        )

    async def fetch_funding_rate(self, symbol: str) -> FundingRate | None:
        assert self._client is not None
        if not self._client.has.get("fetchFundingRate"):
            return None
        await self.rate_limiter.acquire()
        raw = await self._client.fetch_funding_rate(symbol)
        return FundingRate(
            exchange=self.config.id,
            symbol=symbol,
            rate=float(raw.get("fundingRate") or 0.0),
            interval_hours=8.0,
            next_funding_timestamp=(raw.get("fundingTimestamp") or 0) / 1000,
            timestamp=self.timesync.now(),
        )


async def run_orderbook_stream(
    feed: CexFeed,
    symbol: str,
    depth: int,
    on_orderbook: Callable[[OrderBook], Any],
    stop_event: asyncio.Event,
) -> None:
    """Convenience runner: pumps normalized order books to a callback,
    used by the scanner agent to avoid every caller re-implementing the
    reconnect loop."""
    async for book in feed.watch_order_book(symbol, depth, stop_event):
        result = on_orderbook(book)
        if asyncio.iscoroutine(result):
            await result
