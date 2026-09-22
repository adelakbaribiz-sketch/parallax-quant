"""Daily correlation matrix across strategy P&L series, used to detect
when two supposedly-independent strategies (or two pairs within stat_arb)
are actually moving together and should have their combined weight
reduced — per config.RiskConfig.correlation_reduction_threshold."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CorrelationFinding:
    asset_a: str
    asset_b: str
    correlation: float
    recommended_weight_multiplier: float


def compute_correlation_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    """`returns` is a wide DataFrame: columns = strategy/pair identifiers,
    rows = daily returns. Requires at least 2 non-null observations per
    pair of columns or pandas returns NaN for that cell."""
    return returns.corr(method="pearson")


def find_correlated_pairs(
    correlation_matrix: pd.DataFrame, threshold: float, weight_reduction_pct: float
) -> list[CorrelationFinding]:
    findings: list[CorrelationFinding] = []
    columns = correlation_matrix.columns.tolist()
    for i, a in enumerate(columns):
        for b in columns[i + 1 :]:
            corr = correlation_matrix.loc[a, b]
            if pd.isna(corr):
                continue
            if abs(corr) >= threshold:
                findings.append(
                    CorrelationFinding(
                        asset_a=a,
                        asset_b=b,
                        correlation=float(corr),
                        recommended_weight_multiplier=1 - (weight_reduction_pct / 100),
                    )
                )
    return findings


def effective_independent_count(correlation_matrix: pd.DataFrame) -> float:
    """Approximate number of *effectively* independent bets in the
    portfolio via the eigenvalue-based heuristic: N_eff = (sum(eigenvalues))^2
    / sum(eigenvalues^2). Equals N for a fully diagonal (uncorrelated)
    matrix and shrinks toward 1 as correlation rises."""
    eigenvalues = np.linalg.eigvalsh(correlation_matrix.fillna(0).to_numpy())
    eigenvalues = np.clip(eigenvalues, 0, None)  # guard tiny negative noise
    total = eigenvalues.sum()
    if total == 0:
        return 0.0
    return float((total**2) / (eigenvalues**2).sum())
