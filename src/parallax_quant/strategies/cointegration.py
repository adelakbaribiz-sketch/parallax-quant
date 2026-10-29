"""Pair-selection statistics: Johansen and Engle-Granger cointegration
tests, and a Chow test for detecting a structural break in a previously
cointegrated relationship (used by stat_arb's manage_risk to cut a pair
whose cointegration has broken down rather than trusting a stale test)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen


@dataclass
class CointegrationResult:
    is_cointegrated: bool
    p_value: float | None
    test_statistic: float
    critical_value_95: float
    method: str


def engle_granger_test(series_a: pd.Series, series_b: pd.Series, max_lag: int, criterion: str) -> CointegrationResult:
    """Two-step Engle-Granger test with lag chosen by AIC/BIC via
    statsmodels' `coint`, which internally selects the ADF lag using the
    given information criterion (autolag=criterion)."""
    test_stat, p_value, crit_values = coint(series_a, series_b, maxlag=max_lag, autolag=criterion)
    return CointegrationResult(
        is_cointegrated=p_value < 0.05,
        p_value=float(p_value),
        test_statistic=float(test_stat),
        critical_value_95=float(crit_values[1]),  # crit_values = [1%, 5%, 10%]
        method="engle_granger",
    )


def johansen_test(price_matrix: pd.DataFrame, confidence: float) -> CointegrationResult:
    """Johansen trace test on a matrix of >=2 price series. Returns
    cointegration at the requested confidence (0.90/0.95/0.99 supported,
    matching statsmodels' critical-value columns)."""
    conf_col = {0.90: 0, 0.95: 1, 0.99: 2}.get(round(confidence, 2), 1)
    result = coint_johansen(price_matrix.to_numpy(), det_order=0, k_ar_diff=1)
    trace_stat = result.lr1[0]
    crit_value = result.cvt[0, conf_col]
    return CointegrationResult(
        is_cointegrated=trace_stat > crit_value,
        p_value=None,  # Johansen's trace test doesn't report a p-value directly
        test_statistic=float(trace_stat),
        critical_value_95=float(result.cvt[0, 1]),
        method="johansen",
    )


def chow_test(residuals: pd.Series, break_index: int) -> tuple[float, float]:
    """Chow test for a structural break in the cointegrating relationship
    at `break_index`. Returns (F-statistic, p-value). A significant break
    means the hedge ratio the pair was trading on may no longer hold."""
    from scipy import stats

    n = len(residuals)
    if break_index <= 2 or break_index >= n - 2:
        raise ValueError("break_index must leave at least 2 observations on each side")

    x = np.arange(n)
    full_rss = _ols_rss(x, residuals.to_numpy())
    rss1 = _ols_rss(x[:break_index], residuals.to_numpy()[:break_index])
    rss2 = _ols_rss(x[break_index:], residuals.to_numpy()[break_index:])

    k = 2  # intercept + slope
    numerator = (full_rss - (rss1 + rss2)) / k
    denominator = (rss1 + rss2) / (n - 2 * k)
    f_stat = numerator / denominator if denominator > 0 else float("inf")
    p_value = 1 - stats.f.cdf(f_stat, k, n - 2 * k)
    return float(f_stat), float(p_value)


def _ols_rss(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return 0.0
    coeffs = np.polyfit(x, y, 1)
    fitted = np.polyval(coeffs, x)
    return float(np.sum((y - fitted) ** 2))


def is_stationary(series: pd.Series, p_value_threshold: float = 0.05) -> bool:
    """ADF test — used to confirm a spread series is mean-reverting before
    trading it, independent of the pairwise cointegration test."""
    _, p_value, *_ = adfuller(series)
    return p_value < p_value_threshold
