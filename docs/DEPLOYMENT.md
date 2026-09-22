# Deployment

## Local (paper trading, no external services required)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m arbitragex_engine.main --config config/config.yaml
```

`main.py`'s `build_strategies()` is a documented wiring stub — it logs
whether live trading is authorized and returns an empty strategy list.
To actually run strategies, wire them up explicitly (this is
deployment-specific because it depends on which exchanges/symbols you've
enabled in `config.yaml`):

```python
# Example: wiring the funding-rate strategy against paper execution
from arbitragex_engine.data.cex_feed import CexFeed
from arbitragex_engine.data.timesync import TimeSync
from arbitragex_engine.execution.paper import PaperExecutionAdapter
from arbitragex_engine.strategies.funding_rate import FundingRateArbStrategy

timesync = TimeSync(config.data.ntp_server, config.data.ntp_sync_interval_seconds, config.data.max_clock_drift_ms)
exchange_cfg = next(e for e in config.exchanges if e.id == "binance")

async with CexFeed(exchange_cfg, timesync) as feed:
    paper = PaperExecutionAdapter(
        starting_capital_usd=config.execution.paper_starting_capital_usd,
        max_slippage_pct=config.execution.max_slippage_pct,
        orderbook_provider=feed,  # CexFeed implements get_order_book via watch_order_book internally
    )
    strategy = FundingRateArbStrategy(
        config=config.strategies.funding_rate,
        exchange="binance",
        symbols=config.data.symbols,
        source=feed,
        execution=paper,
        capital_usd=config.execution.paper_starting_capital_usd,
        spot_fee_pct=0.1,
        futures_fee_pct=0.1,
    )
    strategies.append(strategy)
```

Repeat per strategy/exchange, then pass the assembled list into
`Orchestrator(strategies=strategies, ...)`.

## Docker Compose (full stack: engine + TimescaleDB + Redis + RabbitMQ + Prometheus + Grafana)

```bash
cp .env.example .env   # fill in sandbox/testnet keys
docker compose -f docker/docker-compose.yml up --build
```

- Grafana: http://localhost:3000 (admin/admin, change immediately) —
  import `monitoring/grafana-dashboard.json`
- Prometheus: http://localhost:9090
- RabbitMQ management UI: http://localhost:15672 (arbx/arbx)

Set `agents.message_bus.backend: rabbitmq` in `config/config.yaml` to
actually use the RabbitMQ container instead of the in-process default.

## Kubernetes

```bash
kubectl apply -f k8s/deployment.yaml   # also creates the arbitragex namespace
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/configmap.yaml
cp k8s/secrets.example.yaml k8s/secrets.yaml   # fill in real values — DO NOT COMMIT
kubectl apply -f k8s/secrets.yaml
```

The Deployment uses `strategy: Recreate`, not a rolling update — running
two replicas of the execution agent against the same capital would
double-execute every decision. Do not change this without adding a
distributed lock first (see `docs/ROADMAP.md`).

## Deploying `contracts/FlashArbReceiver.sol`

Not included: a Foundry/Hardhat project scaffold. To deploy (testnet
first, always):

1. Set up Foundry (`forge init`) or Hardhat in a separate `contracts/`
   toolchain of your choice
2. Install `@aave/core-v3` and `@openzeppelin/contracts` as Solidity
   dependencies
3. Deploy with the target network's Aave v3 `PoolAddressesProvider`
   address as the constructor argument
4. Set `FLASH_ARB_CONTRACT_ADDRESS` in `.env` to the deployed address
5. Get it audited before using it with meaningful funds — see
   `docs/SECURITY.md`

## Enabling live/sandbox trading

See the README's "Live trading — explicitly gated" section. Short
version: `execution.mode: sandbox` (or `live`) in config.yaml, *and*
`export ARBITRAGEX_ENABLE_LIVE_TRADING=I_UNDERSTAND_THE_RISK`. Both
required, verified in `tests/test_config.py`.
