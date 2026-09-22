"""Continuous margin monitoring across every exchange holding a leveraged
position (currently: funding_rate strategy). Polls on
config.RiskConfig.margin_check_interval_seconds and raises an alert at
margin_alert_utilization_pct — deliberately separate from the strategy's
own manage_risk() so a margin problem is caught even if a strategy's loop
is stalled."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from arbitragex_engine.config import RiskConfig

logger = structlog.get_logger(__name__)


@dataclass
class MarginAlert:
    exchange: str
    symbol: str
    utilization_pct: float
    threshold_pct: float


MarginUtilizationFetcher = Callable[[str, str], Awaitable[float]]
AlertHandler = Callable[[MarginAlert], Awaitable[None]]


class MarginMonitor:
    def __init__(
        self,
        config: RiskConfig,
        tracked_positions: Callable[[], list[tuple[str, str]]],  # -> [(exchange, symbol), ...]
        fetch_utilization: MarginUtilizationFetcher,
        on_alert: AlertHandler,
    ) -> None:
        self.config = config
        self.tracked_positions = tracked_positions
        self.fetch_utilization = fetch_utilization
        self.on_alert = on_alert

    async def check_once(self) -> list[MarginAlert]:
        alerts: list[MarginAlert] = []
        for exchange, symbol in self.tracked_positions():
            try:
                utilization = await self.fetch_utilization(exchange, symbol)
            except Exception as exc:  # noqa: BLE001 - a fetch failure is itself alert-worthy
                logger.error("margin_fetch_failed", exchange=exchange, symbol=symbol, error=str(exc))
                continue
            if utilization >= self.config.margin_alert_utilization_pct:
                alert = MarginAlert(
                    exchange=exchange,
                    symbol=symbol,
                    utilization_pct=utilization,
                    threshold_pct=self.config.margin_alert_utilization_pct,
                )
                alerts.append(alert)
                await self.on_alert(alert)
        return alerts

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.config.margin_check_interval_seconds)
            except TimeoutError:
                continue
