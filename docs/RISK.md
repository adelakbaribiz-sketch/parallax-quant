# Risk Management

## Position limits (`risk/position_limits.py`)

Checked by RiskAgent before every execution, independent of any
strategy's own sizing logic:

- `max_pct_capital_per_asset` (default 5%) — total exposure to one asset
  across all strategies and venues
- `max_pct_capital_per_exchange` (default 10%) — total exposure on one
  exchange
- `portfolio_max_drawdown_pct` (default 10%) — new positions are refused
  once current drawdown from peak equity reaches this level

## Value at Risk (`risk/var.py`)

Monte Carlo simulation:

1. Build a covariance matrix of daily returns across open
   positions/strategies
2. Cholesky-decompose it, draw `var_monte_carlo_paths` (default 10,000)
   correlated standard-normal paths scaled to
   `var_time_horizon_days` (default 1 day)
3. Compute simulated portfolio P&L per path
4. VaR = the loss at the `var_confidence_level` (default 99%) empirical
   quantile; CVaR = the mean loss in the tail beyond that quantile

CVaR is always ≥ VaR by construction (verified in
`tests/test_var.py::test_cvar_greater_than_or_equal_to_var`) — it answers
"how bad is it, given that we're already in the bad tail," which VaR
alone does not.

## Correlation monitoring (`risk/correlation.py`)

Daily correlation matrix across strategy/pair return series. Any pair
with `|correlation| ≥ correlation_reduction_threshold` (default 0.7) has
its combined weight recommendation cut by
`correlation_weight_reduction_pct` (default 50%) — two strategies that
are supposed to be independent bets but are actually moving together
provide less real diversification than their separate allocations imply.
`effective_independent_count()` gives a single number (the
eigenvalue-based N_eff heuristic) summarizing how many *genuinely*
independent bets the portfolio currently holds.

## Stress tests (`risk/stress_test.py`)

Run on `stress_test_scenarios` (config-listed, default: all four below),
on a schedule owned by RiskAgent:

| Scenario | Model |
|---|---|
| `withdrawal_freeze` | 15% loss-equivalent on all frozen (currently open) notional |
| `price_shock_30pct` | Flat 30% loss on total open notional |
| `api_outage` | Worst single position's notional at 50% loss, as a proxy for "can't manage risk during the outage" |
| `fifty_one_percent_attack` | Full loss on notional flagged with `metadata["chain"]` (DEX-settled positions) |

Adding a new scenario is a new function in `stress_test.py`'s `SCENARIOS`
dict plus a config entry — no other code changes.

## Margin monitoring (`risk/margin_monitor.py`)

Polls every open leveraged position's margin utilization on
`margin_check_interval_seconds` (default 10s), independent of any
strategy's own loop — an alert fires at `margin_alert_utilization_pct`
(default 80%) even if the owning strategy's task has stalled.

## What this risk layer does not do

It does not simulate correlated *tail* events beyond the four named
stress scenarios (no full historical-scenario replay), does not model
cross-exchange contagion explicitly, and the VaR model assumes i.i.d.
daily returns scaled by `sqrt(horizon)` — a standard simplification that
understates risk during genuine regime changes. See
`docs/LIMITATIONS.md`.
