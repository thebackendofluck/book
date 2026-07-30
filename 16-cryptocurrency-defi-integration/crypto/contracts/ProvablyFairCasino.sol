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
 * Provably Fair Casino Smart Contract
 *
 * Implements on-chain provably fair gambling using Chainlink VRF v2 for
 * verifiable randomness. Supports multiple game types (Roulette, Dice) with:
 * - Player seed combined with VRF randomness for client-verifiable outcomes
 * - Per-game configurable bet limits and house edge (basis points)
 * - Emergency resolution via blockhash fallback for stuck bets (> 1 hour)
 * - Full transparency via events: BetPlaced, BetResolved, PayoutClaimed
 *
 * Reference: Chapter 8 - Provably Fair Smart Contract Architecture section
 */

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@chainlink/contracts/src/v0.8/VRFConsumerBaseV2.sol";
import "@chainlink/contracts/src/v0.8/interfaces/VRFCoordinatorV2Interface.sol";

contract ProvablyFairCasino is Ownable, ReentrancyGuard, VRFConsumerBaseV2 {
    VRFCoordinatorV2Interface private immutable COORDINATOR;

    // Chainlink VRF parameters
    uint64 private s_subscriptionId;
    bytes32 private immutable keyHash;
    uint32 private immutable callbackGasLimit = 100000;
    uint16 private constant REQUEST_CONFIRMATIONS = 3;
    uint32 private constant NUM_WORDS = 1;

    // Roulette bet types (encoded as the first byte of betData)
    uint8 private constant BET_STRAIGHT = 0; // single number 0-36, pays 35:1
    uint8 private constant BET_RED = 1;      // even-money outside bet
    uint8 private constant BET_BLACK = 2;    // even-money outside bet
    uint8 private constant BET_EVEN = 3;     // even-money outside bet
    uint8 private constant BET_ODD = 4;      // even-money outside bet

    // Dice bet directions (encoded as betData)
    uint8 private constant DICE_UNDER = 0;
    uint8 private constant DICE_OVER = 1;

    // Game configurations
    struct GameConfig {
        uint256 minBet;
        uint256 maxBet;
        uint256 houseEdge; // Basis points (100 = 1%)
        bool active;
        uint256 maxPayout; // Maximum payout per bet
    }

    // Bet structure
    struct Bet {
        address player;
        uint256 amount;
        uint256 gameId;
        bytes32 playerSeed;
        bytes betData;
        uint256 vrfRequestId;
        uint256 randomWord;
        uint256 payout;
        bool resolved;
        uint256 timestamp;
    }

    mapping(uint256 => GameConfig) public games;
    mapping(uint256 => Bet) public bets;
    mapping(address => uint256[]) public playerBets;
    mapping(bytes32 => uint256) private requestIdToBetId;

    uint256 private betCounter;
    uint256 public totalBets;
    uint256 public totalPayouts;

    // Events for transparency
    event BetPlaced(uint256 indexed betId, address indexed player, uint256 amount, uint256 gameId);
    event BetResolved(uint256 indexed betId, uint256 payout, uint256 randomWord);
    event PayoutClaimed(address indexed player, uint256 amount);

    constructor(
        address vrfCoordinator,
        bytes32 _keyHash,
        uint64 subscriptionId
    ) VRFConsumerBaseV2(vrfCoordinator) {
        COORDINATOR = VRFCoordinatorV2Interface(vrfCoordinator);
        keyHash = _keyHash;
        s_subscriptionId = subscriptionId;

        // Initialize default games
        _initializeGames();
    }

    function _initializeGames() private {
        // Roulette: 2.7% house edge
        games[1] = GameConfig({
            minBet: 0.001 ether,
            maxBet: 1 ether,
            houseEdge: 270,
            active: true,
            maxPayout: 35 ether
        });

        // Dice: 1% house edge
        games[2] = GameConfig({
            minBet: 0.0001 ether,
            maxBet: 10 ether,
            houseEdge: 100,
            active: true,
            maxPayout: 99 ether
        });
    }

    /**
     * @notice Place a bet with provably fair randomness
     * @param gameId ID of the game to play
     * @param betData Encoded bet parameters (validated eagerly so a malformed
     *        bet never causes the VRF fulfillment callback to revert)
     * @param playerSeed Client seed for provable fairness
     */
    function placeBet(
        uint256 gameId,
        bytes calldata betData,
        bytes32 playerSeed
    ) external payable nonReentrant {
        GameConfig memory game = games[gameId];
        require(game.active, "Game not active");
        require(msg.value >= game.minBet && msg.value <= game.maxBet, "Invalid bet amount");

        // Calculate maximum possible payout; also validates betData up front
        // so we never request randomness for a bet we cannot resolve.
        uint256 maxPayout = calculateMaxPayout(gameId, betData, msg.value);
        require(maxPayout <= game.maxPayout, "Payout exceeds maximum");

        // Request randomness from Chainlink VRF
        uint256 requestId = COORDINATOR.requestRandomWords(
            keyHash,
            s_subscriptionId,
            REQUEST_CONFIRMATIONS,
            callbackGasLimit,
            NUM_WORDS
        );

        // Create bet record
        betCounter++;
        bets[betCounter] = Bet({
            player: msg.sender,
            amount: msg.value,
            gameId: gameId,
            playerSeed: playerSeed,
            betData: betData,
            vrfRequestId: requestId,
            randomWord: 0,
            payout: 0,
            resolved: false,
            timestamp: block.timestamp
        });

        requestIdToBetId[requestId] = betCounter;
        playerBets[msg.sender].push(betCounter);
        totalBets++;

        emit BetPlaced(betCounter, msg.sender, msg.value, gameId);
    }

    /**
     * @notice Callback function for Chainlink VRF
     * @param requestId ID of the VRF request
     * @param randomWords Array of random words
     */
    function fulfillRandomWords(
        uint256 requestId,
        uint256[] memory randomWords
    ) internal override {
        uint256 betId = requestIdToBetId[requestId];
        require(betId > 0, "Invalid request ID");

        Bet storage bet = bets[betId];
        require(!bet.resolved, "Bet already resolved");

        bet.randomWord = randomWords[0];

        // Calculate game outcome
        uint256 payout = calculateGameOutcome(
            bet.gameId,
            bet.amount,
            bet.randomWord,
            bet.playerSeed,
            bet.betData
        );

        bet.payout = payout;
        bet.resolved = true;

        totalPayouts += payout;

        emit BetResolved(betId, payout, randomWords[0]);
    }

    /**
     * @notice Calculate game outcome based on randomness
     * @param gameId Game identifier
     * @param betAmount Original bet amount
     * @param randomWord VRF-provided randomness
     * @param playerSeed Client-provided seed
     * @param betData Player's bet selection (bet type, chosen number/direction)
     * @return Final payout amount
     */
    function calculateGameOutcome(
        uint256 gameId,
        uint256 betAmount,
        uint256 randomWord,
        bytes32 playerSeed,
        bytes memory betData
    ) private view returns (uint256) {
        // Combine VRF randomness with player seed for provable fairness.
        // The VRF output is the sole source of entropy alongside the
        // player's own pre-committed seed; block.timestamp is intentionally
        // excluded so miners/validators cannot bias the outcome.
        uint256 combinedRandom = uint256(keccak256(abi.encodePacked(
            randomWord,
            playerSeed
        )));

        GameConfig memory game = games[gameId];

        if (gameId == 1) {
            // Roulette implementation
            return calculateRouletteOutcome(betAmount, combinedRandom, game.houseEdge, betData);
        } else if (gameId == 2) {
            // Dice implementation
            return calculateDiceOutcome(betAmount, combinedRandom, game.houseEdge, betData);
        }

        return 0;
    }

    function calculateRouletteOutcome(
        uint256 betAmount,
        uint256 randomness,
        uint256 houseEdge,
        bytes memory betData
    ) private pure returns (uint256) {
        (uint8 betType, uint8 betValue) = abi.decode(betData, (uint8, uint8));

        if (betType == BET_STRAIGHT) {
            require(betValue <= 36, "Invalid number");
            uint256 spin = randomness % 37; // Roulette wheel: 0-36 (37 slots)
            return spin == betValue ? betAmount * 36 : 0; // 35:1 payout plus stake
        }

        require(
            betType == BET_RED || betType == BET_BLACK || betType == BET_EVEN || betType == BET_ODD,
            "Invalid bet type"
        );

        // Even-money outside bet (red/black/even/odd): the win probability is
        // the fair 50% (5000 bps) reduced by half the house edge on each
        // side, so a 2x payout yields an expected value of exactly
        // -houseEdge/10000 per unit wagered. For houseEdge=270 that is a
        // 48.65% win chance (win when randomness % 10000 < 4865).
        uint256 winThreshold = 5000 - (houseEdge / 2);
        if ((randomness % 10000) < winThreshold) {
            return betAmount * 2;
        }

        return 0;
    }

    function calculateDiceOutcome(
        uint256 betAmount,
        uint256 randomness,
        uint256 houseEdge,
        bytes memory betData
    ) private pure returns (uint256) {
        uint8 direction = abi.decode(betData, (uint8));
        require(direction == DICE_UNDER || direction == DICE_OVER, "Invalid direction");

        uint256 roll = randomness % 10000;
        uint256 winThreshold = 5000 - (houseEdge / 2);

        bool won = direction == DICE_UNDER
            ? roll < winThreshold
            : roll >= (10000 - winThreshold);

        return won ? betAmount * 2 : 0;
    }

    /**
     * @notice Compute the worst-case payout for a bet before randomness is
     *         requested. Also fully validates betData so a malformed bet
     *         reverts here (cheaply, no VRF request made) instead of inside
     *         the VRF fulfillment callback, where a revert would strand the
     *         bet unresolved.
     */
    function calculateMaxPayout(
        uint256 gameId,
        bytes calldata betData,
        uint256 betAmount
    ) private pure returns (uint256) {
        if (gameId == 1) {
            (uint8 betType, uint8 betValue) = abi.decode(betData, (uint8, uint8));
            require(
                betType == BET_STRAIGHT || betType == BET_RED || betType == BET_BLACK ||
                betType == BET_EVEN || betType == BET_ODD,
                "Invalid bet type"
            );
            if (betType == BET_STRAIGHT) {
                require(betValue <= 36, "Invalid number");
                return betAmount * 36;
            }
            return betAmount * 2;
        }

        if (gameId == 2) {
            uint8 direction = abi.decode(betData, (uint8));
            require(direction == DICE_UNDER || direction == DICE_OVER, "Invalid direction");
            return betAmount * 2;
        }

        return 0;
    }

    /**
     * @notice Claim payout for resolved bet
     * @param betId ID of the resolved bet
     */
    function claimPayout(uint256 betId) external nonReentrant {
        Bet memory bet = bets[betId];
        require(bet.player == msg.sender, "Not bet owner");
        require(bet.resolved, "Bet not resolved");
        require(bet.payout > 0, "No payout available");

        uint256 payout = bet.payout;
        bets[betId].payout = 0; // Prevent double claim

        (bool success, ) = msg.sender.call{value: payout}("");
        require(success, "Payout transfer failed");

        emit PayoutClaimed(msg.sender, payout);
    }

    /**
     * @notice Emergency function to resolve stuck bets
     * @param betId ID of the bet to resolve manually
     */
    function emergencyResolve(uint256 betId) external onlyOwner {
        Bet storage bet = bets[betId];
        require(!bet.resolved, "Bet already resolved");
        require(block.timestamp > bet.timestamp + 1 hours, "Too early for emergency resolve");

        // Use blockhash as fallback randomness
        uint256 fallbackRandom = uint256(blockhash(block.number - 1));
        bet.randomWord = fallbackRandom;

        uint256 payout = calculateGameOutcome(
            bet.gameId,
            bet.amount,
            fallbackRandom,
            bet.playerSeed,
            bet.betData
        );

        bet.payout = payout;
        bet.resolved = true;
        totalPayouts += payout;

        emit BetResolved(betId, payout, fallbackRandom);
    }

    // View functions for transparency
    function getBet(uint256 betId) external view returns (Bet memory) {
        return bets[betId];
    }

    function getPlayerBets(address player) external view returns (uint256[] memory) {
        return playerBets[player];
    }

    function getHouseEdge() external view returns (uint256) {
        return address(this).balance - totalPayouts;
    }

    receive() external payable {}
}
