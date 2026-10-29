# Changelog

## [0.2.0] — Parallax Quant

### Changed
- Renamed the project from ArbitrageX Engine to **Parallax Quant**: package
  `arbitragex_engine` → `parallax_quant`, distribution `parallax-quant`,
  live-trading env var `ARBITRAGEX_ENABLE_LIVE_TRADING` →
  `PARALLAX_ENABLE_LIVE_TRADING`, Prometheus metric prefix `arbitragex_` →
  `parallax_`, Kubernetes namespace `arbitragex` → `parallax`.
  **Breaking** for anyone importing the old package, setting the old env var,
  or alerting on the old metric names.

### Added
- `parallax_quant.dashboard`: local read-only research console (dark theme)
  that renders the real strategy/risk output on a seeded synthetic market;
  `parallax` and `parallax-console` console scripts.
- README screenshots under `docs/screenshots/`.

### Fixed
- `TriangularArbStrategy._max_size_within_depth_limit` compared leg depth
  budgets in different currencies (e.g. ETH vs USDT), which could size a
  USDT-start cycle at ~0.6 instead of ~2,000. Each leg is now converted to the
  cycle's start currency before taking the minimum.

## [0.1.0] — Initial build

### Added
- `config.py` / `config.yaml`: Pydantic-validated, fully admin-editable
  parameters for every strategy, risk limit, and execution setting
- Data layer: `CexFeed` (ccxt async websocket), `DexFeed` (web3.py,
  Chainlink oracle + Uniswap V2 reserves), `TokenBucket` rate limiter,
  NTP `TimeSync`, `TimescaleStore` hypertable persistence
- Strategies: statistical arbitrage (Engle-Granger + Johansen +
  Kalman-filtered hedge ratio + Z-score), triangular arbitrage
  (Bellman-Ford negative-cycle detection), funding-rate arbitrage
  (delta-neutral, liquidation/breakeven math), DEX-CEX flash-loan
  arbitrage (real Solidity receiver contract + Flashbots submission)
- Risk layer: Monte Carlo VaR/CVaR, correlation-based weight reduction,
  position/drawdown limits, named stress-test scenarios, independent
  margin monitor
- Execution layer: `PaperExecutionAdapter` (default), `LiveCexExecutionAdapter`
  and `LiveDexFlashLoanAdapter` (both gated behind
  `config.is_live_trading_authorized()`)
- Agent pipeline: scanner → opportunity → risk → execution, over a
  pluggable in-memory/RabbitMQ message bus, plus a research-stage
  epsilon-greedy bandit learning agent
- Event-driven backtester reusing the live strategy interface, with
  order-book-depth and fixed-bps slippage/fee models and standard
  performance metrics (Sharpe, Sortino, max drawdown, win rate, profit factor)
- Compliance scaffolding (AML/KYC provider interface, audit log) —
  explicitly documented as not constituting legal compliance
- Prometheus metrics + Grafana dashboard, Dockerfile + docker-compose
  (Timescale/Redis/RabbitMQ/Prometheus/Grafana), Kubernetes manifests
- Full documentation suite (`docs/`) including an honest
  REAL/SCAFFOLDING/NOT-IMPLEMENTED breakdown in `LIMITATIONS.md`
- Test suite covering the pure-logic modules — see `docs/TESTING.md` for
  the measured coverage number

### Deliberately not implemented
- MEV sandwich attacks (will not be added — see README/LIMITATIONS.md)
- Options/volatility, cross-chain bridge, and MEV-liquidation strategies
- PPO/DQN learning agent (bandit only)
- Historical market data ingestion (backtester is ready; no data bundled)
