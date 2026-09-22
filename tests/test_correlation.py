import numpy as np
import pandas as pd
import pytest

from arbitragex_engine.risk.correlation import (
    compute_correlation_matrix,
    effective_independent_count,
    find_correlated_pairs,
)


def test_perfectly_correlated_pair_detected():
    base = np.linspace(0, 1, 100)
    returns = pd.DataFrame({"strategy_a": base, "strategy_b": base * 2})  # perfectly correlated
    corr = compute_correlation_matrix(returns)
    findings = find_correlated_pairs(corr, threshold=0.7, weight_reduction_pct=50)

    assert len(findings) == 1
    assert findings[0].correlation > 0.99
    assert findings[0].recommended_weight_multiplier == 0.5


def test_independent_series_not_flagged():
    rng = np.random.default_rng(0)
    returns = pd.DataFrame({"strategy_a": rng.normal(0, 1, 500), "strategy_b": rng.normal(0, 1, 500)})
    corr = compute_correlation_matrix(returns)
    findings = find_correlated_pairs(corr, threshold=0.7, weight_reduction_pct=50)
    assert findings == []


def test_effective_independent_count_full_for_diagonal():
    identity = pd.DataFrame(np.eye(4), columns=["a", "b", "c", "d"], index=["a", "b", "c", "d"])
    n_eff = effective_independent_count(identity)
    assert n_eff == pytest.approx(4.0)


def test_effective_independent_count_shrinks_with_correlation():
    identity = pd.DataFrame(np.eye(4), columns=["a", "b", "c", "d"], index=["a", "b", "c", "d"])

    # Build the writable array first (pandas 3.x Copy-on-Write makes
    # DataFrame.values read-only), then wrap it in a DataFrame.
    corr_values = np.full((4, 4), 0.9)
    np.fill_diagonal(corr_values, 1.0)
    highly_correlated = pd.DataFrame(corr_values, columns=["a", "b", "c", "d"], index=["a", "b", "c", "d"])

    n_eff_indep = effective_independent_count(identity)
    n_eff_corr = effective_independent_count(highly_correlated)
    assert n_eff_corr < n_eff_indep
