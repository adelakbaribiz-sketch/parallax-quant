"""DEX price data via web3.py: Chainlink oracle reads and AMM pool
reserves. Implements Uniswap V2-style constant-product pools directly
(getReserves); V3 concentrated-liquidity tick math is a documented
extension point (see NotImplementedError below) rather than faked."""

from __future__ import annotations

import os

import structlog
from web3 import Web3

from parallax_quant.config import ExchangeConfig
from parallax_quant.data.models import Tick
from parallax_quant.data.timesync import TimeSync

logger = structlog.get_logger(__name__)

# Minimal ABIs — only the read methods this module actually calls.
CHAINLINK_AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]

UNISWAP_V2_PAIR_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "reserve0", "type": "uint112"},
            {"name": "reserve1", "type": "uint112"},
            {"name": "blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class ChainlinkOracle:
    def __init__(self, w3: Web3, aggregator_address: str) -> None:
        self.contract = w3.eth.contract(address=Web3.to_checksum_address(aggregator_address), abi=CHAINLINK_AGGREGATOR_ABI)
        self._decimals: int | None = None

    async def latest_price(self) -> float:
        if self._decimals is None:
            self._decimals = self.contract.functions.decimals().call()
        _, answer, _, _updated_at, _ = self.contract.functions.latestRoundData().call()
        return answer / (10**self._decimals)


class UniswapV2Pool:
    """Constant-product pool: price = reserve1 / reserve0 (token1 per token0),
    adjusted for each token's decimals by the caller."""

    def __init__(self, w3: Web3, pair_address: str, decimals0: int, decimals1: int) -> None:
        self.contract = w3.eth.contract(address=Web3.to_checksum_address(pair_address), abi=UNISWAP_V2_PAIR_ABI)
        self.decimals0 = decimals0
        self.decimals1 = decimals1

    async def spot_price(self) -> float:
        r0, r1, _ = self.contract.functions.getReserves().call()
        adj_r0 = r0 / (10**self.decimals0)
        adj_r1 = r1 / (10**self.decimals1)
        if adj_r0 == 0:
            return 0.0
        return adj_r1 / adj_r0

    async def price_impact(self, amount_in_token0: float) -> float:
        """Constant-product slippage: dy = y - k / (x + dx)."""
        r0, r1, _ = self.contract.functions.getReserves().call()
        x = r0 / (10**self.decimals0)
        y = r1 / (10**self.decimals1)
        if x == 0 or y == 0:
            return 0.0
        k = x * y
        new_x = x + amount_in_token0
        new_y = k / new_x
        out = y - new_y
        spot = y / x
        effective = out / amount_in_token0 if amount_in_token0 else spot
        return abs(spot - effective) / spot * 100


class UniswapV3Pool:
    def __init__(self, *_: object, **__: object) -> None:
        raise NotImplementedError(
            "V3 concentrated-liquidity tick math is not implemented in this build. "
            "Use UniswapV2Pool-style venues, or implement tick-to-price conversion "
            "against the V3 pool's slot0()/liquidity() before enabling V3 pairs in "
            "config.yaml's dex_cex_flashloan.protocols."
        )


class DexFeed:
    def __init__(self, config: ExchangeConfig, timesync: TimeSync) -> None:
        self.config = config
        self.timesync = timesync
        rpc_url = os.environ.get(config.rpc_url_env) if config.rpc_url_env else None
        if not rpc_url:
            raise ValueError(f"DEX feed '{config.id}' has no RPC URL set (env: {config.rpc_url_env})")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))

    async def pool_tick(self, symbol: str, pool: UniswapV2Pool) -> Tick:
        price = await pool.spot_price()
        # DEX pools don't expose a bid/ask spread directly; approximate the
        # effective spread from a small trade's price impact in both
        # directions so downstream code has a comparable Tick shape.
        impact = await pool.price_impact(amount_in_token0=1.0)
        half_spread = price * (impact / 100) / 2
        return Tick(
            exchange=self.config.id,
            symbol=symbol,
            bid=price - half_spread,
            ask=price + half_spread,
            bid_volume=0.0,  # AMMs have no discrete book depth — see OrderBook.depth_to_consume for CEX-only use
            ask_volume=0.0,
            timestamp=self.timesync.now(),
        )
