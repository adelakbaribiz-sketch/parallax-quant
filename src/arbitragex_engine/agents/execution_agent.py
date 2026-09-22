"""Execution agent: the only agent that calls strategy.execute() and
strategy.manage_risk(). Consumes approved opportunities, executes them,
tracks the resulting open positions, and polls manage_risk() on each
open position so exits (Z-score reversion, stop-loss, time-stop, margin
breach) happen even if nothing new is being scanned."""

from __future__ import annotations

import asyncio

import structlog

from arbitragex_engine.agents.message_bus import MessageBus
from arbitragex_engine.agents.topics import TOPIC_APPROVED_OPPORTUNITIES, TOPIC_EXECUTIONS
from arbitragex_engine.strategies.base import (
    BaseStrategy,
    EvaluatedOpportunity,
    Opportunity,
    Position,
    SignalDirection,
)

logger = structlog.get_logger(__name__)


class ExecutionAgent:
    def __init__(self, strategies_by_name: dict[str, BaseStrategy], bus: MessageBus, position_poll_interval_seconds: float = 5.0) -> None:
        self.strategies_by_name = strategies_by_name
        self.bus = bus
        self.position_poll_interval_seconds = position_poll_interval_seconds
        self.open_positions: list[Position] = []

    async def run(self, stop_event: asyncio.Event) -> None:
        queue = await self.bus.subscribe(TOPIC_APPROVED_OPPORTUNITIES)
        risk_task = asyncio.create_task(self._manage_open_positions(stop_event))
        try:
            while not stop_event.is_set():
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                await self._execute(payload)
        finally:
            risk_task.cancel()

    async def _execute(self, payload: dict) -> None:
        opp_data = payload["opportunity"]
        strategy = self.strategies_by_name.get(opp_data["strategy"])
        if strategy is None:
            return

        opportunity = Opportunity(
            strategy=opp_data["strategy"],
            symbol=opp_data["symbol"],
            direction=SignalDirection(opp_data["direction"]),
            raw_edge_pct=opp_data["raw_edge_pct"],
            metadata=opp_data.get("metadata", {}),
            detected_at=opp_data.get("detected_at", 0.0),
        )
        evaluated = EvaluatedOpportunity(
            opportunity=opportunity,
            net_edge_pct=payload["net_edge_pct"],
            fees_pct=payload["fees_pct"],
            slippage_pct=payload["slippage_pct"],
            recommended_notional_usd=payload["recommended_notional_usd"],
            confidence=payload["confidence"],
        )

        try:
            position = await strategy.execute(evaluated)
        except Exception as exc:  # noqa: BLE001
            logger.error("strategy_execute_failed", strategy=strategy.name, error=str(exc))
            return

        if position is None:
            return

        self.open_positions.append(position)
        await self.bus.publish(
            TOPIC_EXECUTIONS,
            {"strategy": position.strategy, "symbol": position.symbol, "notional_usd": position.notional_usd, "opened_at": position.opened_at},
        )
        logger.info("position_opened", strategy=position.strategy, symbol=position.symbol, notional=position.notional_usd)

    async def _manage_open_positions(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            still_open = []
            for position in self.open_positions:
                strategy = self.strategies_by_name.get(position.strategy)
                if strategy is None:
                    continue
                try:
                    should_close = await strategy.manage_risk(position)
                except Exception as exc:  # noqa: BLE001
                    logger.error("manage_risk_failed", strategy=position.strategy, symbol=position.symbol, error=str(exc))
                    should_close = False

                if should_close:
                    logger.info("position_closed", strategy=position.strategy, symbol=position.symbol)
                else:
                    still_open.append(position)
            self.open_positions = still_open

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.position_poll_interval_seconds)
            except TimeoutError:
                continue
