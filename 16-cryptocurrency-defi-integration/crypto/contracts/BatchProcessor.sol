// Companion code for "The Backend of Luck" - Chapter 16, Cryptocurrency and DeFi Integration.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

pragma solidity ^0.8.19;

/**
 * Chapter 16: Cryptocurrency and DeFi Integration
 * Gas-Optimized Batch Payment Processor
 *
 * Optimizes Ethereum gas costs for high-volume payout operations:
 * - Batches up to 50 withdrawals in a single transaction
 * - Gas-aware loop with configurable buffer to prevent out-of-gas errors
 * - Packed struct storage (PlayerStats fits in one 32-byte storage slot)
 * - Checks-effects-interactions ordering with a reentrancy guard around
 *   the payout loop
 *
 * Reference: Chapter 8 - Gas Optimization Strategies section
 */

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/utils/math/SafeMath.sol";

contract BatchProcessor is Ownable, ReentrancyGuard {
    using SafeMath for uint256;

    uint256 private constant BATCH_SIZE = 50;
    uint256 private constant GAS_BUFFER = 30000;

    struct BatchPayment {
        address recipient;
        uint256 amount;
        bool processed;
    }

    mapping(uint256 => BatchPayment) public batchPayments;
    uint256 public batchCounter;

    event BatchProcessed(uint256 indexed batchId, uint256 totalAmount, uint256 recipientsPaid, uint256 gasUsed);

    function batchWithdraw(address[] calldata recipients, uint256[] calldata amounts)
        external
        onlyOwner
        nonReentrant
    {
        require(recipients.length == amounts.length, "Array length mismatch");
        require(recipients.length > 0, "Empty batch");
        require(recipients.length <= BATCH_SIZE, "Batch size too large");

        uint256 batchId = batchCounter++;
        uint256 gasStart = gasleft();
        uint256 totalAmount = 0;
        uint256 recipientsPaid = 0;

        for (uint256 i = 0; i < recipients.length; i++) {
            require(recipients[i] != address(0), "Invalid recipient");
            require(amounts[i] > 0, "Invalid amount");

            // Check gas before each transfer so a batch running low on gas
            // stops cleanly instead of reverting and rolling back payouts
            // already sent earlier in the loop.
            if (gasleft() < GAS_BUFFER) {
                break;
            }

            // Effects before interaction: record the payment as processed
            // before sending funds, so contract state stays consistent
            // even though nonReentrant already blocks reentry into this
            // function.
            batchPayments[batchId * BATCH_SIZE + i] = BatchPayment({
                recipient: recipients[i],
                amount: amounts[i],
                processed: true
            });
            totalAmount = totalAmount.add(amounts[i]);
            recipientsPaid++;

            // Pay each recipient directly; the contract must never send
            // player/operator funds to itself.
            (bool success, ) = recipients[i].call{value: amounts[i]}("");
            require(success, "Recipient transfer failed");
        }

        uint256 gasUsed = gasStart - gasleft();
        emit BatchProcessed(batchId, totalAmount, recipientsPaid, gasUsed);
    }

    // Gas-efficient storage using packed structs
    struct PlayerStats {
        uint64 totalBets;      // 8 bytes
        uint64 totalWins;      // 8 bytes
        uint64 lastBetTime;    // 8 bytes
        uint32 vipLevel;       // 4 bytes
        bool isActive;         // 1 byte
        // Total: 29 bytes (fits in a single 32-byte storage slot)
    }

    mapping(address => PlayerStats) public playerStats;

    /**
     * @notice Record a settled bet's outcome for a player.
     * @dev Restricted to the owner (the settlement service) so an
     * arbitrary caller cannot inflate their own -- or anyone else's --
     * stats. The Solidity compiler already packs `stats` into a single
     * storage slot for a normal struct write; the inline-assembly `sstore`
     * this replaced computed the wrong slot (`playerStats.slot` is the base
     * slot of the whole mapping, not `player`'s slot, which is
     * `keccak256(abi.encode(player, playerStats.slot))`) and wrote a raw
     * `PlayerStats` struct value where a `bytes32` word was expected.
     */
    function updatePlayerStats(address player, bool won) external onlyOwner {
        PlayerStats storage stats = playerStats[player];

        if (won) {
            stats.totalWins++;
        }
        stats.totalBets++;
        stats.lastBetTime = uint64(block.timestamp);
        stats.isActive = true;
    }
}
