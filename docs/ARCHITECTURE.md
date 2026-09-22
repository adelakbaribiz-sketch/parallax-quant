# Architecture

## Message flow

```
┌──────────────┐  opportunities.raw   ┌───────────────────┐
│ ScannerAgent │ ───────────────────▶ │ OpportunityAgent   │
│ (poll every  │                      │ (evaluate, filter  │
│  strategy)   │                      │  by min liquidity) │
└──────────────┘                      └─────────┬──────────┘
                                    opportunities.evaluated
                                                 ▼
┌──────────────┐  opportunities.approved  ┌────────────┐
│ ExecutionAgent│ ◀───────────────────────│ RiskAgent  │
│ (execute,     │   opportunities.rejected│ (position  │
│  manage_risk) │ ◀────────────────────── │  limits,   │
└──────────────┘                          │  drawdown, │
                                           │  VaR/stress)│
                                           └────────────┘
```

All four agents run as concurrent `asyncio` tasks inside one process by
default (`agents.message_bus.backend: in_memory`), coordinated via
`agents/orchestrator.py`. Setting `message_bus.backend: rabbitmq`
switches every `publish`/`subscribe` call to real `aio-pika` queues,
letting the four agents run as separate processes/containers/pods
without any code change in the agents themselves — the message bus is
the only thing that changes.

## Why the strategy interface looks the way it does

`strategies/base.py`'s `BaseStrategy` has exactly four methods because
that's the natural seam between the four agents:

- `scan()` — ScannerAgent calls this. Pure market-data read, no side effects.
- `evaluate()` — OpportunityAgent calls this. Nets out costs, sizes the
  position, never touches an exchange.
- `execute()` — ExecutionAgent calls this, and *only* this agent. Every
  strategy receives its `ExecutionAdapter` at construction time (paper,
  sandbox, or live), so `execute()` never knows which mode it's running in.
- `manage_risk()` — ExecutionAgent polls this for every open position.
  Returns a boolean (close now / keep open), not a mutation, so it's
  trivially unit-testable without a live position.

This is also exactly the same interface the backtester
(`backtest/engine.py`) drives — a strategy is tested and traded through
the identical code path, which is what makes a backtest result meaningful
rather than a parallel simulation that could silently diverge from live
behavior.

## Data layer

```
CexFeed (ccxt async, websocket)  ──┐
DexFeed (web3.py, Chainlink +      ├──▶ Tick / OrderBook / FundingRate
         Uniswap V2 reserves)   ───┘         (data/models.py)
                                              │
                                    TimescaleStore (hypertable,
                                    compression after 7d by default)
```

`data/ratelimit.py`'s `TokenBucket` throttles every REST call per
exchange from `ExchangeConfig.rate_limit_requests_per_second`;
`ExponentialBackoff` (with full jitter) governs websocket reconnects.
`data/timesync.py` corrects every tick's timestamp against NTP so
cross-exchange latency comparisons in the funding-rate and DEX-CEX
strategies are meaningful.

## Risk layer

Independent of any single strategy's `manage_risk()`:

- `risk/position_limits.py` — hard per-asset / per-exchange / drawdown
  caps, checked by RiskAgent before every execution
- `risk/var.py` — Monte Carlo VaR/CVaR over a covariance matrix of
  strategy/pair returns
- `risk/correlation.py` — flags pairs/strategies moving together above
  `correlation_reduction_threshold` and recommends a weight cut
- `risk/stress_test.py` — named scenarios (withdrawal freeze, 30% price
  shock, API outage, 51% attack), run on a schedule by RiskAgent
- `risk/margin_monitor.py` — polls margin utilization independently of
  any strategy loop, so a stalled strategy can't hide a margin problem

## Execution layer

```
ExecutionAdapter (ABC)
  ├── PaperExecutionAdapter   — default, fills against real order-book
  │                             depth from the live feed, no API key
  │                             needs trade permission
  ├── LiveCexExecutionAdapter — real ccxt order placement via
  │                             create_market_order/create_limit_order
  └── LiveDexFlashLoanAdapter — calls the deployed FlashArbReceiver
                                 contract via web3, optional Flashbots
                                 Protect submission
```

Both live adapters call `config.is_live_trading_authorized()` in
`__init__` and raise `RuntimeError` if it returns `False` — see
[LIMITATIONS.md](LIMITATIONS.md) for exactly what that check requires.

## Design system: config as the single source of truth

Every numeric threshold in every strategy, risk check, and execution
setting is a field on a Pydantic model in `config.py`, populated from
`config/config.yaml`. No strategy file contains a bare literal like
`1.5` for a Z-score threshold — it reads `self.config.entry_zscore`.
This is what makes the config file genuinely "admin and AI editable":
changing behavior never requires a code change, and an invalid edit is
rejected at startup with a precise error rather than silently changing
behavior in a way nobody notices.
