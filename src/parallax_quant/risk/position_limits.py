"""Hard position-size and drawdown limits, checked before every order and
on every risk-monitor tick. These are the last line of defense — even if
a strategy's own sizing logic is wrong, these limits cap the blast radius."""

from __future__ import annotations

from dataclasses import dataclass

from parallax_quant.config import RiskConfig


@dataclass
class LimitCheckResult:
    allowed: bool
    reason: str | None = None


def check_position_size(
    proposed_notional_usd: float,
    asset: str,
    exchange: str,
    current_asset_exposure_usd: dict[str, float],
    current_exchange_exposure_usd: dict[str, float],
    total_capital_usd: float,
    config: RiskConfig,
) -> LimitCheckResult:
    asset_after = current_asset_exposure_usd.get(asset, 0.0) + proposed_notional_usd
    if total_capital_usd > 0 and (asset_after / total_capital_usd * 100) > config.max_pct_capital_per_asset:
        return LimitCheckResult(False, f"asset_exposure_would_exceed_{config.max_pct_capital_per_asset}pct_limit")

    exchange_after = current_exchange_exposure_usd.get(exchange, 0.0) + proposed_notional_usd
    if total_capital_usd > 0 and (exchange_after / total_capital_usd * 100) > config.max_pct_capital_per_exchange:
        return LimitCheckResult(False, f"exchange_exposure_would_exceed_{config.max_pct_capital_per_exchange}pct_limit")

    return LimitCheckResult(True)


def check_drawdown(current_equity_usd: float, peak_equity_usd: float, config: RiskConfig) -> LimitCheckResult:
    if peak_equity_usd <= 0:
        return LimitCheckResult(True)
    drawdown_pct = (peak_equity_usd - current_equity_usd) / peak_equity_usd * 100
    if drawdown_pct >= config.portfolio_max_drawdown_pct:
        return LimitCheckResult(False, f"drawdown_{drawdown_pct:.2f}pct_at_or_beyond_{config.portfolio_max_drawdown_pct}pct_limit")
    return LimitCheckResult(True)
