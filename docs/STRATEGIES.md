# Strategies

Every formula below is implemented exactly as written, in the file
referenced, and every numeric parameter it depends on is a field in
`config/config.yaml` (linked as `config.<path>`) — never a hard-coded
literal in the strategy code.

## Statistical arbitrage (`strategies/stat_arb.py`)

**Pair selection.** A candidate pair is traded only if *both*:
- Engle-Granger test (`strategies/cointegration.py::engle_granger_test`,
  lag chosen by `config.strategies.stat_arb.lag_selection_criterion`,
  max lag `engle_granger_max_lag`) has p-value < 0.05, and
- Johansen trace test (`johansen_test`) rejects the null of no
  cointegration at `config.strategies.stat_arb.cointegration_confidence`

**Hedge ratio.** Time-varying, via a scalar Kalman filter
(`strategies/kalman.py`):

```
State:       β_t = β_(t-1) + w_t,      w_t ~ N(0, Q)
Observation: y_t = β_t · x_t + v_t,    v_t ~ N(0, R)
Gain:        K_t = P_(t|t-1)·x_t / (x_t²·P_(t|t-1) + R)
Update:      β_t = β_(t-1) + K_t·(y_t - β_(t-1)·x_t)
             P_t = (1 - K_t·x_t)·P_(t|t-1)
```

`Q = config.strategies.stat_arb.kalman_process_variance_q`,
`R = config.strategies.stat_arb.kalman_observation_variance_r`.

**Signal.** Z-score of the Kalman-filtered spread over
`zscore_lookback_periods` (default 200) periods:

```
Z = (spread_t - mean(spread)) / std(spread)
```

- Entry: `Z ≥ entry_zscore` (short the spread) or `Z ≤ -entry_zscore` (long the spread) — default 1.5
- Exit: `|Z| ≤ exit_zscore` — default 0.3
- Stop-loss: `|Z| ≥ stop_loss_sigma` — default 3.0
- Time-stop: position held ≥ `time_stop_hours` — default 48h

**Structural-break monitoring.** Every `chow_test_recheck_interval_hours`
(default 24h), a Chow test (`cointegration.py::chow_test`) splits the
spread's residual history at its midpoint and tests for a level/slope
break. If p-value < `chow_test_p_value_threshold` (default 0.05), the
position is closed regardless of its current Z-score — the pair's
cointegration is assumed to have broken down.

**Allocation.** `allocation_pct_min`–`allocation_pct_max` (20–30%) of
total capital, `max_pct_per_pair` (5%) per pair, minimum
`min_uncorrelated_pairs` (5) pairs selected before the strategy is
considered adequately diversified (logged as a warning otherwise, not a
hard stop — see `select_cointegrated_pairs`).

## Triangular arbitrage (`strategies/triangular.py`, `strategies/graph.py`)

**Graph construction.** A directed graph over currencies on one
exchange, edge weight `w(i,j) = -log(rate(i,j))`.

**Detection.** Bellman-Ford from a virtual zero-weight source; a
relaxable edge after `|V|-1` iterations indicates a negative-weight
cycle, which corresponds exactly to a profitable loop:

```
sum(w) < 0  ⟺  sum(-log(rate)) < 0  ⟺  product(rate) > 1
```

**Profit condition.** `product(rate) × (1 - taker_fee)^legs > 1 +
min_profit_after_fees_pct` (default 0.05%).

**Sizing.** Capped at `max_pct_per_opportunity` (2%) of capital *and* at
the notional that would consume no more than
`max_orderbook_depth_consumption_pct` (5%) of top-of-book volume on the
thinnest leg (`_max_size_within_depth_limit`).

**Execution.** First leg via a timeout-bounded limit order
(`leg_fill_timeout_ms`, default 2000ms); if filled, the remaining legs
fire concurrently via `asyncio.gather` under the same timeout. If any
remaining leg fails to fill, the first leg is immediately flattened with
a market order — the strategy never holds an unhedged single-currency
position.

## Funding-rate arbitrage (`strategies/funding_rate.py`)

**Position.** Spot long + perpetual short (delta-neutral), opened only
when the perpetual's funding rate is positive (shorts get paid).

**Gross income.**
```
R = Σ (funding_rate_per_period × leverage × notional)
```

**Break-even.**
```
T_breakeven = (fees% + slippage%) / avg(funding_rate_per_period%) × interval_hours
```
If `T_breakeven > breakeven_warning_hours` (default 48h), the opportunity
is rejected.

**Liquidation price** (isolated-margin short, approximation — see
caveat below):
```
liq_price = entry_price × (1 + 1/leverage - maintenance_margin_rate)
```

**Margin safety.** Position closes when the mark price has moved
`safe_margin_distance_pct`% (default 20%) of the way from entry toward
the computed liquidation price, *or* when funding flips negative, *or*
when the exchange-reported margin utilization crosses the same
threshold (`risk/margin_monitor.py` checks this independently on its
own polling loop).

**Leverage.** `leverage_min`–`leverage_max` (2–3×); the strategy sizes
conservatively at `leverage_min` rather than chasing the max.

> **Caveat:** the liquidation-price formula above is a standard
> approximation, not necessarily the exact formula your exchange uses
> (which can include additional factors like a risk-limit tier or
> cross-margin netting). Cross-check against the exchange's own API
> before trusting it with real capital — see `docs/LIMITATIONS.md`.

## DEX-CEX flash-loan arbitrage (`strategies/dex_cex_flashloan.py`)

**Profit.**
```
profit = |P_cex - P_dex| × amount - gas_cost - flashloan_fee
```

**Gas cost** (EIP-1559):
```
gas_cost_usd = gas_units × (base_fee_gwei + priority_fee_gwei) / 1e9 × eth_price_usd
```
scaled up by `gas_estimate_safety_margin_pct` (default 20%) before the
profit check, and rejected outright if `base_fee_gwei >
max_gas_price_gwei`.

**Flash-loan fee.** Protocol-specific (Aave v3: 5 bps at time of
writing, `PROTOCOL_FEE_BPS` in the strategy module).

**MEV protection.** Submission through Flashbots Protect
(`execution/live_dex.py::send_via_flashbots_protect`) when
`use_flashbots_protect: true`, so the arbitrage transaction is relayed
privately to block builders instead of sitting in the public mempool
where another searcher could front-run it. This protects *this*
strategy's own transaction; it is not related to, and this codebase does
not implement, front-running *other* traders' transactions (see
`docs/LIMITATIONS.md`).

**On-chain execution.** Requires `contracts/FlashArbReceiver.sol`
deployed (Aave v3 `flashLoanSimple` callback pattern) — see
`docs/DEPLOYMENT.md`. This is real, standard Solidity, but is **not
audited** and has not been exercised on mainnet as part of this build.

## Explicitly out of scope

Options/volatility arbitrage (Black-Scholes/SABR), cross-chain bridge
arbitrage, MEV liquidation bots, and every strategy in the original
specification's "other strategies" section (basis, calendar spread,
lending-rate arbitrage, prediction markets, P2P) are not implemented —
see `docs/ROADMAP.md`. **MEV sandwich attacks are deliberately excluded
and will not be added** — see `docs/LIMITATIONS.md`.
