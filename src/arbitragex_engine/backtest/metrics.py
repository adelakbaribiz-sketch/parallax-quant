"""Standard performance metrics computed from a daily equity curve and a
trade log. No metric here is invented — each is the textbook formula."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BacktestMetrics:
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    total_return_pct: float
    annualized_return_pct: float
    num_trades: int


def sharpe_ratio(daily_returns: pd.Series, risk_free_rate_annual: float = 0.0, periods_per_year: int = 365) -> float:
    excess = daily_returns - (risk_free_rate_annual / periods_per_year)
    std = excess.std()
    # A near-constant return series can produce a std that's mathematically
    # zero but numerically a tiny nonzero float (mean-subtraction rounding
    # noise on ~1e-16 relative magnitude), which would otherwise blow the
    # ratio up to a meaningless ~1e16 instead of the intended 0.0 guard.
    if pd.isna(std) or np.isclose(std, 0.0, atol=1e-10):
        return 0.0
    return float((excess.mean() / std) * np.sqrt(periods_per_year))


def sortino_ratio(daily_returns: pd.Series, risk_free_rate_annual: float = 0.0, periods_per_year: int = 365) -> float:
    excess = daily_returns - (risk_free_rate_annual / periods_per_year)
    downside = excess[excess < 0]
    downside_std = downside.std()
    if pd.isna(downside_std) or np.isclose(downside_std, 0.0, atol=1e-10):
        return 0.0
    return float((excess.mean() / downside_std) * np.sqrt(periods_per_year))


def max_drawdown_pct(equity_curve: pd.Series) -> float:
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    return float(abs(drawdown.min()) * 100) if len(drawdown) else 0.0


def win_rate_pct(trade_pnls: list[float]) -> float:
    if not trade_pnls:
        return 0.0
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls) * 100


def profit_factor(trade_pnls: list[float]) -> float:
    gross_profit = sum(p for p in trade_pnls if p > 0)
    gross_loss = abs(sum(p for p in trade_pnls if p < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def compute_all_metrics(equity_curve: pd.Series, trade_pnls: list[float], periods_per_year: int = 365) -> BacktestMetrics:
    daily_returns = equity_curve.pct_change().dropna()
    total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0] - 1) * 100 if len(equity_curve) > 1 else 0.0
    n_periods = len(equity_curve)
    years = n_periods / periods_per_year if n_periods > 0 else 1
    annualized = ((equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1 / years) - 1) * 100 if years > 0 and equity_curve.iloc[0] > 0 else 0.0

    return BacktestMetrics(
        sharpe_ratio=sharpe_ratio(daily_returns, periods_per_year=periods_per_year),
        sortino_ratio=sortino_ratio(daily_returns, periods_per_year=periods_per_year),
        max_drawdown_pct=max_drawdown_pct(equity_curve),
        win_rate_pct=win_rate_pct(trade_pnls),
        profit_factor=profit_factor(trade_pnls),
        total_return_pct=float(total_return),
        annualized_return_pct=float(annualized),
        num_trades=len(trade_pnls),
    )
