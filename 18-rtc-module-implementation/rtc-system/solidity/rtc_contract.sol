// Companion code for "The Backend of Luck" - Chapter 18, RTC Module Implementation.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

pragma solidity ^0.8.19;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

/**
 * @title CasinoRTCVerification
 * @dev Smart contract for verifying RTC timestamps on blockchain
 * @notice Provides tamper-proof timestamp verification for casino operations
 */
contract CasinoRTCVerification is Ownable, ReentrancyGuard {
    struct TimestampProof {
        uint256 unixTime;
        uint256 nanoTime;
        string iso8601;
        bytes32 signature;
        uint256 confidence;
        int256 driftMs;
        string source;
        bytes32 proofHash;
        uint256 blockNumber;
        address submitter;
        bool verified;
    }

    struct GameRound {
        uint256 roundId;
        uint256 startTime;
        uint256 endTime;
        bytes32 rngSeed;
        address winner;
        uint256 prizeAmount;
        bool finalized;
    }

    // State variables
    mapping(bytes32 => TimestampProof) public timestampProofs;
    mapping(uint256 => GameRound) public gameRounds;
    mapping(address => bool) public authorizedSubmitters;
    mapping(bytes32 => bool) public usedProofs;

    uint256 public constant MIN_CONFIDENCE = 80; // Minimum confidence percentage
    uint256 public constant MAX_DRIFT_MS = 100; // Maximum allowed drift
    uint256 public roundCounter;

    // Events
    event TimestampVerified(bytes32 indexed proofId, address indexed submitter, bool success);
    event GameRoundStarted(uint256 indexed roundId, uint256 startTime);
    event GameRoundEnded(uint256 indexed roundId, uint256 endTime, address winner);
    event ProofInvalidated(bytes32 indexed proofId, string reason);

    modifier onlyAuthorized() {
        require(authorizedSubmitters[msg.sender] || owner() == msg.sender,
                "Not authorized to submit proofs");
        _;
    }

    modifier validProof(bytes32 proofId) {
        require(timestampProofs[proofId].unixTime > 0, "Proof does not exist");
        require(!usedProofs[proofId], "Proof already used");
        require(timestampProofs[proofId].verified, "Proof not verified");
        _;
    }

    constructor() {
        authorizedSubmitters[msg.sender] = true;
        roundCounter = 1;
    }

    /**
     * @dev Submit and verify an RTC timestamp proof
     * @param unixTime Unix timestamp in seconds
     * @param nanoTime Nanosecond precision
     * @param iso8601 ISO 8601 formatted time
     * @param signature HMAC-SHA256 signature
     * @param confidence Confidence score (0-100)
     * @param driftMs Drift from system time
     * @param source RTC source identifier
     */
    function submitTimestampProof(
        uint256 unixTime,
        uint256 nanoTime,
        string calldata iso8601,
        bytes32 signature,
        uint256 confidence,
        int256 driftMs,
        string calldata source
    ) external onlyAuthorized returns (bytes32) {

        // Validate inputs
        require(unixTime > 0, "Invalid unix time");
        require(confidence >= MIN_CONFIDENCE, "Confidence too low");
        require(driftMs >= -MAX_DRIFT_MS && driftMs <= MAX_DRIFT_MS, "Drift too high");

        // Create proof hash
        bytes32 proofHash = keccak256(abi.encodePacked(
            unixTime, nanoTime, iso8601, signature, confidence, driftMs, source, block.number
        ));

        // Verify signature (simplified - in production use proper verification)
        bool isValidSignature = _verifySignature(proofHash, signature);

        require(isValidSignature, "Invalid signature");

        // Store proof
        timestampProofs[proofHash] = TimestampProof({
            unixTime: unixTime,
            nanoTime: nanoTime,
            iso8601: iso8601,
            signature: signature,
            confidence: confidence,
            driftMs: driftMs,
            source: source,
            proofHash: proofHash,
            blockNumber: block.number,
            submitter: msg.sender,
            verified: true
        });

        emit TimestampVerified(proofHash, msg.sender, true);
        return proofHash;
    }

    /**
     * @dev Start a new game round with RTC timestamp
     * @param rngSeed Random number generator seed
     * @param proofId Timestamp proof for round start
     */
    function startGameRound(bytes32 rngSeed, bytes32 proofId)
        external
        onlyAuthorized
        validProof(proofId)
        returns (uint256) {

        uint256 roundId = roundCounter++;
        TimestampProof memory proof = timestampProofs[proofId];

        gameRounds[roundId] = GameRound({
            roundId: roundId,
            startTime: proof.unixTime,
            endTime: 0,
            rngSeed: rngSeed,
            winner: address(0),
            prizeAmount: 0,
            finalized: false
        });

        usedProofs[proofId] = true;

        emit GameRoundStarted(roundId, proof.unixTime);
        return roundId;
    }

    /**
     * @dev End a game round and declare winner
     * @param roundId Game round identifier
     * @param winner Winner address
     * @param prizeAmount Prize amount in wei
     * @param proofId Timestamp proof for round end
     */
    function endGameRound(
        uint256 roundId,
        address winner,
        uint256 prizeAmount,
        bytes32 proofId
    ) external onlyAuthorized validProof(proofId) {

        require(gameRounds[roundId].startTime > 0, "Round does not exist");
        require(!gameRounds[roundId].finalized, "Round already finalized");

        TimestampProof memory proof = timestampProofs[proofId];
        require(proof.unixTime > gameRounds[roundId].startTime, "End time before start time");

        gameRounds[roundId].endTime = proof.unixTime;
        gameRounds[roundId].winner = winner;
        gameRounds[roundId].prizeAmount = prizeAmount;
        gameRounds[roundId].finalized = true;

        usedProofs[proofId] = true;

        emit GameRoundEnded(roundId, proof.unixTime, winner);
    }

    /**
     * @dev Verify timestamp signature (simplified implementation)
     * @param data Data to verify
     * @param signature Signature to check
     */
    function _verifySignature(bytes32 data, bytes32 signature) internal pure returns (bool) {
        // In production, implement proper signature verification
        // This is a simplified version for demonstration
        return signature != bytes32(0);
    }

    /**
     * @dev Get timestamp proof details
     */
    function getTimestampProof(bytes32 proofId) external view returns (TimestampProof memory) {
        return timestampProofs[proofId];
    }

    /**
     * @dev Get game round details
     */
    function getGameRound(uint256 roundId) external view returns (GameRound memory) {
        return gameRounds[roundId];
    }

    /**
     * @dev Authorize a submitter address
     */
    function authorizeSubmitter(address submitter) external onlyOwner {
        authorizedSubmitters[submitter] = true;
    }

    /**
     * @dev Revoke submitter authorization
     */
    function revokeSubmitter(address submitter) external onlyOwner {
        authorizedSubmitters[submitter] = false;
    }

    /**
     * @dev Emergency function to invalidate a proof
     */
    function invalidateProof(bytes32 proofId, string calldata reason) external onlyOwner {
        require(timestampProofs[proofId].unixTime > 0, "Proof does not exist");

        timestampProofs[proofId].verified = false;
        emit ProofInvalidated(proofId, reason);
    }
}
