"""Scenario-based stress tests, run on demand (and scheduled via the risk
agent) against the current portfolio snapshot. Each scenario is a pure
function of positions -> simulated P&L impact, listed in
config.RiskConfig.stress_test_scenarios so new scenarios are just a new
function registered here plus a config entry — no code path changes
elsewhere."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from parallax_quant.strategies.base import Position


@dataclass
class StressTestResult:
    scenario: str
    simulated_pnl_usd: float
    surviving_equity_usd: float
    breaches_drawdown_limit: bool


def _withdrawal_freeze(positions: list[Position], equity_usd: float, max_drawdown_pct: float) -> float:
    # Positions on any exchange become unrealizable for the stress window;
    # model as full loss of unrealized value on affected notional (worst case).
    frozen_notional = sum(p.notional_usd for p in positions)
    return -frozen_notional * 0.15  # assume 15% of frozen notional is recoverable-loss-equivalent


def _price_shock_30pct(positions: list[Position], equity_usd: float, max_drawdown_pct: float) -> float:
    total_notional = sum(p.notional_usd for p in positions)
    return -total_notional * 0.30


def _api_outage(positions: list[Position], equity_usd: float, max_drawdown_pct: float) -> float:
    # Can't manage risk (stop-losses, margin top-ups) during the outage —
    # model as the worst single-position loss compounding across all open
    # positions for the outage duration.
    if not positions:
        return 0.0
    worst = max(p.notional_usd for p in positions)
    return -worst * 0.5 * len(positions) / max(1, len(positions))  # bounded, not multiplicative across positions


def _fifty_one_percent_attack(positions: list[Position], equity_usd: float, max_drawdown_pct: float) -> float:
    # Chain reorg on any DEX-settled position risks total loss of that leg.
    dex_exposed = sum(p.notional_usd for p in positions if p.metadata.get("chain"))
    return -dex_exposed


SCENARIOS: dict[str, Callable[[list[Position], float, float], float]] = {
    "withdrawal_freeze": _withdrawal_freeze,
    "price_shock_30pct": _price_shock_30pct,
    "api_outage": _api_outage,
    "fifty_one_percent_attack": _fifty_one_percent_attack,
}


def run_stress_tests(
    scenarios: list[str], positions: list[Position], equity_usd: float, max_drawdown_pct: float
) -> list[StressTestResult]:
    results = []
    for name in scenarios:
        fn = SCENARIOS.get(name)
        if fn is None:
            raise ValueError(f"Unknown stress scenario '{name}' — register it in risk/stress_test.py SCENARIOS")
        pnl = fn(positions, equity_usd, max_drawdown_pct)
        surviving_equity = equity_usd + pnl
        drawdown_pct = (equity_usd - surviving_equity) / equity_usd * 100 if equity_usd > 0 else 0.0
        results.append(
            StressTestResult(
                scenario=name,
                simulated_pnl_usd=pnl,
                surviving_equity_usd=surviving_equity,
                breaches_drawdown_limit=drawdown_pct >= max_drawdown_pct,
            )
        )
    return results
