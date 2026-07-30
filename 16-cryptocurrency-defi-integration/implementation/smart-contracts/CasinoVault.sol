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
 * Casino Vault Smart Contract
 *
 * Secure deposit/withdrawal vault for crypto casino operations.
 * Features:
 * - Player deposits with automatic balance tracking
 * - House-edge-aware withdrawal processing
 * - Configurable minimum/maximum deposit and withdrawal limits
 * - Emergency withdrawal with cooldown period
 * - Multi-token support (ETH + ERC-20 via IERC20)
 * - Rate limiting to prevent flash loan attacks
 * - Operator profit extraction with time-lock
 * - Event logging for full audit trail
 *
 * Security: ReentrancyGuard, Pausable, Ownable, rate-limited
 * Reference: Chapter 8 - Crypto Casino Treasury Architecture
 */

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/security/Pausable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

contract CasinoVault is Ownable, ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    // ── Constants ────────────────────────────────────────────────────
    uint256 public constant MAX_HOUSE_EDGE_BPS = 1000;    // 10% max house edge
    uint256 public constant PROFIT_TIMELOCK = 24 hours;
    uint256 public constant EMERGENCY_COOLDOWN = 1 hours;
    uint256 public constant MAX_WITHDRAWAL_PER_BLOCK = 3;

    // ── State Variables ──────────────────────────────────────────────
    uint256 public houseEdgeBps;         // House edge in basis points (100 = 1%)
    uint256 public minDeposit;
    uint256 public maxDeposit;
    uint256 public minWithdrawal;
    uint256 public maxWithdrawal;
    uint256 public totalDeposited;
    uint256 public totalWithdrawn;
    uint256 public totalHouseProfit;
    uint256 public lastProfitExtraction;

    // Player balances (ETH)
    mapping(address => uint256) public playerBalances;
    mapping(address => uint256) public playerTotalDeposited;
    mapping(address => uint256) public playerTotalWithdrawn;
    mapping(address => uint256) public lastDepositBlock;
    mapping(address => uint256) public lastWithdrawalBlock;
    mapping(address => uint256) public withdrawalsThisBlock;

    // ERC-20 token support
    mapping(address => bool) public supportedTokens;
    mapping(address => mapping(address => uint256)) public tokenBalances; // player => token => balance

    // Operator addresses (multi-sig recommended)
    mapping(address => bool) public operators;

    // ── Events ───────────────────────────────────────────────────────
    event Deposited(address indexed player, uint256 amount, uint256 newBalance);
    event Withdrawn(address indexed player, uint256 amount, uint256 fee, uint256 newBalance);
    event TokenDeposited(address indexed player, address indexed token, uint256 amount);
    event TokenWithdrawn(address indexed player, address indexed token, uint256 amount, uint256 fee);
    event GameResult(address indexed player, int256 pnl, string gameType, bytes32 gameId);
    event ProfitExtracted(address indexed to, uint256 amount);
    event HouseEdgeUpdated(uint256 oldBps, uint256 newBps);
    event TokenAdded(address indexed token);
    event TokenRemoved(address indexed token);
    event OperatorAdded(address indexed operator);
    event OperatorRemoved(address indexed operator);
    event EmergencyWithdrawal(address indexed player, uint256 amount);
    event LimitsUpdated(uint256 minDep, uint256 maxDep, uint256 minWith, uint256 maxWith);

    // ── Modifiers ────────────────────────────────────────────────────
    modifier onlyOperator() {
        require(operators[msg.sender] || msg.sender == owner(), "Not authorized operator");
        _;
    }

    modifier rateLimited(address player) {
        if (block.number == lastWithdrawalBlock[player]) {
            require(
                withdrawalsThisBlock[player] < MAX_WITHDRAWAL_PER_BLOCK,
                "Rate limit: max withdrawals per block exceeded"
            );
            withdrawalsThisBlock[player]++;
        } else {
            lastWithdrawalBlock[player] = block.number;
            withdrawalsThisBlock[player] = 1;
        }
        _;
    }

    // ── Constructor ──────────────────────────────────────────────────
    constructor(
        uint256 _houseEdgeBps,
        uint256 _minDeposit,
        uint256 _maxDeposit,
        uint256 _minWithdrawal,
        uint256 _maxWithdrawal
    ) {
        require(_houseEdgeBps <= MAX_HOUSE_EDGE_BPS, "House edge too high");
        require(_minDeposit < _maxDeposit, "Invalid deposit range");
        require(_minWithdrawal < _maxWithdrawal, "Invalid withdrawal range");

        houseEdgeBps = _houseEdgeBps;
        minDeposit = _minDeposit;
        maxDeposit = _maxDeposit;
        minWithdrawal = _minWithdrawal;
        maxWithdrawal = _maxWithdrawal;
        lastProfitExtraction = block.timestamp;

        operators[msg.sender] = true;
    }

    // ── ETH Deposit ──────────────────────────────────────────────────
    /**
     * @notice Deposit ETH into the casino vault
     * @dev Checks min/max limits and records balance
     */
    function deposit() external payable whenNotPaused nonReentrant {
        require(msg.value >= minDeposit, "Below minimum deposit");
        require(msg.value <= maxDeposit, "Exceeds maximum deposit");

        playerBalances[msg.sender] += msg.value;
        playerTotalDeposited[msg.sender] += msg.value;
        totalDeposited += msg.value;
        lastDepositBlock[msg.sender] = block.number;

        emit Deposited(msg.sender, msg.value, playerBalances[msg.sender]);
    }

    // ── ETH Withdrawal ───────────────────────────────────────────────
    /**
     * @notice Withdraw ETH from the casino vault
     * @param amount Amount to withdraw (before house edge fee)
     * @dev Applies house edge fee on withdrawal. Rate-limited per block.
     */
    function withdraw(uint256 amount) external whenNotPaused nonReentrant rateLimited(msg.sender) {
        require(amount >= minWithdrawal, "Below minimum withdrawal");
        require(amount <= maxWithdrawal, "Exceeds maximum withdrawal");
        require(playerBalances[msg.sender] >= amount, "Insufficient balance");

        // Apply house edge fee on withdrawal
        uint256 fee = (amount * houseEdgeBps) / 10000;
        uint256 netAmount = amount - fee;

        playerBalances[msg.sender] -= amount;
        playerTotalWithdrawn[msg.sender] += netAmount;
        totalWithdrawn += netAmount;
        totalHouseProfit += fee;

        (bool success, ) = payable(msg.sender).call{value: netAmount}("");
        require(success, "ETH transfer failed");

        emit Withdrawn(msg.sender, netAmount, fee, playerBalances[msg.sender]);
    }

    // ── ERC-20 Token Deposit ─────────────────────────────────────────
    /**
     * @notice Deposit ERC-20 tokens (USDT, USDC, etc.)
     * @param token Token contract address
     * @param amount Amount to deposit
     */
    function depositToken(address token, uint256 amount) external whenNotPaused nonReentrant {
        require(supportedTokens[token], "Token not supported");
        require(amount > 0, "Amount must be > 0");

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
        tokenBalances[msg.sender][token] += amount;

        emit TokenDeposited(msg.sender, token, amount);
    }

    // ── ERC-20 Token Withdrawal ──────────────────────────────────────
    function withdrawToken(
        address token,
        uint256 amount
    ) external whenNotPaused nonReentrant rateLimited(msg.sender) {
        require(supportedTokens[token], "Token not supported");
        require(tokenBalances[msg.sender][token] >= amount, "Insufficient token balance");

        uint256 fee = (amount * houseEdgeBps) / 10000;
        uint256 netAmount = amount - fee;

        tokenBalances[msg.sender][token] -= amount;
        totalHouseProfit += fee; // Track in native units (approximate)

        IERC20(token).safeTransfer(msg.sender, netAmount);

        emit TokenWithdrawn(msg.sender, token, netAmount, fee);
    }

    // ── Game Result Recording ────────────────────────────────────────
    /**
     * @notice Record a game result (win or loss) for a player
     * @param player Player address
     * @param pnl Profit/loss amount (positive = player wins, negative = house wins)
     * @param gameType Game identifier (e.g., "slots", "blackjack", "roulette")
     * @param gameId Unique game round identifier
     * @dev Only callable by authorized operators (game servers)
     */
    function recordGameResult(
        address player,
        int256 pnl,
        string calldata gameType,
        bytes32 gameId
    ) external onlyOperator {
        if (pnl > 0) {
            // Player won - increase their balance (from house bankroll)
            playerBalances[player] += uint256(pnl);
        } else if (pnl < 0) {
            // Player lost - decrease their balance
            uint256 loss = uint256(-pnl);
            require(playerBalances[player] >= loss, "Player balance insufficient for loss");
            playerBalances[player] -= loss;
            totalHouseProfit += loss;
        }

        emit GameResult(player, pnl, gameType, gameId);
    }

    // ── Emergency Withdrawal ─────────────────────────────────────────
    /**
     * @notice Emergency withdrawal - no house edge, but requires cooldown
     * @dev Available even when paused. Players can always recover funds.
     */
    function emergencyWithdraw() external nonReentrant {
        uint256 balance = playerBalances[msg.sender];
        require(balance > 0, "No balance");
        require(
            block.timestamp >= lastDepositBlock[msg.sender] + EMERGENCY_COOLDOWN,
            "Emergency cooldown not elapsed"
        );

        playerBalances[msg.sender] = 0;

        (bool success, ) = payable(msg.sender).call{value: balance}("");
        require(success, "Emergency withdrawal failed");

        emit EmergencyWithdrawal(msg.sender, balance);
    }

    // ── Admin Functions ──────────────────────────────────────────────

    /**
     * @notice Extract accumulated house profits
     * @param to Destination address (treasury multi-sig)
     * @param amount Amount to extract
     * @dev Time-locked: minimum 24 hours between extractions
     */
    function extractProfit(address to, uint256 amount) external onlyOwner nonReentrant {
        require(
            block.timestamp >= lastProfitExtraction + PROFIT_TIMELOCK,
            "Profit extraction time-locked"
        );
        require(amount <= totalHouseProfit, "Exceeds available profit");
        require(amount <= address(this).balance, "Insufficient contract balance");

        totalHouseProfit -= amount;
        lastProfitExtraction = block.timestamp;

        (bool success, ) = payable(to).call{value: amount}("");
        require(success, "Profit extraction failed");

        emit ProfitExtracted(to, amount);
    }

    function setHouseEdge(uint256 newBps) external onlyOwner {
        require(newBps <= MAX_HOUSE_EDGE_BPS, "House edge too high");
        uint256 old = houseEdgeBps;
        houseEdgeBps = newBps;
        emit HouseEdgeUpdated(old, newBps);
    }

    function setLimits(
        uint256 _minDep, uint256 _maxDep,
        uint256 _minWith, uint256 _maxWith
    ) external onlyOwner {
        require(_minDep < _maxDep && _minWith < _maxWith, "Invalid limits");
        minDeposit = _minDep;
        maxDeposit = _maxDep;
        minWithdrawal = _minWith;
        maxWithdrawal = _maxWith;
        emit LimitsUpdated(_minDep, _maxDep, _minWith, _maxWith);
    }

    function addToken(address token) external onlyOwner {
        supportedTokens[token] = true;
        emit TokenAdded(token);
    }

    function removeToken(address token) external onlyOwner {
        supportedTokens[token] = false;
        emit TokenRemoved(token);
    }

    function addOperator(address op) external onlyOwner {
        operators[op] = true;
        emit OperatorAdded(op);
    }

    function removeOperator(address op) external onlyOwner {
        operators[op] = false;
        emit OperatorRemoved(op);
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    // ── View Functions ───────────────────────────────────────────────

    function getPlayerInfo(address player) external view returns (
        uint256 balance,
        uint256 deposited,
        uint256 withdrawn
    ) {
        return (
            playerBalances[player],
            playerTotalDeposited[player],
            playerTotalWithdrawn[player]
        );
    }

    function getVaultStats() external view returns (
        uint256 contractBalance,
        uint256 _totalDeposited,
        uint256 _totalWithdrawn,
        uint256 _totalProfit,
        uint256 _houseEdgeBps
    ) {
        return (
            address(this).balance,
            totalDeposited,
            totalWithdrawn,
            totalHouseProfit,
            houseEdgeBps
        );
    }

    // Allow contract to receive ETH directly (for bankroll funding)
    receive() external payable {}
}
