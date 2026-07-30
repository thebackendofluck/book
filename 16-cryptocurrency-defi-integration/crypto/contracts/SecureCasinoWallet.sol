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
 * Secure Casino Wallet with Multi-Signature and Timelock
 *
 * Production-ready treasury management contract featuring:
 * - 3-of-N multi-signature approval for all withdrawals
 * - 24-hour timelock on withdrawal execution
 * - Pausable circuit breaker for emergency stops
 * - Supports both native ETH and ERC-20 token withdrawals
 * - SafeERC20 for secure token transfers
 *
 * Reference: Chapter 8 - Security Implementation section
 */

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/security/Pausable.sol";
import "@openzeppelin/contracts/utils/math/SafeMath.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract SecureCasinoWallet is Ownable, ReentrancyGuard, Pausable {
    using SafeMath for uint256;
    using SafeERC20 for IERC20;

    // Multi-signature requirements
    uint256 public constant REQUIRED_SIGNATURES = 3;
    uint256 public constant TIMELOCK_DURATION = 24 hours;

    address[] public signers;
    mapping(address => bool) public isSigner;
    mapping(uint256 => WithdrawalRequest) public withdrawalRequests;
    uint256 public nextRequestId;

    struct WithdrawalRequest {
        address token;
        uint256 amount;
        address recipient;
        uint256 createdAt;
        uint256 approvals;
        bool executed;
        mapping(address => bool) approvedBy;
    }

    modifier onlySigner() {
        require(isSigner[msg.sender], "Not authorized signer");
        _;
    }

    constructor(address[] memory _signers) {
        require(_signers.length >= REQUIRED_SIGNATURES, "Not enough signers");
        for (uint256 i = 0; i < _signers.length; i++) {
            address signer = _signers[i];
            require(signer != address(0), "Invalid signer");
            require(!isSigner[signer], "Duplicate signer");
            signers.push(signer);
            isSigner[signer] = true;
        }
    }

    function addSigner(address signer) external onlyOwner {
        require(signer != address(0), "Invalid signer");
        require(!isSigner[signer], "Already a signer");
        signers.push(signer);
        isSigner[signer] = true;
        emit SignerAdded(signer);
    }

    function removeSigner(address signer) external onlyOwner {
        require(isSigner[signer], "Not a signer");
        require(signers.length > REQUIRED_SIGNATURES, "Cannot drop below required signers");

        isSigner[signer] = false;
        for (uint256 i = 0; i < signers.length; i++) {
            if (signers[i] == signer) {
                signers[i] = signers[signers.length - 1];
                signers.pop();
                break;
            }
        }
        emit SignerRemoved(signer);
    }

    function requestWithdrawal(
        address token,
        uint256 amount,
        address recipient
    ) external onlyOwner whenNotPaused {
        require(recipient != address(0), "Invalid recipient");
        require(amount > 0, "Invalid amount");

        uint256 id = nextRequestId++;
        WithdrawalRequest storage request = withdrawalRequests[id];
        request.token = token;
        request.amount = amount;
        request.recipient = recipient;
        request.createdAt = block.timestamp;

        emit WithdrawalRequested(id, token, amount, recipient);
    }

    function approveWithdrawal(uint256 id) external onlySigner {
        WithdrawalRequest storage request = withdrawalRequests[id];
        require(request.createdAt != 0, "Unknown request");
        require(!request.executed, "Already executed");
        require(!request.approvedBy[msg.sender], "Already approved");

        request.approvedBy[msg.sender] = true;
        request.approvals++;

        emit WithdrawalApproved(id, msg.sender);

        // Auto-execute only once BOTH the approval threshold is met AND the
        // minimum timelock delay has elapsed. If every signer approves
        // before the timelock expires (a common case -- signers often
        // review and sign promptly), execution does not happen here; it
        // becomes available later via executeWithdrawal() below.
        if (request.approvals >= REQUIRED_SIGNATURES &&
            block.timestamp >= request.createdAt + TIMELOCK_DURATION) {
            _executeWithdrawal(id);
        }
    }

    /**
     * @notice Execute a request whose approval threshold was already met
     * before the timelock elapsed.
     * @dev Without this function, a request approved by all signers early
     * (within the timelock window) could never execute: approveWithdrawal
     * only triggers execution at the moment the threshold and the timelock
     * are satisfied simultaneously, and every signer's one-time approval
     * would already be spent. Any signer or the owner can call this once
     * the timelock has independently elapsed; it requires no fresh
     * approval, only that the existing approval count still meets the
     * threshold.
     */
    function executeWithdrawal(uint256 id) external {
        require(isSigner[msg.sender] || msg.sender == owner(), "Not authorized");
        _executeWithdrawal(id);
    }

    function _executeWithdrawal(uint256 id) private nonReentrant {
        WithdrawalRequest storage request = withdrawalRequests[id];
        require(request.createdAt != 0, "Unknown request");
        require(!request.executed, "Already executed");
        require(request.approvals >= REQUIRED_SIGNATURES, "Insufficient approvals");
        require(block.timestamp >= request.createdAt + TIMELOCK_DURATION, "Timelock not elapsed");

        request.executed = true;

        if (request.token == address(0)) {
            // Native currency withdrawal
            (bool success, ) = request.recipient.call{value: request.amount}("");
            require(success, "Transfer failed");
        } else {
            // ERC-20 token withdrawal
            IERC20(request.token).safeTransfer(request.recipient, request.amount);
        }

        emit WithdrawalExecuted(id, request.recipient, request.amount);
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    // Events
    event WithdrawalRequested(uint256 indexed id, address token, uint256 amount, address recipient);
    event WithdrawalApproved(uint256 indexed id, address signer);
    event WithdrawalExecuted(uint256 indexed id, address recipient, uint256 amount);
    event SignerAdded(address indexed signer);
    event SignerRemoved(address indexed signer);

    receive() external payable {}
}
