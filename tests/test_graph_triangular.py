import math

import pytest

from parallax_quant.strategies.graph import RateGraph


def test_no_cycle_when_rates_are_consistent():
    """USD->EUR->GBP->USD with perfectly consistent cross rates has no
    arbitrage: product of rates == 1, weight sum == 0, not negative."""
    graph = RateGraph()
    usd_eur = 0.90
    eur_gbp = 0.85
    gbp_usd = 1.0 / (usd_eur * eur_gbp)  # exactly consistent, no edge

    graph.add_edge("USD", "EUR", usd_eur)
    graph.add_edge("EUR", "GBP", eur_gbp)
    graph.add_edge("GBP", "USD", gbp_usd)

    assert graph.find_negative_cycle() is None


def test_detects_profitable_cycle():
    graph = RateGraph()
    graph.add_edge("USD", "EUR", 0.90)
    graph.add_edge("EUR", "GBP", 0.85)
    # Deliberately mispriced: real fair rate would be ~1.307, this is inflated
    graph.add_edge("GBP", "USD", 1.40)

    cycle = graph.find_negative_cycle()
    assert cycle is not None
    assert cycle[0] == cycle[-1]

    multiplier = graph.cycle_profit_multiplier(cycle)
    assert multiplier > 1.0


def test_cycle_profit_multiplier_matches_manual_product():
    graph = RateGraph()
    graph.add_edge("A", "B", 2.0)
    graph.add_edge("B", "C", 3.0)
    graph.add_edge("C", "A", 0.2)  # 2 * 3 * 0.2 = 1.2 > 1, profitable

    cycle = graph.find_negative_cycle()
    assert cycle is not None
    multiplier = graph.cycle_profit_multiplier(cycle)
    assert multiplier == pytest.approx(1.2, rel=1e-6)


def test_negative_or_zero_rate_rejected():
    graph = RateGraph()
    with pytest.raises(ValueError):
        graph.add_edge("A", "B", 0.0)
    with pytest.raises(ValueError):
        graph.add_edge("A", "B", -1.0)


def test_weight_is_negative_log_rate():
    graph = RateGraph()
    graph.add_edge("A", "B", 2.0)
    assert graph.weight("A", "B") == pytest.approx(-math.log(2.0))


def test_fewer_than_three_nodes_has_no_cycle():
    graph = RateGraph()
    graph.add_edge("A", "B", 1.5)
    graph.add_edge("B", "A", 1.5)  # would be profitable if cycles of length 2 counted
    assert graph.find_negative_cycle() is None
