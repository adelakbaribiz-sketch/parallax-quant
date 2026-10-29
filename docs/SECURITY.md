# Security Review

## Secrets

- No API keys, private keys, or credentials are committed anywhere in
  this repository. Every credential is read from an environment variable
  named in `config.yaml` (e.g. `api_key_env: BINANCE_API_KEY`), never
  hard-coded.
- `.gitignore` excludes `.env*` (except `.env.example`) and
  `k8s/secrets.yaml`.
- `k8s/secrets.example.yaml` is explicit that a plain Kubernetes `Secret`
  is only base64-encoded, not encrypted at rest by default, and
  recommends Vault / External Secrets Operator / Sealed Secrets for any
  real deployment — the original specification's "HSM or Vault"
  requirement is not implemented by this build; the Kubernetes manifests
  here are a starting point, not that requirement satisfied.

## The highest-risk secret: `EXECUTION_WALLET_PRIVATE_KEY`

`execution/live_dex.py` requires a raw private key (via
`eth_account.Account.from_key`) to sign flash-loan transactions. This
key controls real on-chain funds and gas. Recommendations, none of which
this codebase implements for you:

- Use a dedicated hot wallet holding *only* what's needed for gas and
  flash-loan collateral buffer — never a wallet holding significant
  idle funds
- In production, prefer a hardware-backed signer or a transaction-signing
  service (e.g. a cloud KMS or a multi-sig relayer) over a raw
  environment-variable private key
- The `FlashArbReceiver.sol` contract's `onlyOwner` guard on
  `requestFlashLoan`/`sweep` means whoever holds this key has full
  control of the deployed contract's funds — treat key compromise as
  equivalent to contract compromise

## Authentication / authorization

There is no user-facing authentication in this codebase (it's a backend
engine, not a web app with logins) — the "authentication" surface that
matters is exchange API key scoping:

- `ExchangeConfig.withdrawal_enabled` defaults to `false` and every
  exchange entry in `config/config.yaml` sets it explicitly to `false`
  except the DEX entry (where the on-chain wallet inherently can move
  funds — see above). **Always create exchange API keys with trading
  permission only, withdrawal permission off**, regardless of what this
  config flag says — the flag documents intent, it does not and cannot
  enforce the exchange-side permission.

## Live-trading gate (see also LIMITATIONS.md)

Real order placement requires *both* `execution.mode` in config.yaml to
be `sandbox`/`live` *and* the exact environment variable
`PARALLAX_ENABLE_LIVE_TRADING=I_UNDERSTAND_THE_RISK`. This
belt-and-braces design means a single mistake (a YAML typo, or an env
var left over from a different project) cannot alone enable real trading
— verified in `tests/test_config.py`.

## Input validation

All external input (config.yaml, environment variables) is validated
through Pydantic models at startup — an invalid value raises
`ValidationError` with a precise field path rather than being coerced or
ignored. There is no user-facing HTTP API in this build (Prometheus
metrics endpoint only, read-only), so there's no request-body injection
surface to review.

## Smart contract (`contracts/FlashArbReceiver.sol`)

- **Not audited.** Do not deploy with meaningful funds without a proper
  audit.
- `onlyOwner` restricts `requestFlashLoan` and `sweep` to the deployer —
  no other address can trigger a loan or drain funds.
- Uses `forceApprove` (OpenZeppelin's `SafeERC20`) rather than a plain
  `approve`, avoiding the known "approve race condition" issue with
  tokens that require resetting an allowance to zero first.
- The Aave `executeOperation` callback checks `msg.sender == address(POOL)`
  so only the Aave Pool contract can trigger the arbitrage logic —
  standard, necessary guard against a malicious caller impersonating the
  flash-loan callback.
- Router/token addresses are passed as call parameters, not hard-coded —
  reduces redeployment risk if a router address needs to change, but
  also means the *caller* (the off-chain bot / the key holder) is
  responsible for passing correct addresses; the contract does no
  allow-listing of routers or tokens in this build.

## Dependency risk

Dependencies (`ccxt`, `web3.py`, `pydantic`, `asyncpg`, `aio-pika`,
`prometheus-client`, etc.) are current, actively maintained packages
pinned to minimum versions in `pyproject.toml`. Run `pip-audit` or
equivalent before any production deployment — not run as part of this
build (no network access to a vulnerability database from this
environment).

## Summary

| Category | Status |
|---|---|
| Secrets in repo | None found |
| Live trading | Explicitly double-gated (config + env var), verified by tests |
| Exchange withdrawal permission | Documented as required-off; not exchange-side enforceable from this codebase |
| Smart contract audit | Not performed — do not use with real funds without one |
| Dependency vulnerability scan | Not performed as part of this build |

Before any deployment beyond local research/paper trading: get the smart
contract audited, run a dependency vulnerability scan, and move the
execution wallet's private key out of a plain environment variable.
