"""Pydantic-validated configuration loaded from config/config.yaml.

Every tunable parameter in the system lives here — nowhere else in the
codebase should a strategy threshold, allocation percentage, or risk limit
be hard-coded. This is both the admin-editable surface and the schema an
AI agent (see agents/learning_agent.py) reads and proposes changes to; the
learning agent never writes directly to a live-trading value without the
change passing back through this same validated model.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ExecutionMode(str, Enum):
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"


class ExchangeConfig(BaseModel):
    id: str
    kind: Literal["cex", "dex"]
    enabled: bool = True
    api_key_env: str | None = None
    api_secret_env: str | None = None
    sandbox: bool = False
    rate_limit_requests_per_second: float = Field(default=10.0, gt=0)
    rate_limit_burst: int = Field(default=20, gt=0)
    websocket_reconnect_backoff_base_seconds: float = 1.0
    websocket_reconnect_backoff_max_seconds: float = 60.0
    rpc_url_env: str | None = None  # DEX only
    withdrawal_enabled: bool = False


class DataLayerConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    orderbook_depth: int = Field(default=50, gt=0)
    ntp_server: str = "pool.ntp.org"
    ntp_sync_interval_seconds: int = Field(default=3600, gt=0)
    max_clock_drift_ms: float = Field(default=250.0, gt=0)

    timescaledb_dsn_env: str = "TIMESCALEDB_DSN"
    postgres_dsn_env: str = "POSTGRES_DSN"
    redis_url_env: str = "REDIS_URL"
    hypertable_chunk_interval: str = "1 day"
    compression_after: str = "7 days"


class StatArbConfig(BaseModel):
    enabled: bool = True
    allocation_pct_min: float = Field(default=20.0, ge=0, le=100)
    allocation_pct_max: float = Field(default=30.0, ge=0, le=100)
    max_pct_per_pair: float = Field(default=5.0, ge=0, le=100)
    min_uncorrelated_pairs: int = Field(default=5, ge=1)
    cointegration_confidence: float = Field(default=0.95, gt=0, lt=1)
    engle_granger_max_lag: int = Field(default=10, ge=1)
    lag_selection_criterion: Literal["aic", "bic"] = "aic"
    zscore_lookback_periods: int = Field(default=200, ge=10)
    entry_zscore: float = Field(default=1.5, gt=0)
    exit_zscore: float = Field(default=0.3, ge=0)
    stop_loss_sigma: float = Field(default=3.0, gt=0)
    time_stop_hours: float = Field(default=48.0, gt=0)
    chow_test_recheck_interval_hours: float = Field(default=24.0, gt=0)
    chow_test_p_value_threshold: float = Field(default=0.05, gt=0, lt=1)
    kalman_process_variance_q: float = Field(default=1e-5, gt=0)
    kalman_observation_variance_r: float = Field(default=1e-3, gt=0)


class TriangularArbConfig(BaseModel):
    enabled: bool = True
    allocation_pct_min: float = Field(default=10.0, ge=0, le=100)
    allocation_pct_max: float = Field(default=15.0, ge=0, le=100)
    max_pct_per_opportunity: float = Field(default=2.0, ge=0, le=100)
    max_orderbook_depth_consumption_pct: float = Field(default=5.0, gt=0, le=100)
    min_profit_after_fees_pct: float = Field(default=0.05, ge=0)
    leg_fill_timeout_ms: int = Field(default=2000, gt=0)


class FundingRateArbConfig(BaseModel):
    enabled: bool = True
    allocation_pct_min: float = Field(default=30.0, ge=0, le=100)
    allocation_pct_max: float = Field(default=40.0, ge=0, le=100)
    leverage_min: float = Field(default=2.0, ge=1)
    leverage_max: float = Field(default=3.0, ge=1)
    safe_margin_distance_pct: float = Field(default=20.0, gt=0, le=100)
    breakeven_warning_hours: float = Field(default=48.0, gt=0)
    funding_rate_check_interval_minutes: float = Field(default=15.0, gt=0)


class DexCexFlashLoanConfig(BaseModel):
    enabled: bool = True
    allocation_pct_min: float = Field(default=10.0, ge=0, le=100)
    allocation_pct_max: float = Field(default=15.0, ge=0, le=100)
    protocols: list[str] = Field(default_factory=lambda: ["aave_v3", "uniswap_v3"])
    use_flashbots_protect: bool = True
    max_gas_price_gwei: float = Field(default=150.0, gt=0)
    eip1559_priority_fee_gwei: float = Field(default=2.0, ge=0)
    min_profit_after_gas_and_fees_usd: float = Field(default=25.0, ge=0)
    gas_estimate_safety_margin_pct: float = Field(default=20.0, ge=0)


class StrategiesConfig(BaseModel):
    stat_arb: StatArbConfig = Field(default_factory=StatArbConfig)
    triangular: TriangularArbConfig = Field(default_factory=TriangularArbConfig)
    funding_rate: FundingRateArbConfig = Field(default_factory=FundingRateArbConfig)
    dex_cex_flashloan: DexCexFlashLoanConfig = Field(default_factory=DexCexFlashLoanConfig)

    @model_validator(mode="after")
    def _allocations_within_100(self) -> StrategiesConfig:
        total_max = (
            self.stat_arb.allocation_pct_max
            + self.triangular.allocation_pct_max
            + self.funding_rate.allocation_pct_max
            + self.dex_cex_flashloan.allocation_pct_max
        )
        if total_max > 100:
            raise ValueError(
                f"Sum of strategy allocation_pct_max ({total_max}%) exceeds 100% of capital"
            )
        return self


class RiskConfig(BaseModel):
    max_pct_capital_per_asset: float = Field(default=5.0, gt=0, le=100)
    max_pct_capital_per_exchange: float = Field(default=10.0, gt=0, le=100)
    portfolio_max_drawdown_pct: float = Field(default=10.0, gt=0, le=100)
    correlation_recalc_interval_hours: float = Field(default=24.0, gt=0)
    correlation_reduction_threshold: float = Field(default=0.7, gt=0, lt=1)
    correlation_weight_reduction_pct: float = Field(default=50.0, gt=0, le=100)
    var_confidence_level: float = Field(default=0.99, gt=0, lt=1)
    var_monte_carlo_paths: int = Field(default=10_000, ge=100)
    var_time_horizon_days: int = Field(default=1, ge=1)
    margin_alert_utilization_pct: float = Field(default=80.0, gt=0, le=100)
    margin_check_interval_seconds: float = Field(default=10.0, gt=0)
    stress_test_scenarios: list[str] = Field(
        default_factory=lambda: [
            "withdrawal_freeze",
            "price_shock_30pct",
            "api_outage",
            "fifty_one_percent_attack",
        ]
    )


class ExecutionConfig(BaseModel):
    mode: ExecutionMode = ExecutionMode.PAPER
    # Live mode also requires this exact env var to be set — belt-and-braces
    # so a config-file typo alone can never enable real-money trading.
    live_trading_confirmation_env: str = "ARBITRAGEX_ENABLE_LIVE_TRADING"
    live_trading_confirmation_value: str = "I_UNDERSTAND_THE_RISK"
    paper_starting_capital_usd: float = Field(default=100_000.0, gt=0)
    max_slippage_pct: float = Field(default=0.5, ge=0)
    order_retry_attempts: int = Field(default=3, ge=0)
    order_retry_backoff_seconds: float = Field(default=0.5, ge=0)


class ComplianceConfig(BaseModel):
    """Technical scaffolding only. See docs/COMPLIANCE.md — none of this
    constitutes legal or regulatory compliance on its own."""

    aml_kyc_provider: Literal["none", "jumio", "onfido"] = "none"
    aml_kyc_api_key_env: str | None = None
    suspicious_transaction_usd_threshold: float = Field(default=10_000.0, gt=0)
    audit_log_retention_days: int = Field(default=2555, ge=1)  # ~7 years
    performance_fee_pct: float = Field(default=20.0, ge=0, le=100)
    management_fee_pct_annual: float = Field(default=2.0, ge=0, le=100)
    high_water_mark: bool = True


class MessageBusConfig(BaseModel):
    backend: Literal["in_memory", "rabbitmq", "kafka"] = "in_memory"
    rabbitmq_url_env: str = "RABBITMQ_URL"
    kafka_bootstrap_servers_env: str = "KAFKA_BOOTSTRAP_SERVERS"


class AgentsConfig(BaseModel):
    message_bus: MessageBusConfig = Field(default_factory=MessageBusConfig)
    scanner_interval_seconds: float = Field(default=1.0, gt=0)
    opportunity_min_liquidity_usd: float = Field(default=5_000.0, ge=0)
    learning_agent_enabled: bool = False  # research-stage, off by default — see docs/LIMITATIONS.md
    learning_agent_algorithm: Literal["bandit", "ppo", "dqn"] = "bandit"
    learning_agent_update_interval_hours: float = Field(default=24.0, gt=0)


class MonitoringConfig(BaseModel):
    prometheus_port: int = Field(default=9100, gt=0, lt=65536)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"


class BacktestConfig(BaseModel):
    historical_data_start: str = "2024-01-01"
    historical_data_end: str = "2026-01-01"
    slippage_model: Literal["orderbook_depth", "fixed_bps"] = "orderbook_depth"
    fixed_slippage_bps: float = Field(default=5.0, ge=0)
    fee_model: Literal["exchange_tiered", "fixed_bps"] = "exchange_tiered"
    fixed_fee_bps: float = Field(default=10.0, ge=0)


class AppConfig(BaseModel):
    environment: Literal["development", "staging", "production"] = "development"
    base_currency: str = "USDT"
    exchanges: list[ExchangeConfig] = Field(default_factory=list)
    data: DataLayerConfig = Field(default_factory=DataLayerConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)

    @field_validator("exchanges")
    @classmethod
    def _unique_exchange_ids(cls, v: list[ExchangeConfig]) -> list[ExchangeConfig]:
        ids = [e.id for e in v]
        if len(ids) != len(set(ids)):
            raise ValueError("exchange ids must be unique")
        return v


def load_config(path: str | Path = "config/config.yaml") -> AppConfig:
    """Load and validate config.yaml. Raises pydantic.ValidationError with a
    precise field path on any invalid value — never silently coerces."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)


def is_live_trading_authorized(config: AppConfig) -> bool:
    """Live/sandbox execution requires BOTH config.execution.mode != paper
    AND a matching environment variable set to an exact confirmation
    string. This is deliberately redundant: a YAML edit alone, or an env
    var alone, is never sufficient to enable real-money order placement."""
    import os

    if config.execution.mode == ExecutionMode.PAPER:
        return False
    confirmation = os.environ.get(config.execution.live_trading_confirmation_env)
    return confirmation == config.execution.live_trading_confirmation_value
