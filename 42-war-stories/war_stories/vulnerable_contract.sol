// Companion code for "The Backend of Luck" - Chapter 42, War Stories.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.
//
// This contract is DELIBERATELY VULNERABLE. It is the code behind a real
// incident described in the chapter, preserved unchanged so the flaw can be
// studied. Never deploy it.

// Chapter 42: War Stories
// War Story 5: The Blockchain Integration Disaster - Smart Contract Examples
//
// This file contains the VULNERABLE smart contract that caused €650K in losses,
// preserved exactly as-is for educational reference.
//
// DO NOT DEPLOY THIS CONTRACT. It contains critical vulnerabilities:
// 1. Predictable randomness via block.timestamp and block.difficulty
// 2. Missing reentrancy protection
// 3. Incorrect payout calculation (35:1 instead of 36:1)
// 4. House edge not properly applied
//
pragma solidity ^0.8.0;

// PROBLEMATIC: Vulnerable smart contract
contract ProvablyFairRoulette {
    mapping(address => uint256) public balances;
    uint256 public houseEdge = 50; // 5% house edge (basis points)
    uint256 private nonce;

    function spinWheel(uint256 betAmount, uint8 chosenNumber) external {
        require(balances[msg.sender] >= betAmount, "Insufficient balance");

        // Generate "provably fair" result
        bytes32 randomSeed = keccak256(abi.encodePacked(
            block.timestamp,    // BUG: Miners can manipulate this
            block.difficulty,   // BUG: Predictable, deprecated in PoS
            msg.sender,
            nonce++
        ));

        uint8 result = uint8(uint256(randomSeed) % 37); // 0-36

        // Calculate payout
        uint256 payout = 0;
        if (result == chosenNumber) {
            // BUG: Incorrect payout calculation
            payout = betAmount * 35; // Should be 36:1, but this gives 35:1
        }

        // BUG: No reentrancy protection
        balances[msg.sender] -= betAmount;
        if (payout > 0) {
            balances[msg.sender] += payout;
        }

        // BUG: House edge not properly applied
        // The contract should take houseEdge basis points
    }
}
