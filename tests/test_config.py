import pytest
from pydantic import ValidationError

from arbitragex_engine.config import (
    AppConfig,
    ExecutionConfig,
    ExecutionMode,
    StatArbConfig,
    StrategiesConfig,
    is_live_trading_authorized,
    load_config,
)


def test_default_config_loads_from_yaml():
    config = load_config("config/config.yaml")
    assert config.execution.mode == ExecutionMode.PAPER
    assert len(config.exchanges) > 0


def test_zscore_entry_must_be_positive():
    with pytest.raises(ValidationError):
        StatArbConfig(entry_zscore=-1.0)


def test_cointegration_confidence_must_be_between_0_and_1():
    with pytest.raises(ValidationError):
        StatArbConfig(cointegration_confidence=1.5)


def test_duplicate_exchange_ids_rejected():
    with pytest.raises(ValidationError):
        AppConfig(exchanges=[{"id": "binance", "kind": "cex"}, {"id": "binance", "kind": "cex"}])


def test_strategy_allocations_summing_over_100_rejected():
    with pytest.raises(ValidationError):
        StrategiesConfig(
            stat_arb=StatArbConfig(allocation_pct_max=40),
            funding_rate={"allocation_pct_max": 40},
            triangular={"allocation_pct_max": 15},
            dex_cex_flashloan={"allocation_pct_max": 15},
        )


def test_strategy_allocations_within_100_accepted():
    config = StrategiesConfig(
        stat_arb=StatArbConfig(allocation_pct_max=30),
        funding_rate={"allocation_pct_max": 30},
        triangular={"allocation_pct_max": 15},
        dex_cex_flashloan={"allocation_pct_max": 15},
    )
    assert config.stat_arb.allocation_pct_max == 30


def test_live_trading_not_authorized_by_default(monkeypatch):
    config = AppConfig(execution=ExecutionConfig(mode=ExecutionMode.PAPER))
    assert is_live_trading_authorized(config) is False


def test_live_trading_not_authorized_without_env_var(monkeypatch):
    monkeypatch.delenv("ARBITRAGEX_ENABLE_LIVE_TRADING", raising=False)
    config = AppConfig(execution=ExecutionConfig(mode=ExecutionMode.LIVE))
    assert is_live_trading_authorized(config) is False


def test_live_trading_not_authorized_with_wrong_env_value(monkeypatch):
    monkeypatch.setenv("ARBITRAGEX_ENABLE_LIVE_TRADING", "yes_please")
    config = AppConfig(execution=ExecutionConfig(mode=ExecutionMode.LIVE))
    assert is_live_trading_authorized(config) is False


def test_live_trading_authorized_with_correct_mode_and_env_var(monkeypatch):
    monkeypatch.setenv("ARBITRAGEX_ENABLE_LIVE_TRADING", "I_UNDERSTAND_THE_RISK")
    config = AppConfig(execution=ExecutionConfig(mode=ExecutionMode.LIVE))
    assert is_live_trading_authorized(config) is True


def test_sandbox_mode_also_requires_env_var(monkeypatch):
    monkeypatch.delenv("ARBITRAGEX_ENABLE_LIVE_TRADING", raising=False)
    config = AppConfig(execution=ExecutionConfig(mode=ExecutionMode.SANDBOX))
    assert is_live_trading_authorized(config) is False
