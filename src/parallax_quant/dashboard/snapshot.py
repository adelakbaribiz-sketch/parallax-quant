"""Builds the research-console snapshot by running the engine's *real*
strategy, risk, and config code against a seeded synthetic market.

Nothing in here re-implements a formula: Z-scores come from
StatArbStrategy.scan(), cycles from TriangularArbStrategy.scan() (Bellman-
Ford on the rate graph), funding economics from
FundingRateArbStrategy.evaluate(), VaR/CVaR from risk.var, stress P&L from
risk.stress_test. The only thing synthetic is the market data itself, which
is generated from `seed` so every render is reproducible. No exchange is
contacted and no order is placed — execution adapters here are inert
recorders.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from parallax_quant.config import AppConfig, is_live_trading_authorized, load_config
from parallax_quant.data.models import FundingRate, OrderBook, OrderBookLevel
from parallax_quant.risk.correlation import (
    compute_correlation_matrix,
    effective_independent_count,
    find_correlated_pairs,
)
from parallax_quant.risk.stress_test import run_stress_tests
from parallax_quant.risk.var import monte_carlo_var
from parallax_quant.strategies.base import Position, SignalDirection
from parallax_quant.strategies.cointegration import engle_granger_test, johansen_test
from parallax_quant.strategies.funding_rate import (
    FundingRateArbStrategy,
    breakeven_hours,
    liquidation_price_short,
)
from parallax_quant.strategies.stat_arb import StatArbStrategy, _zscore
from parallax_quant.strategies.triangular import LegFill, TriangularArbStrategy

# Reference USD prices the synthetic market is built around. Labelled as
# synthetic everywhere they surface in the UI.
_REFERENCE_USD = {"USDT": 1.0, "BTC": 64_250.0, "ETH": 3_180.0, "SOL": 148.0}
_STAT_ARB_PAIR = ("ETH/USDT", "STETH/USDT")
_FUNDING_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT"]
_FUNDING_BASE_RATES_PCT = [0.012, 0.034, 0.071, -0.009, 0.004, 0.118]  # per 8h interval


# --- inert in-memory sources / adapters ------------------------------------


class _CursorPriceSource:
    def __init__(self, series: dict[str, pd.Series], start: int) -> None:
        self.series = series
        self.cursor = start

    async def get_price_series(self, symbol: str, periods: int) -> pd.Series:
        s = self.series[symbol]
        return s.iloc[max(0, self.cursor - periods) : self.cursor]

    async def get_latest_price(self, symbol: str) -> float:
        return float(self.series[symbol].iloc[self.cursor - 1])


class _BookSource:
    def __init__(self, books: dict[str, OrderBook]) -> None:
        self.books = books

    async def get_order_book(self, exchange: str, symbol: str) -> OrderBook:
        return self.books[symbol]


class _FundingSource:
    def __init__(self, rates_pct: dict[str, float], marks: dict[str, float]) -> None:
        self.rates_pct = rates_pct
        self.marks = marks

    async def get_funding_rate(self, exchange: str, symbol: str) -> FundingRate:
        return FundingRate(exchange=exchange, symbol=symbol, rate=self.rates_pct[symbol] / 100, interval_hours=8.0,
                           next_funding_timestamp=0, timestamp=0)

    async def get_spot_price(self, exchange: str, symbol: str) -> float:
        return self.marks[symbol]

    async def get_mark_price(self, exchange: str, symbol: str) -> float:
        return self.marks[symbol]


class _NoopExecution:
    """Satisfies every strategy's execution Protocol without doing anything.
    The snapshot only calls scan/evaluate, never execute."""

    async def open_pair_position(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("snapshot never executes")

    async def close_position(self, position: Position) -> None:
        return None

    async def place_limit_order(self, *args: Any, **kwargs: Any) -> LegFill:
        raise RuntimeError("snapshot never executes")

    async def place_market_order(self, *args: Any, **kwargs: Any) -> LegFill:
        raise RuntimeError("snapshot never executes")


# --- sections ----------------------------------------------------------------


async def _stat_arb_section(config: AppConfig, rng: np.random.Generator) -> dict[str, Any]:
    cfg = config.strategies.stat_arb
    lookback = cfg.zscore_lookback_periods
    n_live = 360
    n = lookback + n_live

    common = np.cumsum(rng.normal(0, 6.0, n)) + 3_180.0
    spread = np.zeros(n)
    for t in range(1, n):  # Ornstein-Uhlenbeck spread so the pair actually mean-reverts
        spread[t] = spread[t - 1] * 0.9 + rng.normal(0, 2.6)
    a = pd.Series(common + rng.normal(0, 1.5, n))
    b = pd.Series(0.998 * common + spread)
    symbol_a, symbol_b = _STAT_ARB_PAIR
    source = _CursorPriceSource({symbol_a: a, symbol_b: b}, start=lookback)

    eg = engle_granger_test(a.iloc[:lookback], b.iloc[:lookback], cfg.engle_granger_max_lag, cfg.lag_selection_criterion)
    jh = johansen_test(pd.concat([a.iloc[:lookback], b.iloc[:lookback]], axis=1), cfg.cointegration_confidence)

    strategy = StatArbStrategy(config=cfg, symbols=[symbol_a, symbol_b], price_source=source,
                               execution=_NoopExecution(), capital_usd=config.execution.paper_starting_capital_usd)
    selected = await strategy.select_cointegrated_pairs()

    steps: list[dict[str, float | None]] = []
    signals: list[dict[str, Any]] = []
    state = strategy._pairs.get((symbol_a, symbol_b))
    for step in range(n_live):
        source.cursor = lookback + step + 1
        opportunities = await strategy.scan()
        if state is None:
            break
        z = _zscore(state.spread_history) if len(state.spread_history) >= 30 else None
        steps.append({
            "t": step,
            "a": round(float(a.iloc[source.cursor - 1]), 2),
            "b": round(float(b.iloc[source.cursor - 1]), 2),
            "beta": round(state.hedge_ratio_filter.beta, 5),
            "spread": round(state.spread_history[-1], 3),
            "z": None if z is None else round(z, 3),
        })
        for opp in opportunities:
            ev = await strategy.evaluate(opp)
            signals.append({
                "t": step,
                "direction": opp.direction.value,
                "zscore": round(opp.metadata["zscore"], 3),
                "net_edge_pct": round(ev.net_edge_pct, 4),
                "confidence": round(ev.confidence, 3),
                "notional_usd": ev.recommended_notional_usd,
                "actionable": ev.is_actionable,
                "reject_reason": ev.reject_reason,
            })

    return {
        "pair": [symbol_a, symbol_b],
        "selected": [list(p) for p in selected],
        "engle_granger": {"p_value": eg.p_value, "statistic": eg.test_statistic, "critical_95": eg.critical_value_95,
                          "cointegrated": bool(eg.is_cointegrated)},
        "johansen": {"statistic": jh.test_statistic, "critical_95": jh.critical_value_95, "cointegrated": bool(jh.is_cointegrated)},
        "thresholds": {"entry": cfg.entry_zscore, "exit": cfg.exit_zscore, "stop": cfg.stop_loss_sigma},
        "steps": steps,
        "signals": signals,
    }


async def _triangular_section(config: AppConfig, rng: np.random.Generator) -> dict[str, Any]:
    cfg = config.strategies.triangular
    currencies = list(_REFERENCE_USD)
    usd = {c: p * (1 + rng.normal(0, 0.0004)) if c != "USDT" else p for c, p in _REFERENCE_USD.items()}
    half_spread = 0.00025
    # The ETH/SOL cross market trades off its USD-implied value (both
    # directions, so there's no trivial two-leg round trip) — that
    # inconsistency is the only thing that can open a three-leg loop.
    cross_dislocation = 0.0062 + abs(rng.normal(0, 0.0008))
    books: dict[str, OrderBook] = {}
    for x in currencies:
        for y in currencies:
            if x == y:
                continue
            fair = usd[y] / usd[x]  # units of x per 1 y
            ask = fair * (1 + half_spread)
            if (x, y) == ("ETH", "SOL"):
                ask *= 1 - cross_dislocation
            elif (x, y) == ("SOL", "ETH"):
                ask *= 1 + cross_dislocation
            books[f"{x}/{y}"] = OrderBook(exchange="synthetic", symbol=f"{x}/{y}", bids=(),
                                          asks=(OrderBookLevel(price=ask, volume=40_000 / usd[y]),), timestamp=0)

    taker_fee_pct = 0.075
    strategy = TriangularArbStrategy(config=cfg, exchange="synthetic", currencies=currencies,
                                     orderbook_source=_BookSource(books), execution=_NoopExecution(),
                                     capital_usd=config.execution.paper_starting_capital_usd, taker_fee_pct=taker_fee_pct)
    await strategy.refresh_order_books()
    graph = await strategy._build_graph()
    opportunities = await strategy.scan()

    matrix = [[None if x == y else graph._edges[x][y] for y in currencies] for x in currencies]
    # Fair-value deviation of each edge, in bps: how far the quoted rate
    # sits from the reference cross rate (spread + any mispricing).
    deviation = [[None if x == y else round((graph._edges[x][y] / (usd[x] / usd[y]) - 1) * 10_000, 2) for y in currencies]
                 for x in currencies]

    cycles = []
    for opp in opportunities:
        ev = await strategy.evaluate(opp)
        cycles.append({
            "path": opp.metadata["cycle"],
            "gross_edge_pct": round(opp.raw_edge_pct, 4),
            "net_multiplier": opp.metadata["net_multiplier"],
            "net_edge_pct": round(ev.net_edge_pct, 4),
            "fees_pct": round(ev.fees_pct, 4),
            "notional": round(ev.recommended_notional_usd, 2),
            "actionable": ev.is_actionable,
            "reject_reason": ev.reject_reason,
        })
    return {
        "currencies": currencies,
        "rates": matrix,
        "deviation_bps": deviation,
        "taker_fee_pct": taker_fee_pct,
        "min_profit_after_fees_pct": cfg.min_profit_after_fees_pct,
        "cycles": cycles,
    }


async def _funding_section(config: AppConfig, rng: np.random.Generator) -> dict[str, Any]:
    cfg = config.strategies.funding_rate
    rates = {s: round(r + rng.normal(0, 0.003), 4) for s, r in zip(_FUNDING_SYMBOLS, _FUNDING_BASE_RATES_PCT, strict=True)}
    marks = {s: 100.0 for s in _FUNDING_SYMBOLS}
    spot_fee_pct, futures_fee_pct = 0.075, 0.04
    strategy = FundingRateArbStrategy(config=cfg, exchange="synthetic", symbols=_FUNDING_SYMBOLS,
                                      source=_FundingSource(rates, marks), execution=_NoopExecution(),
                                      capital_usd=config.execution.paper_starting_capital_usd,
                                      spot_fee_pct=spot_fee_pct, futures_fee_pct=futures_fee_pct)
    by_symbol = {o.symbol: o for o in await strategy.scan()}
    rows = []
    for symbol in _FUNDING_SYMBOLS:
        row: dict[str, Any] = {"symbol": symbol, "rate_pct_8h": rates[symbol], "annualized_pct": round(rates[symbol] * 3 * 365, 2)}
        opp = by_symbol.get(symbol)
        if opp is None:
            row.update(status="skipped", reason="funding_non_positive")
        else:
            ev = await strategy.evaluate(opp)
            row.update(
                status="actionable" if ev.is_actionable else "rejected",
                reason=ev.reject_reason,
                net_edge_pct=round(ev.net_edge_pct, 4),
                breakeven_h=round(breakeven_hours(ev.fees_pct, ev.slippage_pct, rates[symbol], 8.0), 1),
                notional_usd=ev.recommended_notional_usd,
                confidence=round(ev.confidence, 3),
            )
        rows.append(row)
    liq = liquidation_price_short(100.0, cfg.leverage_min, strategy.maintenance_margin_rate)
    return {
        "rows": rows,
        "fees": {"spot_pct": spot_fee_pct, "futures_pct": futures_fee_pct},
        "breakeven_warning_h": cfg.breakeven_warning_hours,
        "leverage": [cfg.leverage_min, cfg.leverage_max],
        "liquidation_example": {"entry": 100.0, "leverage": cfg.leverage_min, "liq_price": round(liq, 3)},
    }


def _risk_section(config: AppConfig, rng: np.random.Generator, funding: dict[str, Any]) -> dict[str, Any]:
    risk = config.risk
    equity = config.execution.paper_starting_capital_usd
    # The candidate book: what the strategies above would hold if every
    # actionable signal were sized at the risk layer's per-asset cap.
    cap = equity * risk.max_pct_capital_per_asset / 100
    book = [("stat_arb", "ETH/STETH", cap * 0.8, {}), ("triangular", "USDT>ETH>SOL", cap * 0.35, {})]
    book += [("funding_rate", r["symbol"], cap, {}) for r in funding["rows"] if r["status"] == "actionable"]
    book.append(("dex_cex_flashloan", "WETH/USDC", cap * 0.25, {"chain": "ethereum"}))
    positions = [Position(strategy=s, symbol=sym, direction=SignalDirection.LONG, notional_usd=v, entry_price=1.0,
                          opened_at=0.0, metadata=m) for s, sym, v, m in book]

    n_days = 180
    names = [p.symbol for p in positions]
    vols = np.array([0.006, 0.004] + [0.003] * (len(positions) - 3) + [0.009])
    factor = rng.normal(0, 1, n_days)
    loadings = np.linspace(0.93, 0.25, len(positions))
    shocks = rng.normal(0, 1, (n_days, len(positions)))
    returns = (factor[:, None] * loadings + shocks * np.sqrt(1 - loadings**2)) * vols + 0.0002
    returns_df = pd.DataFrame(returns, columns=names)

    values = np.array([p.notional_usd for p in positions])
    cov = np.cov(returns, rowvar=False)
    mean = returns.mean(axis=0)
    var_grid = []
    for confidence in (0.95, 0.975, 0.99):
        for horizon in (1, 5, 10):
            r = monte_carlo_var(values, cov, mean, confidence, horizon, risk.var_monte_carlo_paths, seed=7)
            var_grid.append({"confidence": confidence, "horizon_days": horizon, "var_usd": round(r.var_usd, 2),
                             "cvar_usd": round(r.cvar_usd, 2)})
    headline = monte_carlo_var(values, cov, mean, risk.var_confidence_level, risk.var_time_horizon_days,
                               risk.var_monte_carlo_paths, seed=7)

    corr = compute_correlation_matrix(returns_df)
    findings = find_correlated_pairs(corr, risk.correlation_reduction_threshold, risk.correlation_weight_reduction_pct)
    stress = run_stress_tests(risk.stress_test_scenarios, positions, equity, risk.portfolio_max_drawdown_pct)

    portfolio_returns = returns @ values
    return {
        "equity_usd": equity,
        "gross_exposure_usd": float(values.sum()),
        "positions": [{"strategy": p.strategy, "symbol": p.symbol, "notional_usd": round(p.notional_usd, 2)} for p in positions],
        "var": {"confidence": headline.confidence_level, "horizon_days": headline.horizon_days, "paths": headline.paths,
                "var_usd": round(headline.var_usd, 2), "cvar_usd": round(headline.cvar_usd, 2)},
        "var_grid": var_grid,
        "pnl_history": [round(float(x), 2) for x in portfolio_returns],
        "correlation": {"labels": names, "matrix": [[round(float(v), 3) for v in row] for row in corr.to_numpy()],
                        "threshold": risk.correlation_reduction_threshold,
                        "effective_independent_bets": round(effective_independent_count(corr), 2),
                        "findings": [{"a": f.asset_a, "b": f.asset_b, "correlation": round(f.correlation, 3),
                                      "weight_multiplier": f.recommended_weight_multiplier} for f in findings]},
        "stress": [{"scenario": s.scenario, "pnl_usd": round(s.simulated_pnl_usd, 2),
                    "surviving_equity_usd": round(s.surviving_equity_usd, 2), "breach": s.breaches_drawdown_limit}
                   for s in stress],
        "max_drawdown_pct": risk.portfolio_max_drawdown_pct,
    }


def _config_section(config: AppConfig, config_source: str) -> dict[str, Any]:
    s = config.strategies
    return {
        "source": config_source,
        "mode": config.execution.mode.value,
        "live_trading_authorized": is_live_trading_authorized(config),
        "live_trading_env": config.execution.live_trading_confirmation_env,
        "exchanges": [{"id": e.id, "enabled": e.enabled, "sandbox": e.sandbox} for e in config.exchanges],
        "allocations": [
            {"strategy": "stat_arb", "enabled": s.stat_arb.enabled, "min": s.stat_arb.allocation_pct_min, "max": s.stat_arb.allocation_pct_max},
            {"strategy": "triangular", "enabled": s.triangular.enabled, "min": s.triangular.allocation_pct_min, "max": s.triangular.allocation_pct_max},
            {"strategy": "funding_rate", "enabled": s.funding_rate.enabled, "min": s.funding_rate.allocation_pct_min, "max": s.funding_rate.allocation_pct_max},
            {"strategy": "dex_cex_flashloan", "enabled": s.dex_cex_flashloan.enabled, "min": s.dex_cex_flashloan.allocation_pct_min,
             "max": s.dex_cex_flashloan.allocation_pct_max},
        ],
        "message_bus": config.agents.message_bus.backend,
    }


def load_config_or_defaults(path: str | Path) -> tuple[AppConfig, str]:
    p = Path(path)
    if p.is_file():
        return load_config(p), p.name
    return AppConfig(), "built-in defaults (config file not found)"


async def build_snapshot(config_path: str | Path = "config/config.yaml", seed: int = 8) -> dict[str, Any]:
    config, source = load_config_or_defaults(config_path)
    rng = np.random.default_rng(seed)
    started = time.perf_counter()
    stat_arb = await _stat_arb_section(config, rng)
    triangular = await _triangular_section(config, rng)
    funding = await _funding_section(config, rng)
    risk = _risk_section(config, rng, funding)
    return {
        "meta": {"seed": seed, "generated_at": time.time(), "compute_ms": round((time.perf_counter() - started) * 1000, 1),
                 "data": "synthetic", "note": "Seeded synthetic market. No exchange connection, no orders placed."},
        "config": _config_section(config, source),
        "stat_arb": stat_arb,
        "triangular": triangular,
        "funding": funding,
        "risk": risk,
    }
