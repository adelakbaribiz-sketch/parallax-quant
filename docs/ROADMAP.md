# Roadmap

## Phase 0 — this build (done)

Four strategies with real math, paper-trading execution, agent pipeline,
risk layer, event-driven backtester, YAML/Pydantic config, Docker/k8s
manifests, Prometheus/Grafana, and this documentation suite.

## Phase 1 — validate against real data (next)

- Build a historical data ingestion pipeline (ccxt REST backfill into
  TimescaleDB) — `backtest.historical_data_start/end` in config.yaml are
  currently placeholders with nothing behind them
- Run the backtester (`backtest/engine.py`) against real multi-month
  history for all four strategies; publish the resulting Sharpe/Sortino/
  drawdown numbers honestly, including negative results
- Integration-test the TimescaleDB/RabbitMQ code against real running
  instances (`docker compose up` from `docker/docker-compose.yml`)

## Phase 2 — sandbox live execution

- Run `execution/live_cex.py` against exchange sandbox/testnet accounts
  for an extended period before ever pointing at a funded account
- Compile, deploy, and test `contracts/FlashArbReceiver.sol` on a
  testnet (Sepolia or successor); get it audited before mainnet
- Resolve `execution/live_dex.py`'s `submit_flashloan_bundle()`
  `NotImplementedError` by building the per-pair router/token address
  registry referenced in its docstring

## Phase 3 — production hardening

- Move the execution wallet's private key off a plain env var (KMS/HSM
  or a signing service)
- Dependency vulnerability scanning in CI
- Replace the `Recreate` single-replica Kubernetes deployment strategy
  with a proper distributed-lock-based multi-replica setup if horizontal
  scaling is needed
- PPO/DQN learning agent, if the bandit's results justify the added
  complexity — needs a proper offline-RL validation methodology first,
  not just wiring up a library

## Explicitly deferred, not "coming soon"

- **Options/volatility arbitrage** (Black-Scholes/SABR market-making) —
  a distinct engineering project (Greeks computation, options-chain data
  feed, delta-hedging execution) large enough to warrant its own build
- **Cross-chain bridge arbitrage** — requires integrating specific bridge
  protocols (Wormhole, LayerZero, Stargate) with their own settlement
  and failure-mode semantics
- **MEV liquidation bots** — legitimate MEV (unlike sandwich attacks),
  but a separate strategy needing its own liquidation-monitoring
  infrastructure
- **Basis, calendar spread, lending-rate, prediction-market, P2P
  strategies** — named in the original specification, not built; each
  is a smaller lift than the four implemented here but still real work

## Never planned

- **MEV sandwich attacks.** Not a roadmap item — see README and
  `docs/LIMITATIONS.md`.
