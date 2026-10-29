"""Risk agent: the last gate before execution. Consumes evaluated
opportunities, checks proposed size against position_limits.py (per-asset,
per-exchange, drawdown), and only then republishes as approved. Also owns
the periodic VaR/CVaR and stress-test cadence, publishing to
topics.TOPIC_RISK_ALERTS when a limit is breached."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import structlog

from parallax_quant.agents.message_bus import MessageBus
from parallax_quant.agents.topics import (
    TOPIC_APPROVED_OPPORTUNITIES,
    TOPIC_EVALUATED_OPPORTUNITIES,
    TOPIC_REJECTED_OPPORTUNITIES,
    TOPIC_RISK_ALERTS,
)
from parallax_quant.config import RiskConfig
from parallax_quant.risk.position_limits import check_drawdown, check_position_size
from parallax_quant.risk.stress_test import run_stress_tests
from parallax_quant.strategies.base import Position

logger = structlog.get_logger(__name__)

# Callbacks the agent needs from the portfolio-state owner (the
# orchestrator) rather than holding that state itself — keeps the risk
# agent stateless and independently testable.
ExposureProvider = Callable[[], tuple[dict[str, float], dict[str, float], float]]  # asset, exchange, total_capital
EquityProvider = Callable[[], tuple[float, float]]  # current_equity, peak_equity
PositionsProvider = Callable[[], list[Position]]


class RiskAgent:
    def __init__(
        self,
        config: RiskConfig,
        bus: MessageBus,
        exposure_provider: ExposureProvider,
        equity_provider: EquityProvider,
        positions_provider: PositionsProvider,
        stress_test_interval_seconds: float = 3600.0,
    ) -> None:
        self.config = config
        self.bus = bus
        self.exposure_provider = exposure_provider
        self.equity_provider = equity_provider
        self.positions_provider = positions_provider
        self.stress_test_interval_seconds = stress_test_interval_seconds

    async def run(self, stop_event: asyncio.Event) -> None:
        queue = await self.bus.subscribe(TOPIC_EVALUATED_OPPORTUNITIES)
        stress_task = asyncio.create_task(self._run_stress_test_loop(stop_event))
        try:
            while not stop_event.is_set():
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                await self._evaluate_risk(payload)
        finally:
            stress_task.cancel()

    async def _evaluate_risk(self, payload: dict) -> None:
        opp = payload["opportunity"]
        notional = payload["recommended_notional_usd"]
        symbol = opp["symbol"]
        asset = symbol.split("/")[0] if "/" in symbol else symbol

        asset_exposure, exchange_exposure, total_capital = self.exposure_provider()
        exchange = opp.get("metadata", {}).get("exchange", "unknown")

        size_check = check_position_size(notional, asset, exchange, asset_exposure, exchange_exposure, total_capital, self.config)
        current_equity, peak_equity = self.equity_provider()
        drawdown_check = check_drawdown(current_equity, peak_equity, self.config)

        if not size_check.allowed:
            await self.bus.publish(TOPIC_REJECTED_OPPORTUNITIES, {**payload, "reject_reason": size_check.reason})
            return
        if not drawdown_check.allowed:
            await self.bus.publish(TOPIC_RISK_ALERTS, {"type": "drawdown_breach", "reason": drawdown_check.reason})
            await self.bus.publish(TOPIC_REJECTED_OPPORTUNITIES, {**payload, "reject_reason": drawdown_check.reason})
            return

        await self.bus.publish(TOPIC_APPROVED_OPPORTUNITIES, payload)

    async def _run_stress_test_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.stress_test_interval_seconds)
            except TimeoutError:
                pass
            if stop_event.is_set():
                return
            current_equity, _ = self.equity_provider()
            positions = self.positions_provider()
            results = run_stress_tests(self.config.stress_test_scenarios, positions, current_equity, self.config.portfolio_max_drawdown_pct)
            for result in results:
                if result.breaches_drawdown_limit:
                    logger.warning(
                        "stress_test_breach",
                        scenario=result.scenario,
                        simulated_pnl=result.simulated_pnl_usd,
                        surviving_equity=result.surviving_equity_usd,
                    )
                    await self.bus.publish(
                        TOPIC_RISK_ALERTS,
                        {"type": "stress_test_breach", "scenario": result.scenario, "surviving_equity_usd": result.surviving_equity_usd},
                    )
