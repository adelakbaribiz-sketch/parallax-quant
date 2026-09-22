import numpy as np
import pandas as pd
import pytest

from arbitragex_engine.strategies.cointegration import (
    chow_test,
    engle_granger_test,
    is_stationary,
    johansen_test,
)


@pytest.fixture
def cointegrated_pair():
    rng = np.random.default_rng(123)
    n = 300
    common_walk = np.cumsum(rng.normal(0, 1, n)) + 100
    series_a = pd.Series(common_walk + rng.normal(0, 0.5, n))
    series_b = pd.Series(2 * common_walk + rng.normal(0, 0.5, n))  # cointegrated with A via shared random walk
    return series_a, series_b


@pytest.fixture
def independent_pair():
    rng = np.random.default_rng(456)
    n = 300
    series_a = pd.Series(np.cumsum(rng.normal(0, 1, n)) + 100)
    series_b = pd.Series(np.cumsum(rng.normal(0, 1, n)) + 50)  # independent random walk
    return series_a, series_b


def test_engle_granger_detects_cointegrated_pair(cointegrated_pair):
    a, b = cointegrated_pair
    result = engle_granger_test(a, b, max_lag=5, criterion="aic")
    assert result.is_cointegrated is True
    assert result.method == "engle_granger"


def test_engle_granger_rejects_independent_pair(independent_pair):
    a, b = independent_pair
    result = engle_granger_test(a, b, max_lag=5, criterion="aic")
    assert result.is_cointegrated is False


def test_johansen_detects_cointegrated_pair(cointegrated_pair):
    a, b = cointegrated_pair
    matrix = pd.concat([a, b], axis=1)
    result = johansen_test(matrix, confidence=0.95)
    assert result.method == "johansen"
    assert result.test_statistic > 0


def test_is_stationary_on_white_noise():
    rng = np.random.default_rng(1)
    white_noise = pd.Series(rng.normal(0, 1, 200))
    assert is_stationary(white_noise) is True


def test_is_stationary_false_on_random_walk():
    # ADF is a statistical test with a ~5% false-positive rate for
    # "stationary" even on a true random walk — average the verdict across
    # several independent walks rather than asserting on one seed, which
    # occasionally (correctly, per the test's own error rate) disagrees.
    rng = np.random.default_rng(2)
    verdicts = [is_stationary(pd.Series(np.cumsum(rng.normal(0, 1, 200)))) for _ in range(10)]
    assert sum(verdicts) <= 2  # at most ~20% false-positive rate tolerated across 10 draws


def test_chow_test_detects_structural_break():
    rng = np.random.default_rng(9)
    n = 200
    # Residuals flat around 0 for first half, then a clear level shift
    first_half = rng.normal(0, 0.1, n // 2)
    second_half = rng.normal(5, 0.1, n // 2)
    residuals = pd.Series(np.concatenate([first_half, second_half]))

    _f_stat, p_value = chow_test(residuals, break_index=n // 2)
    assert p_value < 0.01  # a level shift this obvious must be a significant break


def test_chow_test_no_break_in_stable_series():
    rng = np.random.default_rng(11)
    residuals = pd.Series(rng.normal(0, 1, 200))
    _f_stat, p_value = chow_test(residuals, break_index=100)
    assert p_value > 0.01  # pure noise around a stable mean shouldn't register a break at 1% (nondeterministic but very likely)


def test_chow_test_rejects_edge_break_index():
    residuals = pd.Series(range(20), dtype=float)
    with pytest.raises(ValueError):
        chow_test(residuals, break_index=1)
