import pytest

from parallax_quant.strategies.dex_cex_flashloan import (
    estimate_gas_cost_usd,
    expected_profit_usd,
    flashloan_fee_usd,
)


def test_flashloan_fee_matches_bps_calc():
    fee = flashloan_fee_usd(amount_usd=100_000, protocol_fee_bps=5.0)
    assert fee == pytest.approx(50.0)  # 5 bps of 100k = $50


def test_gas_cost_scales_with_gas_units_and_fee():
    low = estimate_gas_cost_usd(gas_units=300_000, base_fee_gwei=20, priority_fee_gwei=2, eth_price_usd=3000)
    high = estimate_gas_cost_usd(gas_units=300_000, base_fee_gwei=100, priority_fee_gwei=2, eth_price_usd=3000)
    assert high > low


def test_gas_cost_manual_calc():
    # 300,000 gas * 22 gwei = 6,600,000 gwei = 0.0066 ETH; at $3000/ETH = $19.80
    cost = estimate_gas_cost_usd(gas_units=300_000, base_fee_gwei=20, priority_fee_gwei=2, eth_price_usd=3000)
    assert cost == pytest.approx(19.8, rel=1e-6)


def test_expected_profit_positive_when_spread_covers_costs():
    profit = expected_profit_usd(cex_price=101.0, dex_price=100.0, amount=1000, gas_cost_usd=50, flashloan_fee_usd_=25)
    # gross = 1.0 * 1000 = 1000; minus 75 costs = 925
    assert profit == pytest.approx(925.0)


def test_expected_profit_negative_when_costs_exceed_spread():
    profit = expected_profit_usd(cex_price=100.05, dex_price=100.0, amount=10, gas_cost_usd=50, flashloan_fee_usd_=25)
    # gross = 0.05 * 10 = 0.5; costs = 75 => deeply negative
    assert profit < 0


def test_profit_uses_absolute_spread_regardless_of_direction():
    profit_cex_higher = expected_profit_usd(101.0, 100.0, 100, gas_cost_usd=1, flashloan_fee_usd_=1)
    profit_dex_higher = expected_profit_usd(100.0, 101.0, 100, gas_cost_usd=1, flashloan_fee_usd_=1)
    assert profit_cex_higher == profit_dex_higher
