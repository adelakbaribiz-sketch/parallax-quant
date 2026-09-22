import numpy as np

from arbitragex_engine.risk.var import monte_carlo_var


def test_var_increases_with_confidence_level():
    rng_positions = np.array([100_000.0, 50_000.0])
    cov = np.array([[0.0004, 0.0001], [0.0001, 0.0003]])
    means = np.array([0.0002, 0.0001])

    result_95 = monte_carlo_var(rng_positions, cov, means, confidence_level=0.95, horizon_days=1, n_paths=20_000, seed=1)
    result_99 = monte_carlo_var(rng_positions, cov, means, confidence_level=0.99, horizon_days=1, n_paths=20_000, seed=1)

    assert result_99.var_usd >= result_95.var_usd


def test_var_scales_with_horizon():
    positions = np.array([100_000.0])
    cov = np.array([[0.0004]])
    means = np.array([0.0])

    result_1d = monte_carlo_var(positions, cov, means, confidence_level=0.99, horizon_days=1, n_paths=20_000, seed=2)
    result_10d = monte_carlo_var(positions, cov, means, confidence_level=0.99, horizon_days=10, n_paths=20_000, seed=2)

    # VaR should grow roughly with sqrt(time), definitely not shrink
    assert result_10d.var_usd > result_1d.var_usd


def test_cvar_greater_than_or_equal_to_var():
    positions = np.array([100_000.0, 75_000.0, 25_000.0])
    cov = np.eye(3) * 0.0005
    means = np.zeros(3)

    result = monte_carlo_var(positions, cov, means, confidence_level=0.95, horizon_days=1, n_paths=50_000, seed=3)
    assert result.cvar_usd >= result.var_usd


def test_zero_volatility_gives_near_zero_var():
    positions = np.array([100_000.0])
    cov = np.array([[1e-12]])
    means = np.array([0.0])

    result = monte_carlo_var(positions, cov, means, confidence_level=0.99, horizon_days=1, n_paths=5_000, seed=4)
    assert result.var_usd < 100.0  # effectively riskless position => negligible VaR


def test_result_metadata_matches_inputs():
    positions = np.array([10_000.0])
    cov = np.array([[0.0001]])
    means = np.array([0.0])

    result = monte_carlo_var(positions, cov, means, confidence_level=0.975, horizon_days=5, n_paths=1000, seed=5)
    assert result.confidence_level == 0.975
    assert result.horizon_days == 5
    assert result.paths == 1000
