"""Common interface every strategy implements: scan → evaluate → execute →
manage_risk. The agents/orchestrator.py pipeline calls these four methods
on every registered strategy without knowing which one it's talking to."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass(slots=True)
class Opportunity:
    """What `scan()` produces: a raw candidate, not yet risk-checked."""

    strategy: str
    symbol: str
    direction: SignalDirection
    raw_edge_pct: float  # gross edge before fees/slippage
    metadata: dict[str, Any] = field(default_factory=dict)
    detected_at: float = 0.0


@dataclass(slots=True)
class EvaluatedOpportunity:
    """What `evaluate()` produces: opportunity plus net-of-cost economics
    and a position size recommendation, ready for the risk agent."""

    opportunity: Opportunity
    net_edge_pct: float
    fees_pct: float
    slippage_pct: float
    recommended_notional_usd: float
    confidence: float  # 0-1, strategy-specific
    reject_reason: str | None = None

    @property
    def is_actionable(self) -> bool:
        return self.reject_reason is None and self.net_edge_pct > 0


@dataclass(slots=True)
class Position:
    strategy: str
    symbol: str
    direction: SignalDirection
    notional_usd: float
    entry_price: float
    opened_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """All monetary/statistical thresholds a strategy uses must come from
    its Pydantic config section (see config.py) — never a literal in this
    class or its subclasses."""

    name: str

    @abstractmethod
    async def scan(self) -> list[Opportunity]:
        """Look at current market state and return raw candidates."""

    @abstractmethod
    async def evaluate(self, opportunity: Opportunity) -> EvaluatedOpportunity:
        """Net out fees/slippage, size the position, attach a reject_reason
        if it doesn't clear the strategy's own threshold."""

    @abstractmethod
    async def execute(self, evaluated: EvaluatedOpportunity) -> Position | None:
        """Hand off to the execution layer (paper/sandbox/live adapter
        injected at construction time). Returns None if execution failed
        or was aborted mid-flight."""

    @abstractmethod
    async def manage_risk(self, position: Position) -> bool:
        """Called periodically for every open position. Returns True if
        the position should be closed now (stop-loss, time-stop, or a
        structural break in the strategy's underlying assumption)."""
