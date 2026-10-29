"""Entry point: loads config.yaml, builds strategies against the
appropriate execution adapter (paper unless live trading is explicitly
authorized — see config.is_live_trading_authorized), and runs the
orchestrator until interrupted.

    python -m parallax_quant.main --config config/config.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import structlog

from parallax_quant.agents.orchestrator import Orchestrator
from parallax_quant.config import AppConfig, is_live_trading_authorized, load_config
from parallax_quant.monitoring.metrics_exporter import start_metrics_server


def configure_logging(config: AppConfig) -> None:
    level = getattr(logging, config.monitoring.log_level)
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    renderer = structlog.processors.JSONRenderer() if config.monitoring.log_format == "json" else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
    )


def build_strategies(config: AppConfig) -> list:
    """Wires each enabled strategy to a paper execution adapter by
    default. Live/sandbox adapters are only constructed when
    is_live_trading_authorized(config) is True — see execution/live_cex.py
    and execution/live_dex.py, which independently refuse to construct
    otherwise. This function does not decide authorization itself; it
    only reads the result."""
    strategies: list = []
    logger = structlog.get_logger(__name__)

    if is_live_trading_authorized(config):
        logger.warning(
            "LIVE_TRADING_AUTHORIZED",
            mode=config.execution.mode.value,
            warning="Real orders may be placed. This is not a drill.",
        )
    else:
        logger.info("paper_trading_mode", reason="execution.mode=paper or live-trading env var not set")

    # Strategy construction wiring (data sources, execution adapters) is
    # necessarily specific to which exchanges/symbols are enabled in
    # config.yaml — see docs/DEPLOYMENT.md for the concrete wiring example
    # and tests/ for constructed-in-isolation examples of each strategy.
    return strategies


async def async_main(config_path: str) -> None:
    config = load_config(config_path)
    configure_logging(config)
    logger = structlog.get_logger(__name__)

    start_metrics_server(config.monitoring.prometheus_port)
    logger.info("metrics_server_started", port=config.monitoring.prometheus_port)

    strategies = build_strategies(config)
    if not strategies:
        logger.warning(
            "no_strategies_wired",
            hint="build_strategies() in main.py is a wiring stub — see docs/DEPLOYMENT.md to connect real data/execution adapters for your enabled exchanges before running live.",
        )

    orchestrator = Orchestrator(
        strategies=strategies,
        agents_config=config.agents,
        risk_config=config.risk,
        exposure_provider=lambda: ({}, {}, config.execution.paper_starting_capital_usd),
        equity_provider=lambda: (config.execution.paper_starting_capital_usd, config.execution.paper_starting_capital_usd),
        positions_provider=list,
    )
    await orchestrator.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Parallax Quant")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()
    asyncio.run(async_main(args.config))


if __name__ == "__main__":
    main()
