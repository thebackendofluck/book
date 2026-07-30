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
Real-Time Player Personalization Engine for iGaming Platforms
==============================================================

Delivers personalized game suggestions, bonus offers, and UI layout
adaptations based on real-time player profiles. Designed for sub-100ms
response times at casino-scale traffic.

Covers:
- Player profile construction from behavioral signals
- Multi-armed bandit game recommendation (Thompson Sampling)
- Context-aware bonus offer selection
- UI layout optimization per player segment
- Responsible gambling guardrails (no personalization for at-risk players)

Feasibility Assessment:
- Thompson Sampling requires only beta distribution sampling (stdlib math)
- Player profiles are aggregated from event streams (Kafka -> Redis)
- Sub-100ms latency achievable with in-memory profile cache
- Responsible gambling constraints are hard-coded, not ML-driven
- No external dependencies for core; optional: numpy for faster sampling

Dependencies: None for core
"""

import math
import json
import random
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

class PlayerSegment(Enum):
    NEW_PLAYER = "new_player"           # 0-7 days
    CASUAL = "casual"                   # low frequency, low stakes
    REGULAR = "regular"                 # moderate frequency
    VIP = "vip"                         # high value, high frequency
    DORMANT = "dormant"                 # no activity 30+ days
    AT_RISK = "at_risk"                 # responsible gambling flags


class GameCategory(Enum):
    SLOTS = "slots"
    TABLE_GAMES = "table_games"
    LIVE_CASINO = "live_casino"
    SPORTS_BETTING = "sports_betting"
    POKER = "poker"
    SCRATCH_CARDS = "scratch_cards"
    BINGO = "bingo"
    VIRTUAL_SPORTS = "virtual_sports"


class BonusType(Enum):
    DEPOSIT_MATCH = "deposit_match"
    FREE_SPINS = "free_spins"
    CASHBACK = "cashback"
    NO_DEPOSIT = "no_deposit"
    RELOAD = "reload"
    LOYALTY_REWARD = "loyalty_reward"
    TOURNAMENT_ENTRY = "tournament_entry"


class UILayout(Enum):
    SLOTS_FOCUSED = "slots_focused"
    TABLE_FOCUSED = "table_focused"
    LIVE_FOCUSED = "live_focused"
    SPORTS_FOCUSED = "sports_focused"
    BALANCED = "balanced"
    MINIMAL = "minimal"  # for at-risk players


@dataclass
class PlayerProfile:
    """Real-time player profile aggregated from behavioral signals."""
    player_id: str
    segment: PlayerSegment
    preferred_categories: list[GameCategory] = field(default_factory=list)
    avg_session_duration_min: float = 0.0
    avg_bet_size: float = 0.0
    total_deposits_30d: float = 0.0
    sessions_last_7d: int = 0
    favorite_games: list[str] = field(default_factory=list)
    preferred_currency: str = "EUR"
    jurisdiction: str = "UK"
    language: str = "en"
    device_type: str = "mobile"
    registration_days: int = 0
    lifetime_value: float = 0.0
    risk_score: float = 0.0  # 0.0 = safe, 1.0 = high risk
    deposit_limit_set: bool = False
    self_exclusion_history: bool = False
    last_active: str = ""
    time_of_day_preference: str = "evening"  # morning, afternoon, evening, night


@dataclass
class GameRecommendation:
    game_id: str
    game_name: str
    category: GameCategory
    score: float
    reason: str
    provider: str = ""
    rtp: float = 0.0
    volatility: str = "medium"


@dataclass
class BonusOffer:
    offer_id: str
    bonus_type: BonusType
    value: float
    currency: str
    wagering_requirement: float
    expiry_hours: int
    target_segment: PlayerSegment
    reason: str
    terms_summary: str


@dataclass
class PersonalizationResponse:
    """Complete personalization payload returned to the frontend."""
    player_id: str
    segment: PlayerSegment
    recommended_games: list[GameRecommendation] = field(default_factory=list)
    bonus_offers: list[BonusOffer] = field(default_factory=list)
    ui_layout: UILayout = UILayout.BALANCED
    hero_banner: dict = field(default_factory=dict)
    responsible_gambling_active: bool = False
    processing_time_ms: float = 0.0


# ---------------------------------------------------------------------------
# Thompson Sampling recommender
# ---------------------------------------------------------------------------

class ThompsonSamplingRecommender:
    """
    Multi-armed bandit recommender using Thompson Sampling.
    Each game is a "arm"; we track (successes, failures) per player-game pair.
    Successes = clicks/plays, failures = impressions without click.

    Beta(alpha, beta) prior, where alpha = 1 + successes, beta = 1 + failures.
    """

    def __init__(self):
        # {player_id: {game_id: (successes, failures)}}
        self._arms: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)

    def record_impression(self, player_id: str, game_id: str, clicked: bool):
        """Record that a game was shown and whether the player engaged."""
        current = self._arms[player_id].get(game_id, (0, 0))
        if clicked:
            self._arms[player_id][game_id] = (current[0] + 1, current[1])
        else:
            self._arms[player_id][game_id] = (current[0], current[1] + 1)

    def sample_scores(self, player_id: str, game_ids: list[str]) -> dict[str, float]:
        """
        Sample from Beta distribution for each game.
        Returns {game_id: sampled_score} for ranking.
        """
        scores = {}
        player_arms = self._arms.get(player_id, {})

        for game_id in game_ids:
            successes, failures = player_arms.get(game_id, (0, 0))
            alpha = 1 + successes
            beta_param = 1 + failures
            # Beta distribution sampling using inverse CDF approximation
            scores[game_id] = self._sample_beta(alpha, beta_param)

        return scores

    def _sample_beta(self, alpha: float, beta: float) -> float:
        """Sample from Beta(alpha, beta) using gamma distribution relationship."""
        # Beta(a,b) = Gamma(a,1) / (Gamma(a,1) + Gamma(b,1))
        x = random.gammavariate(alpha, 1.0)
        y = random.gammavariate(beta, 1.0)
        return x / (x + y) if (x + y) > 0 else 0.5


# ---------------------------------------------------------------------------
# Game catalog (simulated)
# ---------------------------------------------------------------------------

GAME_CATALOG = [
    {"id": "starburst", "name": "Starburst", "category": GameCategory.SLOTS, "provider": "NetEnt", "rtp": 96.1, "volatility": "low", "tags": ["classic", "gems"]},
    {"id": "book-of-dead", "name": "Book of Dead", "category": GameCategory.SLOTS, "provider": "Play'n GO", "rtp": 96.21, "volatility": "high", "tags": ["adventure", "egypt"]},
    {"id": "gonzo-quest", "name": "Gonzo's Quest", "category": GameCategory.SLOTS, "provider": "NetEnt", "rtp": 95.97, "volatility": "medium", "tags": ["adventure", "avalanche"]},
    {"id": "mega-moolah", "name": "Mega Moolah", "category": GameCategory.SLOTS, "provider": "Microgaming", "rtp": 88.12, "volatility": "high", "tags": ["jackpot", "progressive"]},
    {"id": "sweet-bonanza", "name": "Sweet Bonanza", "category": GameCategory.SLOTS, "provider": "Pragmatic Play", "rtp": 96.48, "volatility": "high", "tags": ["cluster", "candy"]},
    {"id": "blackjack-classic", "name": "Classic Blackjack", "category": GameCategory.TABLE_GAMES, "provider": "Evolution", "rtp": 99.5, "volatility": "low", "tags": ["classic", "strategy"]},
    {"id": "european-roulette", "name": "European Roulette", "category": GameCategory.TABLE_GAMES, "provider": "Evolution", "rtp": 97.3, "volatility": "medium", "tags": ["classic", "roulette"]},
    {"id": "baccarat-pro", "name": "Baccarat Pro", "category": GameCategory.TABLE_GAMES, "provider": "Playtech", "rtp": 98.94, "volatility": "low", "tags": ["classic", "baccarat"]},
    {"id": "live-lightning-roulette", "name": "Lightning Roulette", "category": GameCategory.LIVE_CASINO, "provider": "Evolution", "rtp": 97.3, "volatility": "medium", "tags": ["live", "roulette", "multiplier"]},
    {"id": "live-crazy-time", "name": "Crazy Time", "category": GameCategory.LIVE_CASINO, "provider": "Evolution", "rtp": 95.5, "volatility": "high", "tags": ["live", "gameshow"]},
    {"id": "live-blackjack-vip", "name": "VIP Blackjack", "category": GameCategory.LIVE_CASINO, "provider": "Evolution", "rtp": 99.5, "volatility": "low", "tags": ["live", "vip", "blackjack"]},
    {"id": "dream-catcher", "name": "Dream Catcher", "category": GameCategory.LIVE_CASINO, "provider": "Evolution", "rtp": 96.58, "volatility": "medium", "tags": ["live", "gameshow", "wheel"]},
]


# ---------------------------------------------------------------------------
# Personalization engine
# ---------------------------------------------------------------------------

class PersonalizationEngine:
    """
    Core personalization engine that combines collaborative filtering signals,
    contextual bandits, and business rules to produce real-time recommendations.

    Architecture:
        Event stream (Kafka) -> Profile builder (Flink) -> Redis profile cache
        -> this engine (API call, <100ms) -> Frontend rendering

    Responsible gambling guardrails:
        - Players with risk_score > 0.7 get NO personalized bonus offers
        - At-risk players see simplified UI with prominent RG tools
        - Self-excluded players are never targeted
    """

    RISK_THRESHOLD = 0.7  # above this, no personalization of offers

    def __init__(self):
        self.recommender = ThompsonSamplingRecommender()
        self.game_catalog = {g["id"]: g for g in GAME_CATALOG}
        self._offer_counter = 0

    def personalize(self, profile: PlayerProfile) -> PersonalizationResponse:
        """
        Generate a complete personalization response for a player.
        This is the main entry point, called on every page load / session start.
        """
        import time
        start = time.monotonic()

        response = PersonalizationResponse(
            player_id=profile.player_id,
            segment=profile.segment,
        )

        # Responsible gambling check
        if profile.risk_score > self.RISK_THRESHOLD or profile.self_exclusion_history:
            response.responsible_gambling_active = True
            response.ui_layout = UILayout.MINIMAL
            response.recommended_games = self._safe_recommendations(profile)
            response.hero_banner = {
                "type": "responsible_gambling",
                "title": "Play Responsibly",
                "message": "Set deposit limits, take a break, or self-exclude anytime.",
                "cta_url": "/responsible-gambling",
            }
            response.processing_time_ms = (time.monotonic() - start) * 1000
            return response

        # Game recommendations
        response.recommended_games = self._recommend_games(profile)

        # Bonus offers
        response.bonus_offers = self._select_bonus_offers(profile)

        # UI layout
        response.ui_layout = self._select_layout(profile)

        # Hero banner
        response.hero_banner = self._select_hero_banner(profile)

        response.processing_time_ms = (time.monotonic() - start) * 1000
        return response

    def _recommend_games(self, profile: PlayerProfile, count: int = 8) -> list[GameRecommendation]:
        """
        Recommend games using Thompson Sampling + content-based filtering.
        Combines exploration (new games) with exploitation (known preferences).
        """
        # Get all game IDs
        all_game_ids = list(self.game_catalog.keys())

        # Thompson Sampling scores
        ts_scores = self.recommender.sample_scores(profile.player_id, all_game_ids)

        # Content-based boost for preferred categories
        category_boost = {}
        for game_id, game in self.game_catalog.items():
            boost = 0.0
            if game["category"] in profile.preferred_categories:
                boost += 0.2
            if game_id in profile.favorite_games:
                boost += 0.15
            # Device-appropriate: mobile players prefer simpler games
            if profile.device_type == "mobile" and game["volatility"] == "low":
                boost += 0.05
            # VIP boost for high-value games
            if profile.segment == PlayerSegment.VIP and "vip" in game.get("tags", []):  # ty:ignore[unsupported-operator]
                boost += 0.1
            category_boost[game_id] = boost

        # Combined scores
        combined = {}
        for game_id in all_game_ids:
            combined[game_id] = ts_scores.get(game_id, 0.5) + category_boost.get(game_id, 0.0)

        # Sort by combined score
        ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)

        recommendations = []
        seen_categories = set()

        for game_id, score in ranked:
            if len(recommendations) >= count:
                break

            game = self.game_catalog[game_id]

            # Diversity: no more than 3 games from same category
            cat = game["category"]
            cat_count = sum(1 for r in recommendations if r.category == cat)
            if cat_count >= 3:
                continue

            reason = self._generate_reason(game, profile)
            recommendations.append(GameRecommendation(
                game_id=game_id,
                game_name=game["name"],  # ty:ignore[invalid-argument-type]
                category=game["category"],  # ty:ignore[invalid-argument-type]
                score=round(score, 4),
                reason=reason,
                provider=game["provider"],  # ty:ignore[invalid-argument-type]
                rtp=game["rtp"],  # ty:ignore[invalid-argument-type]
                volatility=game["volatility"],  # ty:ignore[invalid-argument-type]
            ))

        return recommendations

    def _safe_recommendations(self, profile: PlayerProfile) -> list[GameRecommendation]:
        """
        For at-risk players: recommend only low-volatility, high-RTP games.
        No jackpot games, no high-volatility slots.
        """
        safe_games = [
            g for g in GAME_CATALOG
            if g["volatility"] == "low" and g["rtp"] >= 96.0  # ty:ignore[unsupported-operator]
        ]

        return [
            GameRecommendation(
                game_id=g["id"],  # ty:ignore[invalid-argument-type]
                game_name=g["name"],  # ty:ignore[invalid-argument-type]
                category=g["category"],  # ty:ignore[invalid-argument-type]
                score=g["rtp"] / 100.0,  # ty:ignore[unsupported-operator]
                reason="Low-volatility game with fair return rate",
                provider=g["provider"],  # ty:ignore[invalid-argument-type]
                rtp=g["rtp"],  # ty:ignore[invalid-argument-type]
                volatility=g["volatility"],  # ty:ignore[invalid-argument-type]
            )
            for g in safe_games[:4]
        ]

    def _select_bonus_offers(self, profile: PlayerProfile) -> list[BonusOffer]:
        """
        Select contextually appropriate bonus offers.
        Rules engine with segment-based targeting and guardrails.
        """
        offers = []

        # No offers for at-risk players (already handled in personalize())
        # but double-check here
        if profile.risk_score > self.RISK_THRESHOLD:
            return []

        segment_offers = {
            PlayerSegment.NEW_PLAYER: [
                (BonusType.DEPOSIT_MATCH, 100, 35, 168, "Welcome bonus for new players"),
                (BonusType.FREE_SPINS, 50, 40, 72, "Explore our slots collection"),
            ],
            PlayerSegment.CASUAL: [
                (BonusType.FREE_SPINS, 20, 30, 48, "Weekend fun spins"),
                (BonusType.CASHBACK, 10, 1, 168, "Safety net on your play"),
            ],
            PlayerSegment.REGULAR: [
                (BonusType.RELOAD, 50, 25, 72, "Reload and keep playing"),
                (BonusType.TOURNAMENT_ENTRY, 0, 0, 48, "Free tournament entry"),
            ],
            PlayerSegment.VIP: [
                (BonusType.DEPOSIT_MATCH, 200, 20, 168, "Exclusive VIP match"),
                (BonusType.LOYALTY_REWARD, 100, 1, 336, "Thank you for your loyalty"),
                (BonusType.CASHBACK, 15, 1, 168, "VIP cashback on losses"),
            ],
            PlayerSegment.DORMANT: [
                (BonusType.NO_DEPOSIT, 10, 50, 48, "We miss you - come back"),
                (BonusType.FREE_SPINS, 30, 35, 72, "Try our newest games"),
            ],
        }

        segment_list = segment_offers.get(profile.segment, [])

        for bonus_type, value, wagering, expiry, reason in segment_list:
            self._offer_counter += 1
            offers.append(BonusOffer(
                offer_id=f"OFFER-{self._offer_counter:05d}",
                bonus_type=bonus_type,
                value=value,
                currency=profile.preferred_currency,
                wagering_requirement=wagering,
                expiry_hours=expiry,
                target_segment=profile.segment,
                reason=reason,
                terms_summary=f"{wagering}x wagering, expires in {expiry}h, "
                             f"max bet {profile.preferred_currency} 5 during wagering",
            ))

        return offers

    def _select_layout(self, profile: PlayerProfile) -> UILayout:
        """Select UI layout based on player preferences."""
        if not profile.preferred_categories:
            return UILayout.BALANCED

        top_category = profile.preferred_categories[0]
        layout_map = {
            GameCategory.SLOTS: UILayout.SLOTS_FOCUSED,
            GameCategory.TABLE_GAMES: UILayout.TABLE_FOCUSED,
            GameCategory.LIVE_CASINO: UILayout.LIVE_FOCUSED,
            GameCategory.SPORTS_BETTING: UILayout.SPORTS_FOCUSED,
        }
        return layout_map.get(top_category, UILayout.BALANCED)

    def _select_hero_banner(self, profile: PlayerProfile) -> dict:
        """Select hero banner content based on segment and context."""
        banners = {
            PlayerSegment.NEW_PLAYER: {
                "type": "welcome",
                "title": "Welcome to the Casino!",
                "message": "Claim your welcome bonus and explore 2000+ games.",
                "cta_text": "Claim Bonus",
                "cta_url": "/bonus/welcome",
            },
            PlayerSegment.VIP: {
                "type": "vip_exclusive",
                "title": "VIP Exclusive Event",
                "message": "Join this week's VIP tournament with a 50,000 prize pool.",
                "cta_text": "Join Tournament",
                "cta_url": "/tournaments/vip-weekly",
            },
            PlayerSegment.DORMANT: {
                "type": "comeback",
                "title": "Welcome Back!",
                "message": "We've added 200+ new games since your last visit.",
                "cta_text": "Explore New Games",
                "cta_url": "/games/new",
            },
        }
        return banners.get(profile.segment, {
            "type": "featured",
            "title": "Featured This Week",
            "message": "Try our newest live casino game shows.",
            "cta_text": "Play Now",
            "cta_url": "/games/featured",
        })

    def _generate_reason(self, game: dict, profile: PlayerProfile) -> str:
        """Generate a human-readable reason for the recommendation."""
        reasons = []
        if game["category"] in profile.preferred_categories:
            reasons.append(f"matches your preference for {game['category'].value}")
        if game["id"] in profile.favorite_games:
            reasons.append("one of your favorites")
        if game["rtp"] > 97:
            reasons.append(f"high RTP ({game['rtp']}%)")
        if "jackpot" in game.get("tags", []):
            reasons.append("progressive jackpot available")
        if not reasons:
            reasons.append("popular with similar players")
        return "; ".join(reasons).capitalize()


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    """Simulate personalization for different player segments."""
    random.seed(42)

    engine = PersonalizationEngine()

    print("\n" + "=" * 70)
    print("  Real-Time Player Personalization Engine - Simulation")
    print("=" * 70)

    # Simulate interaction history for Thompson Sampling
    for _ in range(200):
        player_id = f"PLR-{random.randint(1, 5):05d}"
        game_id = random.choice(list(engine.game_catalog.keys()))
        clicked = random.random() < 0.3
        engine.recommender.record_impression(player_id, game_id, clicked)

    # Test profiles
    profiles = [
        PlayerProfile(
            player_id="PLR-00001",
            segment=PlayerSegment.NEW_PLAYER,
            preferred_categories=[GameCategory.SLOTS],
            avg_session_duration_min=15,
            avg_bet_size=2.50,
            total_deposits_30d=50,
            sessions_last_7d=3,
            device_type="mobile",
            jurisdiction="UK",
            registration_days=3,
            risk_score=0.1,
        ),
        PlayerProfile(
            player_id="PLR-00002",
            segment=PlayerSegment.VIP,
            preferred_categories=[GameCategory.LIVE_CASINO, GameCategory.TABLE_GAMES],
            avg_session_duration_min=120,
            avg_bet_size=150,
            total_deposits_30d=15000,
            sessions_last_7d=12,
            favorite_games=["live-blackjack-vip", "baccarat-pro"],
            device_type="desktop",
            jurisdiction="Malta",
            registration_days=730,
            lifetime_value=85000,
            risk_score=0.2,
        ),
        PlayerProfile(
            player_id="PLR-00003",
            segment=PlayerSegment.AT_RISK,
            preferred_categories=[GameCategory.SLOTS],
            avg_session_duration_min=240,
            avg_bet_size=50,
            total_deposits_30d=3000,
            sessions_last_7d=20,
            device_type="mobile",
            jurisdiction="UK",
            registration_days=180,
            risk_score=0.85,
            deposit_limit_set=True,
        ),
        PlayerProfile(
            player_id="PLR-00004",
            segment=PlayerSegment.DORMANT,
            preferred_categories=[GameCategory.SPORTS_BETTING],
            avg_session_duration_min=0,
            sessions_last_7d=0,
            device_type="mobile",
            jurisdiction="Sweden",
            registration_days=400,
            risk_score=0.15,
        ),
    ]

    for profile in profiles:
        result = engine.personalize(profile)

        print(f"\n  {'=' * 60}")
        print(f"  Player: {result.player_id} | Segment: {result.segment.value}")
        print(f"  UI Layout: {result.ui_layout.value}")
        print(f"  RG Active: {result.responsible_gambling_active}")
        print(f"  Processing: {result.processing_time_ms:.2f}ms")

        if result.hero_banner:
            print(f"  Banner: [{result.hero_banner.get('type', 'N/A')}] "
                  f"{result.hero_banner.get('title', '')}")

        if result.recommended_games:
            print(f"\n  Game Recommendations ({len(result.recommended_games)}):")
            for g in result.recommended_games[:5]:
                print(f"    - {g.game_name} ({g.category.value}) "
                      f"RTP:{g.rtp}% vol:{g.volatility} score:{g.score:.3f}")
                print(f"      Reason: {g.reason}")

        if result.bonus_offers:
            print(f"\n  Bonus Offers ({len(result.bonus_offers)}):")
            for o in result.bonus_offers:
                print(f"    - [{o.bonus_type.value}] {o.currency} {o.value} "
                      f"({o.wagering_requirement}x wagering, {o.expiry_hours}h)")
                print(f"      {o.reason}")
        elif result.responsible_gambling_active:
            print("\n  Bonus Offers: SUPPRESSED (responsible gambling mode)")

    print(f"\n  {'=' * 60}")
    print("\n  Production architecture:")
    print("    Kafka events -> Flink profile builder -> Redis cache")
    print("    -> this engine (API, <100ms) -> React frontend")
    print("    -> A/B testing via LaunchDarkly feature flags")
    print("    -> Grafana dashboard for recommendation performance\n")


if __name__ == "__main__":
    demo()
