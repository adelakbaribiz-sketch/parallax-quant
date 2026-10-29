from parallax_quant.config import RiskConfig
from parallax_quant.risk.position_limits import check_drawdown, check_position_size


def make_config(**overrides):
    return RiskConfig(**overrides)


def test_rejects_when_asset_exposure_exceeds_limit():
    config = make_config(max_pct_capital_per_asset=5.0)
    result = check_position_size(
        proposed_notional_usd=6_000,
        asset="BTC",
        exchange="binance",
        current_asset_exposure_usd={},
        current_exchange_exposure_usd={},
        total_capital_usd=100_000,
        config=config,
    )
    assert result.allowed is False
    assert "asset_exposure" in result.reason


def test_allows_when_within_all_limits():
    config = make_config(max_pct_capital_per_asset=5.0, max_pct_capital_per_exchange=10.0)
    result = check_position_size(
        proposed_notional_usd=3_000,
        asset="ETH",
        exchange="okx",
        current_asset_exposure_usd={},
        current_exchange_exposure_usd={},
        total_capital_usd=100_000,
        config=config,
    )
    assert result.allowed is True


def test_rejects_when_exchange_exposure_exceeds_limit_even_if_asset_ok():
    config = make_config(max_pct_capital_per_asset=50.0, max_pct_capital_per_exchange=10.0)
    result = check_position_size(
        proposed_notional_usd=2_000,
        asset="ETH",
        exchange="okx",
        current_asset_exposure_usd={},
        current_exchange_exposure_usd={"okx": 9_000},
        total_capital_usd=100_000,
        config=config,
    )
    assert result.allowed is False
    assert "exchange_exposure" in result.reason


def test_drawdown_breach_detected():
    config = make_config(portfolio_max_drawdown_pct=10.0)
    result = check_drawdown(current_equity_usd=88_000, peak_equity_usd=100_000, config=config)
    assert result.allowed is False


def test_drawdown_within_limit_allowed():
    config = make_config(portfolio_max_drawdown_pct=10.0)
    result = check_drawdown(current_equity_usd=95_000, peak_equity_usd=100_000, config=config)
    assert result.allowed is True


def test_drawdown_with_zero_peak_never_divides_by_zero():
    config = make_config()
    result = check_drawdown(current_equity_usd=0, peak_equity_usd=0, config=config)
    assert result.allowed is True
