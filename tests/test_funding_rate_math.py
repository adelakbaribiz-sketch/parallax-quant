import pytest

from arbitragex_engine.strategies.funding_rate import (
    breakeven_hours,
    expected_funding_income,
    liquidation_price_short,
    safe_margin_distance_pct,
)


def test_liquidation_price_short_above_entry():
    # A short position liquidates when price rises, so liq_price > entry
    liq = liquidation_price_short(entry_price=100.0, leverage=3.0, maintenance_margin_rate=0.005)
    assert liq > 100.0
    # 1/3 initial margin - 0.5% maintenance => ~32.83% above entry
    assert liq == pytest.approx(100 * (1 + 1 / 3 - 0.005), rel=1e-9)


def test_higher_leverage_means_closer_liquidation():
    liq_low_lev = liquidation_price_short(100.0, leverage=2.0, maintenance_margin_rate=0.005)
    liq_high_lev = liquidation_price_short(100.0, leverage=5.0, maintenance_margin_rate=0.005)
    assert (liq_high_lev - 100.0) < (liq_low_lev - 100.0)


def test_safe_margin_distance_at_entry_is_zero():
    liq = liquidation_price_short(100.0, 3.0, 0.005)
    assert safe_margin_distance_pct(entry_price=100.0, mark_price=100.0, liq_price=liq) == 0.0


def test_safe_margin_distance_at_liquidation_is_100():
    liq = liquidation_price_short(100.0, 3.0, 0.005)
    assert safe_margin_distance_pct(entry_price=100.0, mark_price=liq, liq_price=liq) == pytest.approx(100.0)


def test_safe_margin_distance_halfway():
    liq = liquidation_price_short(100.0, 3.0, 0.005)
    midpoint = 100.0 + (liq - 100.0) / 2
    assert safe_margin_distance_pct(100.0, midpoint, liq) == pytest.approx(50.0, rel=1e-6)


def test_breakeven_hours_scales_inversely_with_funding_rate():
    hours_low_rate = breakeven_hours(total_fees_pct=0.2, slippage_pct=0.1, avg_funding_rate_pct_per_period=0.01, interval_hours=8)
    hours_high_rate = breakeven_hours(total_fees_pct=0.2, slippage_pct=0.1, avg_funding_rate_pct_per_period=0.05, interval_hours=8)
    assert hours_high_rate < hours_low_rate


def test_breakeven_hours_infinite_when_no_funding():
    assert breakeven_hours(0.2, 0.1, avg_funding_rate_pct_per_period=0.0, interval_hours=8) == float("inf")


def test_expected_funding_income_linear_in_periods_and_leverage():
    income_1x = expected_funding_income(funding_rate_pct_per_period=0.01, leverage=1.0, notional_usd=10_000, periods=1)
    income_2x = expected_funding_income(funding_rate_pct_per_period=0.01, leverage=2.0, notional_usd=10_000, periods=1)
    income_1x_10periods = expected_funding_income(funding_rate_pct_per_period=0.01, leverage=1.0, notional_usd=10_000, periods=10)

    assert income_2x == pytest.approx(income_1x * 2)
    assert income_1x_10periods == pytest.approx(income_1x * 10)
    assert income_1x == pytest.approx(0.01 / 100 * 1.0 * 10_000 * 1)
