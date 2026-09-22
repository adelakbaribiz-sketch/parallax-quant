"""Scanner agent: polls every registered strategy's scan() on
config.AgentsConfig.scanner_interval_seconds and publishes raw
opportunities to the message bus. Deliberately does no filtering or
risk judgment — that's the opportunity/risk agents' job — so this stays
simple enough to run continuously without becoming a bottleneck."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict

import structlog

from arbitragex_engine.agents.message_bus import MessageBus
from arbitragex_engine.agents.topics import TOPIC_RAW_OPPORTUNITIES
from arbitragex_engine.strategies.base import BaseStrategy

logger = structlog.get_logger(__name__)


class ScannerAgent:
    def __init__(self, strategies: list[BaseStrategy], bus: MessageBus, interval_seconds: float) -> None:
        self.strategies = strategies
        self.bus = bus
        self.interval_seconds = interval_seconds

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            start = time.monotonic()
            for strategy in self.strategies:
                try:
                    opportunities = await strategy.scan()
                except Exception as exc:  # noqa: BLE001 - one strategy's bug must not kill the scanner loop
                    logger.error("strategy_scan_failed", strategy=strategy.name, error=str(exc))
                    continue
                for opp in opportunities:
                    payload = asdict(opp)
                    payload["direction"] = opp.direction.value
                    await self.bus.publish(TOPIC_RAW_OPPORTUNITIES, payload)

            elapsed = time.monotonic() - start
            remaining = max(0.0, self.interval_seconds - elapsed)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=remaining)
            except TimeoutError:
                continue
