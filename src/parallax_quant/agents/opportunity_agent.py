"""Opportunity agent: consumes raw opportunities, calls the owning
strategy's evaluate() to net out costs and size the position, drops
anything below config.AgentsConfig.opportunity_min_liquidity_usd or that
the strategy itself rejected, and republishes the rest for the risk agent."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

import structlog

from parallax_quant.agents.message_bus import MessageBus
from parallax_quant.agents.topics import TOPIC_EVALUATED_OPPORTUNITIES, TOPIC_RAW_OPPORTUNITIES
from parallax_quant.strategies.base import BaseStrategy, Opportunity, SignalDirection

logger = structlog.get_logger(__name__)


class OpportunityAgent:
    def __init__(self, strategies_by_name: dict[str, BaseStrategy], bus: MessageBus, min_liquidity_usd: float) -> None:
        self.strategies_by_name = strategies_by_name
        self.bus = bus
        self.min_liquidity_usd = min_liquidity_usd

    async def run(self, stop_event: asyncio.Event) -> None:
        queue = await self.bus.subscribe(TOPIC_RAW_OPPORTUNITIES)
        while not stop_event.is_set():
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            strategy = self.strategies_by_name.get(payload["strategy"])
            if strategy is None:
                logger.warning("unknown_strategy_in_opportunity", strategy=payload.get("strategy"))
                continue

            opportunity = Opportunity(
                strategy=payload["strategy"],
                symbol=payload["symbol"],
                direction=SignalDirection(payload["direction"]),
                raw_edge_pct=payload["raw_edge_pct"],
                metadata=payload.get("metadata", {}),
                detected_at=payload.get("detected_at", 0.0),
            )

            try:
                evaluated = await strategy.evaluate(opportunity)
            except Exception as exc:  # noqa: BLE001
                logger.error("strategy_evaluate_failed", strategy=strategy.name, error=str(exc))
                continue

            if not evaluated.is_actionable:
                continue
            if evaluated.recommended_notional_usd < self.min_liquidity_usd:
                continue

            out = {
                "opportunity": {**asdict(evaluated.opportunity), "direction": evaluated.opportunity.direction.value},
                "net_edge_pct": evaluated.net_edge_pct,
                "fees_pct": evaluated.fees_pct,
                "slippage_pct": evaluated.slippage_pct,
                "recommended_notional_usd": evaluated.recommended_notional_usd,
                "confidence": evaluated.confidence,
            }
            await self.bus.publish(TOPIC_EVALUATED_OPPORTUNITIES, out)
