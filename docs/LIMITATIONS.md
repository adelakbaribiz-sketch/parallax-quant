# Limitations — Honest Status

Every capability claimed in the README or docs maps to exactly one status
below. Anything not listed should be assumed **NOT IMPLEMENTED**.

| Area | Status | Detail |
|---|---|---|
| BaseStrategy interface + agent pipeline | **REAL / IMPLEMENTED** | Runs end-to-end against paper trading; see `tests/` |
| Kalman filter, cointegration tests, Bellman-Ford, VaR, correlation math | **REAL / IMPLEMENTED, UNIT-TESTED** | Exact formulas from `docs/STRATEGIES.md`, verified against synthetic data with known properties (see `tests/test_kalman.py`, `test_cointegration.py`, `test_var.py`, `test_graph_triangular.py`) |
| Paper trading execution | **REAL / IMPLEMENTED** | Fills against live/recorded order-book depth, tracks a real cash ledger — no real order ever leaves the process |
| Live CEX order placement (`execution/live_cex.py`) | **REAL CODE, NOT LIVE-TESTED** | Calls real `ccxt` order methods; gated behind `is_live_trading_authorized()`; has not been run against a funded account as part of this build |
| Live DEX flash-loan execution (`execution/live_dex.py`) | **PARTIAL — explicit gap** | Transaction building, signing, and Flashbots submission are implemented; `submit_flashloan_bundle()` raises `NotImplementedError` because per-pair router/token address resolution is left as an explicit integration point rather than guessing addresses |
| `contracts/FlashArbReceiver.sol` | **REAL, UNAUDITED, UNDEPLOYED** | Standard Aave v3 flash-loan receiver pattern; has not been compiled, deployed, or tested on any network as part of this build |
| AI risk auditor / "AI" framing | **NOT AN ML MODEL** | Every decision (GO/NO-GO equivalents, thresholds) is deterministic math against config-driven parameters — no trained model, no LLM call anywhere in the trading path |
| Learning agent — bandit algorithm | **REAL / IMPLEMENTED, UNIT-TESTED** | A genuine epsilon-greedy multi-armed bandit; disabled by default (`learning_agent_enabled: false`) |
| Learning agent — PPO / DQN | **NOT IMPLEMENTED** | Declared in the config schema as a documented future direction; selecting them raises `NotImplementedError` rather than silently doing nothing or faking a result |
| MEV sandwich attacks | **WILL NOT BE IMPLEMENTED** | Deliberately excluded — extracts value from an identifiable other trader's transaction; see README |
| Options/volatility arbitrage (Black-Scholes/SABR) | **NOT IMPLEMENTED** | Out of scope for this build; see `docs/ROADMAP.md` |
| Cross-chain bridge arbitrage | **NOT IMPLEMENTED** | Out of scope for this build |
| MEV liquidation bots | **NOT IMPLEMENTED** | Out of scope for this build |
| Basis / calendar spread / lending-rate / prediction-market / P2P strategies | **NOT IMPLEMENTED** | Named in the original specification's "other strategies" section; none built |
| Backtesting engine | **REAL / IMPLEMENTED** | Event-driven, replays real ticks through the *same* strategy code path as live/paper trading; not yet run against a large historical dataset as part of this build (no historical data was fetched — see below) |
| Historical market data | **NOT INCLUDED** | No historical tick/order-book data is bundled or fetched by this build; `backtest.historical_data_start/end` in config.yaml are placeholders for when a real data-download pipeline is added |
| TimescaleDB / Postgres / Redis / RabbitMQ integration code | **REAL CODE, NOT INTEGRATION-TESTED** | Written against real client libraries (`asyncpg`, `aio-pika`) with correct hypertable/compression DDL; has not been run against live instances of these services as part of this build (no Docker/DB available in this environment) — see `docs/TESTING.md` |
| Kubernetes manifests | **REAL, NOT CLUSTER-TESTED** | Valid manifests with resource limits, probes, and a `Recreate` strategy (deliberately, to prevent double-execution); not applied to a real cluster as part of this build |
| AML/KYC compliance | **SCAFFOLDING ONLY** | See `docs/COMPLIANCE.md` — does not constitute legal compliance |
| "Legal hedge fund" status | **DOES NOT EXIST** | No entity has been formed, no registration filed; this is software, not a fund |
| Prometheus/Grafana monitoring | **REAL / IMPLEMENTED** | Real metric definitions and a real dashboard JSON; not run against a live Prometheus/Grafana instance as part of this build |
| 80% test coverage target | **SEE docs/TESTING.md FOR THE ACTUAL MEASURED NUMBER** | Not asserted here without having run coverage — see that document for what was actually measured |

## Why some things are "real code, not integration-tested"

This build environment does not have live exchange accounts, a funded
wallet, or running TimescaleDB/RabbitMQ/Kubernetes instances available
to test against. Rather than either (a) skipping that code entirely or
(b) claiming it was tested when it wasn't, each such module is written
as real, complete, reviewable code and explicitly labeled here as
untested-in-this-build. Exercise it against sandbox/testnet
services before trusting it.

## Current project stage

**Research / paper-trading framework.** Not a production trading system,
not a live fund, not audited. See `docs/ROADMAP.md` for the concrete
path from here to something that could responsibly touch real capital.
