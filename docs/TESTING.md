# Testing

Numbers below are from an actual `pytest --cov` run against this
codebase (Python 3.12.2, all dependencies installed from `pyproject.toml`)
— not asserted targets. Reproduce with:

```bash
pip install -e ".[dev]"
pytest              # runs with coverage per pyproject.toml's addopts
ruff check src tests
mypy src
```

## PASSED

- **111 tests passing**, 0 failures
- **`ruff check src tests`** — 0 errors
- **`mypy src`** — 0 errors, 49 source files checked
- **Overall coverage: 50%** (2098 statements, 1058 missed)

### Coverage by area

| Module group | Coverage | Why |
|---|---|---|
| `config.py` | 100% | Fully exercised by `test_config.py`, including the live-trading gate |
| `data/models.py`, `data/ratelimit.py` | 100% | Pure logic, no I/O |
| `risk/position_limits.py` | 100% | Pure logic |
| `strategies/base.py` | 100% | Exercised transitively by the strategy tests |
| `risk/var.py`, `risk/correlation.py`, `risk/stress_test.py` | 88–94% | Core formulas tested; a few defensive branches (e.g. Cholesky jitter fallback) not hit by the test data chosen |
| `strategies/kalman.py`, `strategies/graph.py`, `strategies/cointegration.py` | 94–98% | Verified against synthetic data with known statistical properties |
| `strategies/funding_rate.py`, `strategies/triangular.py` | 92–95% | Full scan/evaluate/execute/manage_risk cycle exercised against fake Protocol implementations (no network) |
| `strategies/stat_arb.py` | 77% | Core cycle tested; a few Chow-test-triggered and edge-case branches not hit |
| `execution/paper.py`, `execution/base.py` | 79–83% | Fill/slippage/fee logic tested; a couple of edge branches not hit |
| `strategies/dex_cex_flashloan.py` | 41% | `scan`/`evaluate` math tested at the function level (`test_dex_flashloan_math.py`); the strategy class's async orchestration isn't, since `execute()` calls `submit_flashloan_bundle()` which itself raises `NotImplementedError` by design (see LIMITATIONS.md) |
| `agents/*`, `data/cex_feed.py`, `data/dex_feed.py`, `data/storage.py`, `execution/live_cex.py`, `execution/live_dex.py`, `backtest/engine.py`, `compliance/*`, `monitoring/*`, `main.py` | 0% | Require a running message broker, database, exchange connection, or RPC endpoint — none available in this build environment. Written as real, reviewable code (see each module's docstring) but not exercised by an automated test here |

**No 80% blanket target was met or claimed.** The pure-logic and
Protocol-testable modules — which is to say, everything that doesn't
require a live external service — sit at 77–100%. The 0%-covered modules
are exactly the ones `docs/LIMITATIONS.md` already flags as
"real code, not integration-tested in this build."

## FIXED during this pass

- **Sharpe/Sortino ratio blow-up on near-constant returns**
  (`backtest/metrics.py`): a return series with mathematically zero
  variance can have a numerically tiny nonzero `std()` due to
  mean-subtraction floating-point rounding, which turned an intended
  `0.0` guard into a ~1e16 sharpe ratio. Fixed with `np.isclose(std, 0,
  atol=1e-10)` instead of an exact `== 0` check. Caught by
  `test_sharpe_positive_for_consistent_positive_returns`.
- 3 test-only bugs: a `pandas` Copy-on-Write read-only-array issue in
  `test_correlation.py`, a floating-point-precision assertion in
  `test_data_models.py`, and an inherently ~5%-flaky ADF-test assertion
  in `test_cointegration.py` (fixed by averaging the verdict across 10
  independent draws instead of asserting on one seed).
- 13 `ruff` findings: unused variables, a mutable class-attribute
  default, `zip()` → `itertools.pairwise()`, a redundant nested `if`,
  and a blind-exception catch that's now logged.
- 7 `mypy` findings: mostly `Any`-typing the lazily-imported `aio_pika`
  connection/channel attributes and correcting one return-type
  annotation (`dict` → `web3.types.TxParams`).

## REMAINING / not done

- No integration tests against a real TimescaleDB/RabbitMQ/exchange —
  see the 0%-coverage table row above and `docs/LIMITATIONS.md`
- No test for `backtest/engine.py`'s end-to-end run loop (the underlying
  slippage/fee/metrics modules it composes are tested individually)
- No load/performance testing
- No mutation testing
