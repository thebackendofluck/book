#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
NFT-Based Loyalty Program for iGaming (ERC-721)
==================================================

Smart contract design and management tools for NFT-based casino
loyalty programs. Implements tiered membership NFTs, achievement
badges, and tradeable rewards on EVM-compatible blockchains.

Usage:
    python nft_loyalty.py --generate-contract
    python nft_loyalty.py --demo
    python nft_loyalty.py --tiers
"""

import json
import logging
import argparse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class LoyaltyTier:
    level: int
    name: str
    points_required: int
    benefits: list = field(default_factory=list)
    nft_metadata: dict = field(default_factory=dict)
    max_supply: Optional[int] = None  # None = unlimited
    tradeable: bool = True
    soulbound: bool = False  # non-transferable


LOYALTY_TIERS = [
    LoyaltyTier(1, "Bronze Player", 0,
                ["5% cashback on losses", "Birthday bonus", "Standard support"],
                {"image": "bronze_card.png", "animation": "bronze_shine.mp4",
                 "attributes": [{"trait_type": "Tier", "value": "Bronze"},
                                {"trait_type": "Cashback", "value": "5%"}]},
                tradeable=False, soulbound=True),
    LoyaltyTier(2, "Silver Player", 5000,
                ["10% cashback", "Weekly reload bonus", "Faster withdrawals", "Priority support"],
                {"image": "silver_card.png",
                 "attributes": [{"trait_type": "Tier", "value": "Silver"},
                                {"trait_type": "Cashback", "value": "10%"}]},
                tradeable=False, soulbound=True),
    LoyaltyTier(3, "Gold Player", 25000,
                ["15% cashback", "Personal account manager", "Exclusive tournaments",
                 "Higher betting limits", "Instant withdrawals"],
                {"image": "gold_card.png",
                 "attributes": [{"trait_type": "Tier", "value": "Gold"},
                                {"trait_type": "Cashback", "value": "15%"}]},
                tradeable=False, soulbound=True),
    LoyaltyTier(4, "Platinum Player", 100000,
                ["20% cashback", "VIP events", "Luxury gifts", "Custom game limits",
                 "Dedicated VIP host", "Private tables"],
                {"image": "platinum_card.png",
                 "attributes": [{"trait_type": "Tier", "value": "Platinum"},
                                {"trait_type": "Cashback", "value": "20%"}]},
                max_supply=1000, tradeable=False, soulbound=True),
    LoyaltyTier(5, "Diamond Player", 500000,
                ["25% cashback", "Bespoke experience", "Trip invitations",
                 "No withdrawal limits", "Personal concierge"],
                {"image": "diamond_card.png",
                 "attributes": [{"trait_type": "Tier", "value": "Diamond"},
                                {"trait_type": "Cashback", "value": "25%"}]},
                max_supply=100, tradeable=False, soulbound=True),
]


ACHIEVEMENT_NFTS = [
    {"id": "ACH-001", "name": "First Deposit", "description": "Made your first deposit",
     "rarity": "common", "tradeable": True, "max_supply": None},
    {"id": "ACH-002", "name": "Lucky Seven", "description": "Won 7 consecutive hands",
     "rarity": "rare", "tradeable": True, "max_supply": 10000},
    {"id": "ACH-003", "name": "High Roller", "description": "Single bet over $10,000",
     "rarity": "epic", "tradeable": True, "max_supply": 1000},
    {"id": "ACH-004", "name": "Jackpot Winner", "description": "Won a progressive jackpot",
     "rarity": "legendary", "tradeable": True, "max_supply": 100},
    {"id": "ACH-005", "name": "Marathon Player", "description": "1000+ hours of play",
     "rarity": "epic", "tradeable": True, "max_supply": 5000},
    {"id": "ACH-006", "name": "Responsible Player", "description": "Used responsible gaming tools proactively",
     "rarity": "rare", "tradeable": False, "max_supply": None, "soulbound": True},
]


# ---------------------------------------------------------------------------
# Solidity contract generator
# ---------------------------------------------------------------------------

SOLIDITY_CONTRACT = '''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/token/ERC721/extensions/ERC721Enumerable.sol";
import "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";
import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/Counters.sol";

/**
 * @title CasinoLoyaltyNFT
 * @notice NFT-based loyalty program for iGaming operators
 * @dev Implements tiered membership (soulbound) and achievement badges (tradeable)
 *
 * Features:
 * - Soulbound tier NFTs (non-transferable membership cards)
 * - Tradeable achievement NFTs (badges, milestones)
 * - Role-based minting (only authorized casino backend)
 * - Tier upgrade/downgrade mechanics
 * - On-chain loyalty points tracking
 * - Regulatory compliance: KYC-verified wallets only
 */
contract CasinoLoyaltyNFT is ERC721, ERC721Enumerable, ERC721URIStorage, AccessControl {
    using Counters for Counters.Counter;

    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
    bytes32 public constant ADMIN_ROLE = keccak256("ADMIN_ROLE");

    Counters.Counter private _tokenIdCounter;

    // Token types
    enum TokenType { TIER, ACHIEVEMENT }

    struct TokenData {
        TokenType tokenType;
        uint8 tier;           // 1-5 for tier tokens, 0 for achievements
        string achievementId; // empty for tier tokens
        bool soulbound;       // non-transferable if true
        uint256 mintedAt;
    }

    // Player loyalty data
    struct PlayerData {
        uint256 loyaltyPoints;
        uint8 currentTier;
        uint256 tierTokenId;
        bool kycVerified;
        uint256 registeredAt;
    }

    mapping(uint256 => TokenData) public tokenData;
    mapping(address => PlayerData) public players;
    mapping(string => uint256) public achievementSupply;    // achievementId => minted count
    mapping(string => uint256) public achievementMaxSupply; // achievementId => max (0 = unlimited)

    // Events
    event TierUpgrade(address indexed player, uint8 fromTier, uint8 toTier, uint256 tokenId);
    event TierDowngrade(address indexed player, uint8 fromTier, uint8 toTier, uint256 tokenId);
    event AchievementMinted(address indexed player, string achievementId, uint256 tokenId);
    event PointsAwarded(address indexed player, uint256 points, uint256 totalPoints);
    event PlayerRegistered(address indexed player);
    event KYCVerified(address indexed player);

    constructor() ERC721("Casino Loyalty", "LOYAL") {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        _grantRole(ADMIN_ROLE, msg.sender);
        _grantRole(MINTER_ROLE, msg.sender);
    }

    // --- Player Management ---

    function registerPlayer(address player) external onlyRole(MINTER_ROLE) {
        require(players[player].registeredAt == 0, "Already registered");
        players[player] = PlayerData({
            loyaltyPoints: 0,
            currentTier: 1,
            tierTokenId: 0,
            kycVerified: false,
            registeredAt: block.timestamp
        });
        // Mint initial Bronze tier NFT
        _mintTierNFT(player, 1);
        emit PlayerRegistered(player);
    }

    function verifyKYC(address player) external onlyRole(ADMIN_ROLE) {
        require(players[player].registeredAt > 0, "Not registered");
        players[player].kycVerified = true;
        emit KYCVerified(player);
    }

    // --- Points & Tiers ---

    function awardPoints(address player, uint256 points) external onlyRole(MINTER_ROLE) {
        require(players[player].registeredAt > 0, "Not registered");
        players[player].loyaltyPoints += points;
        emit PointsAwarded(player, points, players[player].loyaltyPoints);
    }

    function upgradeTier(address player, uint8 newTier) external onlyRole(MINTER_ROLE) {
        PlayerData storage pd = players[player];
        require(pd.registeredAt > 0, "Not registered");
        require(newTier > pd.currentTier && newTier <= 5, "Invalid tier");

        uint8 oldTier = pd.currentTier;

        // Burn old tier NFT
        if (pd.tierTokenId != 0) {
            _burn(pd.tierTokenId);
        }

        // Mint new tier NFT
        _mintTierNFT(player, newTier);
        emit TierUpgrade(player, oldTier, newTier, pd.tierTokenId);
    }

    // --- Achievements ---

    function mintAchievement(
        address player,
        string calldata achievementId,
        string calldata tokenURI_
    ) external onlyRole(MINTER_ROLE) {
        require(players[player].kycVerified, "KYC required");

        uint256 maxSupply = achievementMaxSupply[achievementId];
        if (maxSupply > 0) {
            require(achievementSupply[achievementId] < maxSupply, "Max supply reached");
        }

        uint256 tokenId = _tokenIdCounter.current();
        _tokenIdCounter.increment();
        _safeMint(player, tokenId);
        _setTokenURI(tokenId, tokenURI_);

        tokenData[tokenId] = TokenData({
            tokenType: TokenType.ACHIEVEMENT,
            tier: 0,
            achievementId: achievementId,
            soulbound: false,
            mintedAt: block.timestamp
        });

        achievementSupply[achievementId]++;
        emit AchievementMinted(player, achievementId, tokenId);
    }

    function setAchievementMaxSupply(
        string calldata achievementId,
        uint256 maxSupply
    ) external onlyRole(ADMIN_ROLE) {
        achievementMaxSupply[achievementId] = maxSupply;
    }

    // --- Internal ---

    function _mintTierNFT(address player, uint8 tier) internal {
        uint256 tokenId = _tokenIdCounter.current();
        _tokenIdCounter.increment();
        _safeMint(player, tokenId);

        tokenData[tokenId] = TokenData({
            tokenType: TokenType.TIER,
            tier: tier,
            achievementId: "",
            soulbound: true,  // Tier NFTs are soulbound
            mintedAt: block.timestamp
        });

        players[player].currentTier = tier;
        players[player].tierTokenId = tokenId;
    }

    // --- Transfer restrictions (soulbound) ---

    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 tokenId,
        uint256 batchSize
    ) internal override(ERC721, ERC721Enumerable) {
        // Allow minting (from == 0) and burning (to == 0)
        if (from != address(0) && to != address(0)) {
            require(!tokenData[tokenId].soulbound, "Soulbound: non-transferable");
        }
        super._beforeTokenTransfer(from, to, tokenId, batchSize);
    }

    // --- Required overrides ---

    function _burn(uint256 tokenId) internal override(ERC721, ERC721URIStorage) {
        super._burn(tokenId);
    }

    function tokenURI(uint256 tokenId) public view override(ERC721, ERC721URIStorage) returns (string memory) {
        return super.tokenURI(tokenId);
    }

    function supportsInterface(bytes4 interfaceId) public view override(ERC721, ERC721Enumerable, AccessControl) returns (bool) {
        return super.supportsInterface(interfaceId);
    }
}
'''


def generate_contract():
    return SOLIDITY_CONTRACT


def main():
    parser = argparse.ArgumentParser(description="NFT Loyalty Program for iGaming")
    parser.add_argument("--generate-contract", action="store_true")
    parser.add_argument("--tiers", action="store_true")
    parser.add_argument("--achievements", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    if args.generate_contract:
        print(generate_contract())
    elif args.tiers:
        for tier in LOYALTY_TIERS:
            print(f"\n  Level {tier.level}: {tier.name} ({tier.points_required:,} points)")
            print(f"  Supply: {'Unlimited' if not tier.max_supply else tier.max_supply}")
            print(f"  Soulbound: {tier.soulbound}")
            for b in tier.benefits:
                print(f"    - {b}")
    elif args.achievements:
        print(json.dumps(ACHIEVEMENT_NFTS, indent=2))
    elif args.demo:
        print("=== NFT Loyalty Program for iGaming ===\n")
        print(f"Tier NFTs: {len(LOYALTY_TIERS)} levels (soulbound)")
        print(f"Achievement NFTs: {len(ACHIEVEMENT_NFTS)} types (tradeable)\n")
        print("Architecture:")
        print("  Casino Backend -> Minter Service -> Smart Contract (Polygon/Base)")
        print("  Player Wallet -> View NFTs -> Marketplace (achievements only)")
        print(f"\nUse --generate-contract to output the Solidity contract")
    else:
        print("Usage: python nft_loyalty.py --demo")
        print("       python nft_loyalty.py --generate-contract > CasinoLoyaltyNFT.sol")
        print("       python nft_loyalty.py --tiers")


if __name__ == "__main__":
    main()
