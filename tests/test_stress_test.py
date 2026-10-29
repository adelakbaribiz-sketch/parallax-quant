import pytest

from parallax_quant.risk.stress_test import run_stress_tests
from parallax_quant.strategies.base import Position, SignalDirection


def make_position(notional, chain=None):
    return Position(
        strategy="stat_arb",
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        notional_usd=notional,
        entry_price=100.0,
        opened_at=0.0,
        metadata={"chain": chain} if chain else {},
    )


def test_price_shock_reduces_equity_by_30_percent_of_notional():
    positions = [make_position(50_000)]
    results = run_stress_tests(["price_shock_30pct"], positions, equity_usd=100_000, max_drawdown_pct=10)
    result = results[0]
    assert result.simulated_pnl_usd == -15_000
    assert result.surviving_equity_usd == 85_000


def test_breach_flag_set_when_drawdown_exceeds_limit():
    positions = [make_position(80_000)]
    results = run_stress_tests(["price_shock_30pct"], positions, equity_usd=100_000, max_drawdown_pct=10)
    assert results[0].breaches_drawdown_limit is True


def test_no_breach_when_impact_small():
    positions = [make_position(1_000)]
    results = run_stress_tests(["price_shock_30pct"], positions, equity_usd=100_000, max_drawdown_pct=50)
    assert results[0].breaches_drawdown_limit is False


def test_fifty_one_percent_attack_only_hits_chain_exposed_positions():
    positions = [make_position(10_000, chain="ethereum"), make_position(20_000)]
    results = run_stress_tests(["fifty_one_percent_attack"], positions, equity_usd=100_000, max_drawdown_pct=10)
    assert results[0].simulated_pnl_usd == -10_000


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        run_stress_tests(["not_a_real_scenario"], [], equity_usd=100_000, max_drawdown_pct=10)


def test_all_configured_scenarios_are_registered():
    from parallax_quant.config import RiskConfig

    default_scenarios = RiskConfig().stress_test_scenarios
    # Should not raise for any scenario the default config declares
    run_stress_tests(default_scenarios, [], equity_usd=100_000, max_drawdown_pct=10)
