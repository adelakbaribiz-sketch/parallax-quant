// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IPoolAddressesProvider} from "@aave/core-v3/contracts/interfaces/IPoolAddressesProvider.sol";
import {IPool} from "@aave/core-v3/contracts/interfaces/IPool.sol";
import {FlashLoanSimpleReceiverBase} from "@aave/core-v3/contracts/flashloan/base/FlashLoanSimpleReceiverBase.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external returns (uint[] memory amounts);
}

/// @title FlashArbReceiver
/// @notice Minimal Aave v3 flash-loan receiver that buys `tokenOut` for
/// `tokenIn` on one DEX router and is expected to be paired, off-chain,
/// with a CEX sell leg executed by execution/live_cex.py. The on-chain
/// leg here only ever touches the DEX side plus loan repayment — it does
/// not and cannot execute the CEX leg, since that requires an off-chain
/// exchange account.
/// @dev NOT AUDITED. Deploy and exercise extensively on a testnet
/// (Sepolia/Goerli-successor) before pointing this at mainnet funds. See
/// docs/LIMITATIONS.md — this repository does not include a deployment
/// pipeline, a completed audit, or any mainnet transaction history.
contract FlashArbReceiver is FlashLoanSimpleReceiverBase, Ownable {
    using SafeERC20 for IERC20;

    event ArbExecuted(address indexed tokenIn, address indexed tokenOut, uint256 amountIn, uint256 profit);

    constructor(IPoolAddressesProvider provider) FlashLoanSimpleReceiverBase(provider) Ownable(msg.sender) {}

    /// @notice Borrow `amount` of `asset`, swap it for `tokenOut` on
    /// `router`, and hold the output on this contract (the off-chain
    /// bot then sells `tokenOut` on the CEX leg and reconciles profit
    /// separately — this function never assumes it can reach a CEX).
    function requestFlashLoan(
        address asset,
        uint256 amount,
        address router,
        address tokenOut,
        uint256 minAmountOut
    ) external onlyOwner {
        bytes memory params = abi.encode(router, tokenOut, minAmountOut);
        POOL.flashLoanSimple(address(this), asset, amount, params, 0);
    }

    /// @notice Aave v3 callback — executes with the borrowed funds
    /// already in this contract's balance. Must repay amount+premium by
    /// the end of this function or the whole transaction reverts,
    /// including the loan itself: there is no partial-failure state.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address /* initiator */,
        bytes calldata params
    ) external override returns (bool) {
        require(msg.sender == address(POOL), "caller must be Aave Pool");

        (address router, address tokenOut, uint256 minAmountOut) = abi.decode(params, (address, address, uint256));

        IERC20(asset).forceApprove(router, amount);
        address[] memory path = new address[](2);
        path[0] = asset;
        path[1] = tokenOut;

        uint256[] memory amounts = IUniswapV2Router(router).swapExactTokensForTokens(
            amount,
            minAmountOut,
            path,
            address(this),
            block.timestamp
        );

        uint256 amountOwed = amount + premium;
        require(IERC20(asset).balanceOf(address(this)) >= amountOwed, "insufficient funds to repay flash loan");

        IERC20(asset).forceApprove(address(POOL), amountOwed);
        emit ArbExecuted(asset, tokenOut, amount, amounts[amounts.length - 1]);
        return true;
    }

    /// @notice Sweep any token balance (loan repayment leaves residual
    /// tokenOut on this contract) back to the owner. Owner-only — no
    /// external party can drain the contract.
    function sweep(address token, address to) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        IERC20(token).safeTransfer(to, balance);
    }
}
