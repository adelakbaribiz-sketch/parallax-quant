"""Orchestrator: builds the message bus and the four pipeline agents
(scanner -> opportunity -> risk -> execution) from AppConfig, wires them
to the registered strategies, and runs them as concurrent asyncio tasks
until a stop signal (SIGINT/SIGTERM or an injected stop_event) fires.

This does not decide paper vs. live execution — that's entirely a
function of which ExecutionAdapter each strategy was constructed with
before being handed to the orchestrator (see main.py)."""

from __future__ import annotations

import asyncio
import signal

import structlog

from arbitragex_engine.agents.execution_agent import ExecutionAgent
from arbitragex_engine.agents.message_bus import MessageBus, build_message_bus
from arbitragex_engine.agents.opportunity_agent import OpportunityAgent
from arbitragex_engine.agents.risk_agent import EquityProvider, ExposureProvider, PositionsProvider, RiskAgent
from arbitragex_engine.agents.scanner_agent import ScannerAgent
from arbitragex_engine.config import AgentsConfig, RiskConfig
from arbitragex_engine.strategies.base import BaseStrategy

logger = structlog.get_logger(__name__)


class Orchestrator:
    def __init__(
        self,
        strategies: list[BaseStrategy],
        agents_config: AgentsConfig,
        risk_config: RiskConfig,
        exposure_provider: ExposureProvider,
        equity_provider: EquityProvider,
        positions_provider: PositionsProvider,
    ) -> None:
        self.strategies = strategies
        self.strategies_by_name = {s.name: s for s in strategies}
        self.agents_config = agents_config
        self.risk_config = risk_config
        self.bus: MessageBus = build_message_bus(agents_config.message_bus)

        self.scanner = ScannerAgent(strategies, self.bus, agents_config.scanner_interval_seconds)
        self.opportunity_agent = OpportunityAgent(self.strategies_by_name, self.bus, agents_config.opportunity_min_liquidity_usd)
        self.risk_agent = RiskAgent(risk_config, self.bus, exposure_provider, equity_provider, positions_provider)
        self.execution_agent = ExecutionAgent(self.strategies_by_name, self.bus)

        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop_event.set)
            except NotImplementedError:
                pass  # signal handlers aren't available on Windows event loops — rely on stop() being called explicitly

        logger.info(
            "orchestrator_starting",
            strategies=[s.name for s in self.strategies],
            message_bus=self.agents_config.message_bus.backend,
        )

        tasks = [
            asyncio.create_task(self.scanner.run(self._stop_event), name="scanner_agent"),
            asyncio.create_task(self.opportunity_agent.run(self._stop_event), name="opportunity_agent"),
            asyncio.create_task(self.risk_agent.run(self._stop_event), name="risk_agent"),
            asyncio.create_task(self.execution_agent.run(self._stop_event), name="execution_agent"),
        ]

        try:
            await self._stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.bus.close()
            logger.info("orchestrator_stopped")

    def stop(self) -> None:
        self._stop_event.set()
