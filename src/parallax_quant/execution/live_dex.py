"""Live flash-loan execution: calls the deployed FlashArbReceiver contract
(contracts/FlashArbReceiver.sol) via web3.py, optionally submitting through
Flashbots Protect's private-transaction RPC so the arbitrage tx isn't
visible in the public mempool before inclusion.

Like execution/live_cex.py, this refuses to construct unless
config.is_live_trading_authorized() is True. Additionally requires
FLASH_ARB_CONTRACT_ADDRESS to point at an already-deployed instance of
FlashArbReceiver.sol — this module does not deploy contracts.

NOT exercised against mainnet as part of this build. See docs/LIMITATIONS.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog
from eth_account import Account
from web3 import Web3
from web3.types import TxParams

from parallax_quant.config import AppConfig, ExchangeConfig, is_live_trading_authorized
from parallax_quant.strategies.dex_cex_flashloan import FlashLoanResult

logger = structlog.get_logger(__name__)

FLASH_ARB_RECEIVER_ABI = [
    {
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "router", "type": "address"},
            {"name": "tokenOut", "type": "address"},
            {"name": "minAmountOut", "type": "uint256"},
        ],
        "name": "requestFlashLoan",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

FLASHBOTS_PROTECT_RPC = "https://rpc.flashbots.net"


class LiveDexFlashLoanAdapter:
    def __init__(self, app_config: AppConfig, exchange_config: ExchangeConfig) -> None:
        if not is_live_trading_authorized(app_config):
            raise RuntimeError(
                "Live DEX execution was requested but is not authorized. See "
                "execution/live_cex.py's error message for the exact steps — "
                "the same two-part authorization applies here."
            )
        rpc_url = os.environ.get(exchange_config.rpc_url_env or "", "")
        if not rpc_url:
            raise ValueError(f"Env var {exchange_config.rpc_url_env} is not set")

        contract_address = os.environ.get("FLASH_ARB_CONTRACT_ADDRESS")
        if not contract_address:
            raise RuntimeError(
                "FLASH_ARB_CONTRACT_ADDRESS is not set. Deploy contracts/FlashArbReceiver.sol "
                "first (see docs/DEPLOYMENT.md) — this adapter calls an already-deployed contract, "
                "it does not deploy one."
            )

        private_key = os.environ.get("EXECUTION_WALLET_PRIVATE_KEY")
        if not private_key:
            raise RuntimeError("EXECUTION_WALLET_PRIVATE_KEY is not set")

        self.app_config = app_config
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.account = Account.from_key(private_key)
        self.contract = self.w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=FLASH_ARB_RECEIVER_ABI)
        logger.warning("live_dex_adapter_initialized", contract=contract_address, wallet=self.account.address)

    async def submit_flashloan_bundle(
        self,
        protocol: str,
        pool_id: str,
        cex_exchange: str,
        symbol: str,
        amount: float,
        direction: str,
        max_gas_price_gwei: float,
        priority_fee_gwei: float,
        use_flashbots: bool,
    ) -> FlashLoanResult:
        # Router/token addresses for the pair must come from a venue
        # registry (see docs/ARCHITECTURE.md's DEX venue config) — kept
        # out of this adapter to avoid hard-coding chain-specific addresses
        # into execution logic. Raising here rather than guessing an
        # address is the deliberately safe failure mode.
        raise NotImplementedError(
            "submit_flashloan_bundle requires per-pair router/token address resolution, "
            "which this build leaves as an explicit integration point (see "
            "docs/LIMITATIONS.md) rather than hard-coding placeholder contract "
            "addresses that would silently fail or, worse, silently succeed against "
            "the wrong pool."
        )

    def build_flashloan_transaction(
        self, asset: str, amount_wei: int, router: str, token_out: str, min_amount_out_wei: int, max_gas_price_gwei: float, priority_fee_gwei: float
    ) -> TxParams:
        """Builds (but does not send) the EIP-1559 transaction calling
        FlashArbReceiver.requestFlashLoan. Split out from submission so it
        can be unit-tested without a live RPC connection."""
        nonce = self.w3.eth.get_transaction_count(self.account.address)
        return self.contract.functions.requestFlashLoan(
            Web3.to_checksum_address(asset),
            amount_wei,
            Web3.to_checksum_address(router),
            Web3.to_checksum_address(token_out),
            min_amount_out_wei,
        ).build_transaction(
            {
                "from": self.account.address,
                "nonce": nonce,
                "maxFeePerGas": self.w3.to_wei(max_gas_price_gwei, "gwei"),
                "maxPriorityFeePerGas": self.w3.to_wei(priority_fee_gwei, "gwei"),
                "chainId": self.w3.eth.chain_id,
            }
        )

    def sign_transaction(self, tx: dict) -> bytes:
        signed = self.account.sign_transaction(tx)
        return signed.raw_transaction

    async def send_via_flashbots_protect(self, raw_tx: bytes) -> str:
        """Submits through Flashbots Protect's RPC, which relays privately
        to block builders instead of the public mempool — protects this
        transaction from being front-run/sandwiched by other searchers.
        Uses eth_sendRawTransaction against the Protect RPC endpoint per
        Flashbots' documented drop-in-replacement interface."""
        protect_w3 = Web3(Web3.HTTPProvider(FLASHBOTS_PROTECT_RPC))
        tx_hash = protect_w3.eth.send_raw_transaction(raw_tx)
        return tx_hash.hex()


def load_contract_abi(build_artifact_path: str | Path) -> list:
    """Loads the full ABI from a Foundry/Hardhat build artifact JSON,
    for callers that need more than the minimal ABI hard-coded above
    (e.g. reading emitted events)."""
    artifact = json.loads(Path(build_artifact_path).read_text())
    return artifact["abi"]
