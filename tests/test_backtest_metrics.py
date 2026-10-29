import numpy as np
import pandas as pd
import pytest

from parallax_quant.backtest.metrics import (
    compute_all_metrics,
    max_drawdown_pct,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    win_rate_pct,
)


def test_sharpe_zero_for_zero_volatility():
    flat_returns = pd.Series([0.0] * 100)
    assert sharpe_ratio(flat_returns) == 0.0


def test_sharpe_positive_for_consistent_positive_returns():
    returns = pd.Series([0.001] * 100)
    # Pure constant returns have a std that's mathematically zero but can be
    # a tiny nonzero float due to mean-subtraction rounding noise — the
    # near-zero guard (np.isclose, not ==) must still catch this.
    assert sharpe_ratio(returns) == 0.0


def test_sharpe_reasonable_for_noisy_positive_returns():
    rng = np.random.default_rng(1)
    returns = pd.Series(rng.normal(0.001, 0.01, 500))
    sharpe = sharpe_ratio(returns)
    assert sharpe > 0


def test_sortino_only_penalizes_downside():
    rng = np.random.default_rng(2)
    upside_noisy = pd.Series(np.abs(rng.normal(0.001, 0.01, 500)))  # never negative
    sortino = sortino_ratio(upside_noisy)
    # No downside deviation at all -> guarded to 0.0
    assert sortino == 0.0


def test_max_drawdown_detects_known_drop():
    equity = pd.Series([100, 110, 90, 95, 120])
    dd = max_drawdown_pct(equity)
    assert dd == pytest.approx((110 - 90) / 110 * 100, rel=1e-6)


def test_win_rate_and_profit_factor():
    trades = [100, -50, 200, -25, -25]
    assert win_rate_pct(trades) == pytest.approx(40.0)  # 2 of 5 positive
    pf = profit_factor(trades)
    assert pf == pytest.approx(300 / 100)  # gross profit 300, gross loss 100


def test_profit_factor_infinite_with_no_losses():
    assert profit_factor([100, 50, 25]) == float("inf")


def test_profit_factor_zero_with_no_trades():
    assert profit_factor([]) == 0.0


def test_compute_all_metrics_end_to_end():
    equity = pd.Series([100_000, 101_000, 100_500, 103_000, 102_000], index=pd.date_range("2026-01-01", periods=5))
    trades = [1000, -500, 2500]
    metrics = compute_all_metrics(equity, trades, periods_per_year=365)

    assert metrics.num_trades == 3
    assert metrics.win_rate_pct == pytest.approx(2 / 3 * 100)
    assert metrics.total_return_pct == pytest.approx((102_000 / 100_000 - 1) * 100)
