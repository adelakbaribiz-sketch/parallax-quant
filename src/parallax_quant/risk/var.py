"""Value-at-Risk and Conditional VaR via Monte Carlo simulation over
historical return covariance, per config.RiskConfig (var_confidence_level,
var_monte_carlo_paths, var_time_horizon_days)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class VarResult:
    var_usd: float
    cvar_usd: float
    confidence_level: float
    horizon_days: int
    paths: int


def monte_carlo_var(
    position_values_usd: np.ndarray,
    covariance_matrix: np.ndarray,
    mean_daily_returns: np.ndarray,
    confidence_level: float,
    horizon_days: int,
    n_paths: int,
    seed: int | None = None,
) -> VarResult:
    """Simulates `n_paths` correlated multi-day return paths for a
    portfolio of positions via a Cholesky decomposition of the covariance
    matrix, then reads VaR/CVaR off the simulated P&L distribution's
    empirical quantile.

    position_values_usd: current USD notional of each position (N,)
    covariance_matrix:    daily return covariance across the N assets (N, N)
    mean_daily_returns:   daily mean return per asset (N,)
    """
    n_assets = len(position_values_usd)
    if covariance_matrix.shape != (n_assets, n_assets):
        raise ValueError("covariance_matrix shape must match number of positions")

    rng = np.random.default_rng(seed)
    # Scale mean/cov to the horizon (assumes i.i.d. daily returns, the
    # standard simplifying assumption for a short VaR horizon).
    horizon_mean = mean_daily_returns * horizon_days
    horizon_cov = covariance_matrix * horizon_days

    # Cholesky requires positive semi-definiteness; nudge the diagonal if
    # numerical noise from a small sample makes it borderline singular.
    try:
        chol = np.linalg.cholesky(horizon_cov)
    except np.linalg.LinAlgError:
        jitter = np.eye(n_assets) * 1e-10
        chol = np.linalg.cholesky(horizon_cov + jitter)

    z = rng.standard_normal((n_paths, n_assets))
    simulated_returns = horizon_mean + z @ chol.T  # (n_paths, n_assets)

    portfolio_pnl = simulated_returns @ position_values_usd  # (n_paths,)

    loss = -portfolio_pnl
    var_index = int(np.ceil(confidence_level * n_paths)) - 1
    sorted_losses = np.sort(loss)
    var_usd = float(sorted_losses[var_index])

    tail_losses = sorted_losses[var_index:]
    cvar_usd = float(tail_losses.mean()) if len(tail_losses) > 0 else var_usd

    return VarResult(
        var_usd=max(0.0, var_usd),
        cvar_usd=max(0.0, cvar_usd),
        confidence_level=confidence_level,
        horizon_days=horizon_days,
        paths=n_paths,
    )
