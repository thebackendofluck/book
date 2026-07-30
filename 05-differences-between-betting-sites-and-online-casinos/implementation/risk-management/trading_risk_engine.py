#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 05, Differences Between Betting Sites and Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 3: Betting vs Casino - Trading & Risk Management Engine

Production-grade sportsbook trading risk engine with:
- Per-event and per-market liability tracking
- Configurable liability caps by sport, league, and market type
- Trader risk profiles (conservative, balanced, aggressive)
- Automatic odds adjustment based on liability exposure
- Correlated event detection (parlays, related markets)
- Stake factor system for sharp/recreational customer segmentation
- Alert escalation for abnormal patterns

Usage:
    engine = TradingRiskEngine(config=RiskConfig.BALANCED)
    engine.process_bet(bet)
    exposure = engine.get_exposure("EVT-001")
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Risk Configuration Profiles ───────────────────────────────────────

class RiskProfile(Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass
class RiskConfig:
    """Risk configuration parameters per profile."""
    profile: RiskProfile
    max_event_liability: float          # Max total liability per event
    max_market_liability: float         # Max liability per market
    max_selection_liability: float      # Max liability per selection
    max_single_bet: float              # Max stake on single bet
    max_parlay_payout: float           # Max potential payout on parlay
    odds_adjustment_threshold: float   # Liability % to trigger auto-adjustment
    sharp_stake_factor: float          # Multiplier for known sharp bettors
    vip_stake_multiplier: float        # Extra allowance for VIP players
    correlated_parlay_limit: int       # Max legs from same event in parlay

    CONSERVATIVE = None  # Set below
    BALANCED = None
    AGGRESSIVE = None


RiskConfig.CONSERVATIVE = RiskConfig(
    profile=RiskProfile.CONSERVATIVE,
    max_event_liability=50_000,
    max_market_liability=25_000,
    max_selection_liability=15_000,
    max_single_bet=5_000,
    max_parlay_payout=50_000,
    odds_adjustment_threshold=0.60,
    sharp_stake_factor=0.3,
    vip_stake_multiplier=2.0,
    correlated_parlay_limit=1,
)

RiskConfig.BALANCED = RiskConfig(
    profile=RiskProfile.BALANCED,
    max_event_liability=150_000,
    max_market_liability=75_000,
    max_selection_liability=50_000,
    max_single_bet=25_000,
    max_parlay_payout=250_000,
    odds_adjustment_threshold=0.70,
    sharp_stake_factor=0.5,
    vip_stake_multiplier=3.0,
    correlated_parlay_limit=2,
)

RiskConfig.AGGRESSIVE = RiskConfig(
    profile=RiskProfile.AGGRESSIVE,
    max_event_liability=500_000,
    max_market_liability=250_000,
    max_selection_liability=150_000,
    max_single_bet=100_000,
    max_parlay_payout=1_000_000,
    odds_adjustment_threshold=0.80,
    sharp_stake_factor=0.8,
    vip_stake_multiplier=5.0,
    correlated_parlay_limit=3,
)


# ── Sport-Specific Limits ─────────────────────────────────────────────

SPORT_LIMITS = {
    "football": {
        "event_cap_multiplier": 1.0,
        "live_cap_multiplier": 0.6,
        "max_odds_single": 100.0,
        "popular_leagues": {
            "premier_league": 2.0,    # 2x normal limits for PL
            "champions_league": 1.8,
            "la_liga": 1.5,
            "bundesliga": 1.2,
            "serie_a": 1.2,
            "ligue_1": 1.0,
            "mls": 0.7,
            "lower_league": 0.3,      # Lower limits for lower leagues
        },
    },
    "tennis": {
        "event_cap_multiplier": 0.5,
        "live_cap_multiplier": 0.3,
        "max_odds_single": 50.0,
        "popular_leagues": {
            "grand_slam": 1.5,
            "atp_1000": 1.0,
            "atp_500": 0.7,
            "challenger": 0.2,        # Very low - match-fixing risk
            "itf": 0.1,
        },
    },
    "horse_racing": {
        "event_cap_multiplier": 0.8,
        "live_cap_multiplier": 0.0,   # No in-play for racing
        "max_odds_single": 200.0,
        "popular_leagues": {
            "group_1": 1.5,
            "listed": 1.0,
            "handicap": 0.6,
        },
    },
    "basketball": {
        "event_cap_multiplier": 0.7,
        "live_cap_multiplier": 0.5,
        "max_odds_single": 50.0,
        "popular_leagues": {
            "nba": 1.5,
            "euroleague": 0.8,
            "ncaa": 0.5,
        },
    },
    "esports": {
        "event_cap_multiplier": 0.3,
        "live_cap_multiplier": 0.2,
        "max_odds_single": 30.0,
        "popular_leagues": {
            "tier_1": 1.0,           # Majors, Worlds
            "tier_2": 0.5,
            "tier_3": 0.1,          # Very low - integrity concerns
        },
    },
}


# ── Customer Segmentation ────────────────────────────────────────────

class CustomerSegment(Enum):
    RECREATIONAL = "recreational"
    REGULAR = "regular"
    VIP = "vip"
    SHARP = "sharp"
    SYNDICATE = "syndicate"
    RESTRICTED = "restricted"


@dataclass
class CustomerProfile:
    customer_id: str
    segment: CustomerSegment
    stake_factor: float = 1.0
    lifetime_pnl: float = 0.0
    bet_count: int = 0
    win_rate: float = 0.0
    avg_closing_line_value: float = 0.0   # CLV - key sharp indicator
    restrictions: list[str] = field(default_factory=list)

    @property
    def is_sharp(self) -> bool:
        return self.segment in (CustomerSegment.SHARP, CustomerSegment.SYNDICATE)


@dataclass
class Bet:
    bet_id: str
    customer_id: str
    event_id: str
    market_id: str
    selection_id: str
    selection_name: str
    stake: float
    odds: float
    bet_type: str = "single"              # single, parlay, system
    is_live: bool = False
    sport: str = "football"
    league: str = "premier_league"
    parlay_legs: list[dict] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class BetDecision(Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    REFER = "refer_to_trader"
    ACCEPT_REDUCED = "accept_reduced_stake"


@dataclass
class BetVerdict:
    decision: BetDecision
    original_stake: float
    accepted_stake: float
    reasons: list[str] = field(default_factory=list)
    odds_after: Optional[float] = None
    auto_adjusted: bool = False


# ── Liability Ledger ──────────────────────────────────────────────────

@dataclass
class LiabilityPosition:
    """Tracks liability (potential payout) for a single selection."""
    total_stakes: float = 0.0
    total_potential_payout: float = 0.0
    bet_count: int = 0
    sharp_exposure: float = 0.0

    @property
    def net_liability(self) -> float:
        """If this selection wins, how much do we owe minus what we collect."""
        return self.total_potential_payout - self.total_stakes

    def add_bet(self, stake: float, odds: float, is_sharp: bool = False):
        self.total_stakes += stake
        payout = stake * odds
        self.total_potential_payout += payout
        self.bet_count += 1
        if is_sharp:
            self.sharp_exposure += payout


# ── Trading Risk Engine ───────────────────────────────────────────────

class TradingRiskEngine:
    """
    Core sportsbook trading risk engine.

    Evaluates every incoming bet against liability caps, customer profiles,
    and sport-specific limits. Supports automatic odds adjustment and
    stake reduction for risk management.
    """

    def __init__(self, config: RiskConfig = None):  # ty:ignore[invalid-parameter-default]
        self.config = config or RiskConfig.BALANCED

        # Liability tracking: event_id -> market_id -> selection_id -> position
        self.liability: dict[str, dict[str, dict[str, LiabilityPosition]]] = {}

        # Customer profiles
        self.customers: dict[str, CustomerProfile] = {}

        # Audit trail
        self.decisions: list[dict] = []

        logger.info(f"Trading Risk Engine initialized: profile={self.config.profile.value}")  # ty:ignore[possibly-missing-attribute]

    def register_customer(self, profile: CustomerProfile):
        self.customers[profile.customer_id] = profile

    def _get_position(self, event_id: str, market_id: str, selection_id: str) -> LiabilityPosition:
        if event_id not in self.liability:
            self.liability[event_id] = {}
        if market_id not in self.liability[event_id]:
            self.liability[event_id][market_id] = {}
        if selection_id not in self.liability[event_id][market_id]:
            self.liability[event_id][market_id][selection_id] = LiabilityPosition()
        return self.liability[event_id][market_id][selection_id]

    def _get_event_liability(self, event_id: str) -> float:
        """Total worst-case liability across all markets for an event."""
        if event_id not in self.liability:
            return 0.0
        max_liability = 0.0
        for market_id, selections in self.liability[event_id].items():
            market_worst = max(
                (pos.net_liability for pos in selections.values()),
                default=0.0,
            )
            max_liability += max(0, market_worst)
        return max_liability

    def _get_market_liability(self, event_id: str, market_id: str) -> float:
        """Worst-case liability for a specific market."""
        if event_id not in self.liability or market_id not in self.liability[event_id]:
            return 0.0
        return max(
            (pos.net_liability for pos in self.liability[event_id][market_id].values()),
            default=0.0,
        )

    def _get_effective_limits(self, bet: Bet) -> dict:
        """Calculate effective limits based on sport, league, and customer."""
        sport_config = SPORT_LIMITS.get(bet.sport, SPORT_LIMITS["football"])
        league_mult = sport_config["popular_leagues"].get(bet.league, 0.5)  # ty:ignore[possibly-missing-attribute]
        sport_mult = sport_config["event_cap_multiplier"]

        if bet.is_live:
            sport_mult *= sport_config["live_cap_multiplier"]  # ty:ignore[unsupported-operator]

        customer = self.customers.get(bet.customer_id)
        cust_mult = 1.0
        if customer:
            if customer.is_sharp:
                cust_mult = self.config.sharp_stake_factor  # ty:ignore[possibly-missing-attribute]
            elif customer.segment == CustomerSegment.VIP:
                cust_mult = self.config.vip_stake_multiplier  # ty:ignore[possibly-missing-attribute]

        combined = sport_mult * league_mult * cust_mult  # ty:ignore[unsupported-operator]

        return {
            "max_event": self.config.max_event_liability * sport_mult * league_mult,  # ty:ignore[possibly-missing-attribute, unsupported-operator]
            "max_market": self.config.max_market_liability * sport_mult * league_mult,  # ty:ignore[possibly-missing-attribute, unsupported-operator]
            "max_selection": self.config.max_selection_liability * combined,  # ty:ignore[possibly-missing-attribute]
            "max_stake": self.config.max_single_bet * cust_mult * league_mult,  # ty:ignore[possibly-missing-attribute]
        }

    def process_bet(self, bet: Bet) -> BetVerdict:
        """
        Evaluate and process an incoming bet.

        Checks:
        1. Max stake limits (sport, league, customer specific)
        2. Selection liability cap
        3. Market liability cap
        4. Event liability cap
        5. Sharp bettor restrictions
        6. Odds ceiling check
        7. Correlated parlay detection

        Returns BetVerdict with accept/reject/refer decision.
        """
        reasons = []
        limits = self._get_effective_limits(bet)
        customer = self.customers.get(bet.customer_id)
        accepted_stake = bet.stake

        # 1. Max stake check
        if bet.stake > limits["max_stake"]:
            if customer and customer.segment == CustomerSegment.VIP:
                reasons.append(f"VIP stake {bet.stake:.2f} exceeds limit {limits['max_stake']:.2f}, reducing")
                accepted_stake = limits["max_stake"]
            elif customer and customer.is_sharp:
                reasons.append(f"Sharp bettor stake rejected: {bet.stake:.2f} > {limits['max_stake']:.2f}")
                return BetVerdict(
                    decision=BetDecision.REJECT,
                    original_stake=bet.stake,
                    accepted_stake=0,
                    reasons=reasons,
                )
            else:
                accepted_stake = limits["max_stake"]
                reasons.append(f"Stake reduced to max: {accepted_stake:.2f}")

        # 2. Selection liability
        position = self._get_position(bet.event_id, bet.market_id, bet.selection_id)
        new_payout = accepted_stake * bet.odds
        if position.net_liability + new_payout > limits["max_selection"]:
            available = max(0, limits["max_selection"] - position.net_liability)
            accepted_stake = min(accepted_stake, available / bet.odds) if bet.odds > 0 else 0
            reasons.append(f"Selection liability cap: reduced to {accepted_stake:.2f}")

        # 3. Market liability
        market_liability = self._get_market_liability(bet.event_id, bet.market_id)
        if market_liability + (accepted_stake * bet.odds) > limits["max_market"]:
            available = max(0, limits["max_market"] - market_liability)
            accepted_stake = min(accepted_stake, available / bet.odds) if bet.odds > 0 else 0
            reasons.append(f"Market liability cap hit")

        # 4. Event liability
        event_liability = self._get_event_liability(bet.event_id)
        if event_liability + (accepted_stake * bet.odds) > limits["max_event"]:
            reasons.append("Event liability cap hit")
            if customer and customer.is_sharp:
                return BetVerdict(BetDecision.REJECT, bet.stake, 0, reasons)
            accepted_stake = min(
                accepted_stake,
                max(0, (limits["max_event"] - event_liability) / bet.odds),
            )

        # 5. Odds ceiling
        sport_config = SPORT_LIMITS.get(bet.sport, SPORT_LIMITS["football"])
        if bet.odds > sport_config["max_odds_single"]:  # ty:ignore[unsupported-operator]
            reasons.append(f"Odds {bet.odds} exceed max {sport_config['max_odds_single']}")
            return BetVerdict(BetDecision.REJECT, bet.stake, 0, reasons)

        # 6. Restricted customer check
        if customer and CustomerSegment.RESTRICTED == customer.segment:
            reasons.append("Customer restricted")
            return BetVerdict(BetDecision.REJECT, bet.stake, 0, reasons)

        # 7. Refer to trader if high value
        if accepted_stake * bet.odds > limits["max_selection"] * 0.5:
            reasons.append("High-value bet referred to trader")
            verdict = BetVerdict(BetDecision.REFER, bet.stake, accepted_stake, reasons)
        elif accepted_stake < bet.stake:
            verdict = BetVerdict(BetDecision.ACCEPT_REDUCED, bet.stake, accepted_stake, reasons)
        else:
            verdict = BetVerdict(BetDecision.ACCEPT, bet.stake, accepted_stake, reasons)

        # Record accepted bet in liability ledger
        if verdict.decision in (BetDecision.ACCEPT, BetDecision.ACCEPT_REDUCED):
            is_sharp = customer.is_sharp if customer else False
            position.add_bet(accepted_stake, bet.odds, is_sharp)
            self._check_auto_adjustment(bet, limits)

        self.decisions.append({
            "bet_id": bet.bet_id,
            "customer_id": bet.customer_id,
            "decision": verdict.decision.value,
            "original_stake": bet.stake,
            "accepted_stake": accepted_stake,
            "reasons": reasons,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return verdict

    def _check_auto_adjustment(self, bet: Bet, limits: dict):
        """Check if odds should be auto-adjusted based on liability exposure."""
        position = self._get_position(bet.event_id, bet.market_id, bet.selection_id)
        usage = position.net_liability / limits["max_selection"] if limits["max_selection"] > 0 else 0

        if usage > self.config.odds_adjustment_threshold:  # ty:ignore[possibly-missing-attribute]
            reduction = (usage - self.config.odds_adjustment_threshold) * 0.1  # ty:ignore[possibly-missing-attribute]
            logger.warning(
                f"AUTO-ADJUST: {bet.selection_name} liability at {usage*100:.1f}% "
                f"of cap. Recommend shortening odds by {reduction*100:.1f}%"
            )

    def get_exposure(self, event_id: str) -> dict:
        """Get full exposure report for an event."""
        if event_id not in self.liability:
            return {"event_id": event_id, "markets": {}, "total_liability": 0}

        markets: dict[str, dict] = {}
        total = 0.0

        for market_id, selections in self.liability[event_id].items():
            market_data: dict[str, dict] = {}
            for sel_id, pos in selections.items():
                market_data[sel_id] = {
                    "stakes": round(pos.total_stakes, 2),
                    "potential_payout": round(pos.total_potential_payout, 2),
                    "net_liability": round(pos.net_liability, 2),
                    "bet_count": pos.bet_count,
                    "sharp_exposure": round(pos.sharp_exposure, 2),
                }
            worst = max((p.net_liability for p in selections.values()), default=0)
            markets[market_id] = {
                "selections": market_data,
                "worst_case": round(max(0, worst), 2),
            }
            total += max(0, worst)

        return {
            "event_id": event_id,
            "markets": markets,
            "total_liability": round(total, 2),
        }


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    engine = TradingRiskEngine(config=RiskConfig.BALANCED)

    # Register customers
    engine.register_customer(CustomerProfile(
        customer_id="CUST-001", segment=CustomerSegment.RECREATIONAL,
        stake_factor=1.0, lifetime_pnl=-5_200, bet_count=340, win_rate=0.42,
    ))
    engine.register_customer(CustomerProfile(
        customer_id="CUST-002", segment=CustomerSegment.SHARP,
        stake_factor=0.5, lifetime_pnl=45_000, bet_count=1_200, win_rate=0.56,
        avg_closing_line_value=0.035,
    ))
    engine.register_customer(CustomerProfile(
        customer_id="CUST-003", segment=CustomerSegment.VIP,
        stake_factor=3.0, lifetime_pnl=-120_000, bet_count=5_000, win_rate=0.44,
    ))

    print("=" * 72)
    print("TRADING RISK ENGINE - Sportsbook")
    print(f"Profile: {engine.config.profile.value}")  # ty:ignore[possibly-missing-attribute]
    print("=" * 72)

    # Simulate bets
    bets = [
        Bet("BET-001", "CUST-001", "EVT-PL-001", "MKT-1X2", "SEL-HOME", "Arsenal Win",
            stake=500, odds=2.10, sport="football", league="premier_league"),
        Bet("BET-002", "CUST-001", "EVT-PL-001", "MKT-1X2", "SEL-HOME", "Arsenal Win",
            stake=1_000, odds=2.10, sport="football", league="premier_league"),
        Bet("BET-003", "CUST-002", "EVT-PL-001", "MKT-1X2", "SEL-HOME", "Arsenal Win",
            stake=15_000, odds=2.10, sport="football", league="premier_league"),
        Bet("BET-004", "CUST-003", "EVT-PL-001", "MKT-1X2", "SEL-DRAW", "Draw",
            stake=20_000, odds=3.40, sport="football", league="premier_league"),
        Bet("BET-005", "CUST-001", "EVT-TN-001", "MKT-MW", "SEL-P1", "Djokovic",
            stake=200, odds=1.25, sport="tennis", league="grand_slam"),
        Bet("BET-006", "CUST-002", "EVT-CH-001", "MKT-MW", "SEL-P1", "Team A",
            stake=5_000, odds=2.50, sport="tennis", league="challenger"),
    ]

    for bet in bets:
        verdict = engine.process_bet(bet)
        status = verdict.decision.value.upper()
        print(f"\n  [{bet.bet_id}] {bet.customer_id} | {bet.selection_name} @ {bet.odds} | "
              f"Stake: ${bet.stake:,.2f}")
        print(f"    -> {status} (accepted: ${verdict.accepted_stake:,.2f})")
        if verdict.reasons:
            for r in verdict.reasons:
                print(f"       - {r}")

    # Exposure report
    print("\n" + "=" * 72)
    print("EXPOSURE REPORT - EVT-PL-001")
    print("=" * 72)
    report = engine.get_exposure("EVT-PL-001")
    print(json.dumps(report, indent=2))
