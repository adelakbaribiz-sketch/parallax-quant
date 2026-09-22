"""Statistical arbitrage: cointegrated pairs traded on a Kalman-filtered
hedge ratio and a rolling Z-score, per config.StatArbConfig.

Pair selection: Engle-Granger *and* Johansen must both confirm
cointegration at the configured confidence — using both is a deliberate
belt-and-braces choice (Engle-Granger is simple but sensitive to which
series is the regressor; Johansen doesn't have that asymmetry but needs
more data to be reliable) rather than picking one arbitrarily.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from arbitragex_engine.config import StatArbConfig
from arbitragex_engine.strategies.base import (
    BaseStrategy,
    EvaluatedOpportunity,
    Opportunity,
    Position,
    SignalDirection,
)
from arbitragex_engine.strategies.cointegration import chow_test, engle_granger_test, johansen_test
from arbitragex_engine.strategies.kalman import KalmanHedgeRatio


class PriceHistorySource(Protocol):
    async def get_price_series(self, symbol: str, periods: int) -> pd.Series: ...
    async def get_latest_price(self, symbol: str) -> float: ...


class ExecutionAdapter(Protocol):
    async def open_pair_position(
        self, long_symbol: str, short_symbol: str, hedge_ratio: float, notional_usd: float
    ) -> Position: ...
    async def close_position(self, position: Position) -> None: ...


@dataclass
class _PairState:
    symbol_a: str
    symbol_b: str
    hedge_ratio_filter: KalmanHedgeRatio
    spread_history: list[float] = field(default_factory=list)
    last_chow_check: float = 0.0
    position: Position | None = None


class StatArbStrategy(BaseStrategy):
    name = "stat_arb"

    def __init__(
        self,
        config: StatArbConfig,
        symbols: list[str],
        price_source: PriceHistorySource,
        execution: ExecutionAdapter,
        capital_usd: float,
        clock=time.time,
    ) -> None:
        self.config = config
        self.symbols = symbols
        self.price_source = price_source
        self.execution = execution
        self.capital_usd = capital_usd
        self.clock = clock
        self._pairs: dict[tuple[str, str], _PairState] = {}

    def candidate_pairs(self) -> list[tuple[str, str]]:
        return list(itertools.combinations(sorted(self.symbols), 2))

    async def select_cointegrated_pairs(self) -> list[tuple[str, str]]:
        """Run both cointegration tests on every candidate pair; called
        periodically (not on every scan) since it's the expensive step."""
        selected: list[tuple[str, str]] = []
        for a, b in self.candidate_pairs():
            series_a = await self.price_source.get_price_series(a, self.config.zscore_lookback_periods)
            series_b = await self.price_source.get_price_series(b, self.config.zscore_lookback_periods)
            if len(series_a) < 30 or len(series_b) < 30:
                continue

            eg = engle_granger_test(series_a, series_b, self.config.engle_granger_max_lag, self.config.lag_selection_criterion)
            jh = johansen_test(pd.concat([series_a, series_b], axis=1), self.config.cointegration_confidence)

            if eg.is_cointegrated and jh.is_cointegrated:
                selected.append((a, b))
                if (a, b) not in self._pairs:
                    self._pairs[(a, b)] = _PairState(
                        symbol_a=a,
                        symbol_b=b,
                        hedge_ratio_filter=KalmanHedgeRatio(
                            process_variance_q=self.config.kalman_process_variance_q,
                            observation_variance_r=self.config.kalman_observation_variance_r,
                        ),
                    )
        if len(selected) < self.config.min_uncorrelated_pairs:
            import structlog

            structlog.get_logger(__name__).warning(
                "insufficient_cointegrated_pairs",
                found=len(selected),
                required=self.config.min_uncorrelated_pairs,
            )
        return selected

    async def scan(self) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        for (a, b), state in self._pairs.items():
            if state.position is not None:
                continue  # already in a position on this pair

            price_a = await self.price_source.get_latest_price(a)
            price_b = await self.price_source.get_latest_price(b)
            beta = state.hedge_ratio_filter.update(price_a, price_b)
            spread = state.hedge_ratio_filter.spread(price_a, price_b)
            state.spread_history.append(spread)
            if len(state.spread_history) > self.config.zscore_lookback_periods:
                state.spread_history.pop(0)
            if len(state.spread_history) < 30:
                continue

            z = _zscore(state.spread_history)
            if z >= self.config.entry_zscore:
                # Spread too high: short b, long a*beta (short the spread)
                opportunities.append(
                    Opportunity(
                        strategy=self.name,
                        symbol=f"{a}/{b}",
                        direction=SignalDirection.SHORT,
                        raw_edge_pct=abs(z) * 0.1,  # heuristic magnitude, refined in evaluate()
                        metadata={"pair": (a, b), "beta": beta, "zscore": z},
                        detected_at=self.clock(),
                    )
                )
            elif z <= -self.config.entry_zscore:
                opportunities.append(
                    Opportunity(
                        strategy=self.name,
                        symbol=f"{a}/{b}",
                        direction=SignalDirection.LONG,
                        raw_edge_pct=abs(z) * 0.1,
                        metadata={"pair": (a, b), "beta": beta, "zscore": z},
                        detected_at=self.clock(),
                    )
                )
        return opportunities

    async def evaluate(self, opportunity: Opportunity) -> EvaluatedOpportunity:
        z = opportunity.metadata["zscore"]
        expected_reversion = abs(z) - self.config.exit_zscore
        # Net edge approximated as expected Z-score reversion scaled to a
        # rough pct-of-spread move; fee/slippage netting happens in
        # execution.base.ExecutionAdapter.estimate_costs for the real
        # per-venue fee schedule — this keeps the strategy venue-agnostic.
        gross_edge_pct = max(0.0, expected_reversion) * 0.1
        fees_pct = 0.2  # placeholder aggregate; execution layer applies real fee schedule
        slippage_pct = 0.05
        net_edge_pct = gross_edge_pct - fees_pct - slippage_pct

        max_notional = self.capital_usd * (self.config.max_pct_per_pair / 100)
        confidence = min(1.0, abs(z) / (self.config.entry_zscore * 2))

        return EvaluatedOpportunity(
            opportunity=opportunity,
            net_edge_pct=net_edge_pct,
            fees_pct=fees_pct,
            slippage_pct=slippage_pct,
            recommended_notional_usd=max_notional,
            confidence=confidence,
            reject_reason=None if net_edge_pct > 0 else "net_edge_non_positive_after_costs",
        )

    async def execute(self, evaluated: EvaluatedOpportunity) -> Position | None:
        if not evaluated.is_actionable:
            return None
        a, b = evaluated.opportunity.metadata["pair"]
        beta = evaluated.opportunity.metadata["beta"]
        state = self._pairs[(a, b)]

        if evaluated.opportunity.direction == SignalDirection.SHORT:
            position = await self.execution.open_pair_position(
                long_symbol=b, short_symbol=a, hedge_ratio=beta, notional_usd=evaluated.recommended_notional_usd
            )
        else:
            position = await self.execution.open_pair_position(
                long_symbol=a, short_symbol=b, hedge_ratio=beta, notional_usd=evaluated.recommended_notional_usd
            )
        state.position = position
        return position

    async def manage_risk(self, position: Position) -> bool:
        pair = position.metadata.get("pair")
        if pair is None or pair not in self._pairs:
            return True  # unknown pair state — close defensively
        state = self._pairs[pair]

        if not state.spread_history:
            return False
        z = _zscore(state.spread_history)

        held_hours = (self.clock() - position.opened_at) / 3600
        if held_hours >= self.config.time_stop_hours:
            return True
        if abs(z) <= self.config.exit_zscore:
            return True
        if abs(z) >= self.config.stop_loss_sigma:
            return True

        # Periodic structural-break check via Chow test
        if self.clock() - state.last_chow_check >= self.config.chow_test_recheck_interval_hours * 3600:
            state.last_chow_check = self.clock()
            if len(state.spread_history) >= 30:
                mid = len(state.spread_history) // 2
                _, p_value = chow_test(pd.Series(state.spread_history), mid)
                if p_value < self.config.chow_test_p_value_threshold:
                    return True  # cointegration relationship likely broken
        return False


def _zscore(series: list[float]) -> float:
    s = pd.Series(series)
    std = s.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return float((s.iloc[-1] - s.mean()) / std)
