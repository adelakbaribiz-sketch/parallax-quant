"""Live/sandbox CEX order placement via ccxt. Refuses to construct unless
config.is_live_trading_authorized() returns True — i.e. execution.mode is
"sandbox" or "live" in config.yaml AND the PARALLAX_ENABLE_LIVE_TRADING
env var is set to the exact confirmation string. There is no other path
to real order placement in this codebase.

This adapter has NOT been exercised against a funded live account as part
of this build — see docs/LIMITATIONS.md. Test against an exchange's
sandbox/testnet (config.yaml exchanges[].sandbox: true) extensively before
ever pointing it at a funded account.
"""

from __future__ import annotations

import os

import ccxt.async_support as ccxt
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from parallax_quant.config import AppConfig, ExchangeConfig, is_live_trading_authorized
from parallax_quant.execution.base import ExecutionAdapter, OrderResult
from parallax_quant.strategies.base import Position

logger = structlog.get_logger(__name__)


class LiveCexExecutionAdapter(ExecutionAdapter):
    def __init__(self, app_config: AppConfig, exchange_config: ExchangeConfig) -> None:
        if not is_live_trading_authorized(app_config):
            raise RuntimeError(
                "Live/sandbox execution was requested but is not authorized. "
                "Set execution.mode to 'sandbox' or 'live' in config.yaml AND "
                f"export {app_config.execution.live_trading_confirmation_env}="
                f"{app_config.execution.live_trading_confirmation_value}. "
                "Read docs/LIMITATIONS.md and docs/COMPLIANCE.md first."
            )
        self.app_config = app_config
        self.exchange_config = exchange_config
        exchange_cls = getattr(ccxt, exchange_config.id)
        self._client = exchange_cls(
            {
                "apiKey": os.environ.get(exchange_config.api_key_env or "", ""),
                "secret": os.environ.get(exchange_config.api_secret_env or "", ""),
                "enableRateLimit": True,
            }
        )
        if exchange_config.sandbox and hasattr(self._client, "set_sandbox_mode"):
            self._client.set_sandbox_mode(True)
        logger.warning(
            "live_execution_adapter_initialized",
            exchange=exchange_config.id,
            mode=app_config.execution.mode.value,
            sandbox=exchange_config.sandbox,
        )

    async def close(self) -> None:
        await self._client.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8))
    async def place_order(
        self, exchange: str, symbol: str, side: str, order_type: str, amount: float, price: float | None = None
    ) -> OrderResult:
        try:
            if order_type == "market":
                raw = await self._client.create_market_order(symbol, side, amount)
            else:
                if price is None:
                    return OrderResult(filled=False, filled_amount=0.0, avg_fill_price=0.0, fee_usd=0.0, error="limit_order_requires_price")
                raw = await self._client.create_limit_order(symbol, side, amount, price)
        except Exception as exc:  # noqa: BLE001 - surfaced to caller, not swallowed
            logger.error("live_order_failed", exchange=exchange, symbol=symbol, side=side, error=str(exc))
            return OrderResult(filled=False, filled_amount=0.0, avg_fill_price=0.0, fee_usd=0.0, error=str(exc))

        filled_amount = float(raw.get("filled") or 0.0)
        avg_price = float(raw.get("average") or raw.get("price") or 0.0)
        fee_info = raw.get("fee") or {}
        fee_usd = float(fee_info.get("cost") or 0.0)
        return OrderResult(
            filled=filled_amount > 0,
            filled_amount=filled_amount,
            avg_fill_price=avg_price,
            fee_usd=fee_usd,
            order_id=raw.get("id"),
        )

    async def close_position(self, position: Position) -> OrderResult:
        side = "sell" if position.direction.value == "long" else "buy"
        return await self.place_order(
            exchange=self.exchange_config.id,
            symbol=position.symbol,
            side=side,
            order_type="market",
            amount=position.notional_usd / max(position.entry_price, 1e-9),
        )
