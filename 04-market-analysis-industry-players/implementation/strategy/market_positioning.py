#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 04, Market Analysis and Industry Players.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Market Positioning Strategy and Differentiation Tool for iGaming
=================================================================

Analyzes and recommends market positioning strategies for iGaming operators.
Covers differentiation axes, competitive moats, value propositions, and
go-to-market strategy formulation.

Usage:
    python market_positioning.py --analyze
    python market_positioning.py --positioning-map
    python market_positioning.py --strategy premium
"""

import json
import logging
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Positioning frameworks
# ---------------------------------------------------------------------------

class PositioningArchetype(Enum):
    PREMIUM_VIP = "premium_vip"               # High-roller focus, exclusive experience
    MASS_MARKET = "mass_market"               # Volume-driven, broad appeal
    TECH_INNOVATOR = "tech_innovator"         # Technology-first differentiator
    NICHE_SPECIALIST = "niche_specialist"     # Single vertical expert
    REGIONAL_CHAMPION = "regional_champion"   # Dominant in specific geography
    SOCIAL_FIRST = "social_first"             # Community and social features
    SAFETY_TRUST = "safety_trust"             # Responsible gaming leader
    VALUE_LEADER = "value_leader"             # Best odds/RTP, lowest margins


class DifferentiationAxis(Enum):
    PRODUCT_BREADTH = "product_breadth"       # Number of games/betting markets
    USER_EXPERIENCE = "user_experience"       # UI/UX quality, speed
    ODDS_VALUE = "odds_value"                 # Competitive odds/RTP
    LIVE_BETTING = "live_betting"             # In-play experience
    PAYMENT_SPEED = "payment_speed"           # Withdrawal/deposit speed
    VIP_PROGRAM = "vip_program"               # Loyalty and VIP tiers
    CONTENT_EXCLUSIVITY = "content_exclusivity"  # Exclusive games/features
    BRAND_TRUST = "brand_trust"               # Safety, transparency
    MOBILE_FIRST = "mobile_first"             # Mobile app quality
    SOCIAL_FEATURES = "social_features"       # Community, multiplayer
    LOCALIZATION = "localization"             # Local language, payment, content
    STREAMING = "streaming"                   # Live streaming integration


@dataclass
class CompetitorPosition:
    """A competitor's position on key differentiation axes."""
    name: str
    archetype: PositioningArchetype
    scores: dict = field(default_factory=dict)  # axis -> score (1-10)
    primary_differentiators: list = field(default_factory=list)
    target_segments: list = field(default_factory=list)
    price_positioning: str = "mid"  # budget, mid, premium
    brand_strength: int = 5  # 1-10


@dataclass
class MarketGap:
    """An identified gap in the market."""
    axis: DifferentiationAxis
    description: str
    opportunity_size: str  # small, medium, large
    investment_required: str  # low, medium, high
    time_to_market_months: int
    risk_level: str  # low, medium, high
    potential_revenue_uplift_pct: float


@dataclass
class PositioningStrategy:
    """A recommended positioning strategy."""
    archetype: PositioningArchetype
    tagline: str
    value_proposition: str
    primary_axes: list = field(default_factory=list)
    target_segments: list = field(default_factory=list)
    key_initiatives: list = field(default_factory=list)
    investment_areas: list = field(default_factory=list)
    kpis: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    timeline_months: int = 12


# ---------------------------------------------------------------------------
# Market positioning engine
# ---------------------------------------------------------------------------

SAMPLE_COMPETITORS = [
    CompetitorPosition(
        name="Flutter/FanDuel",
        archetype=PositioningArchetype.MASS_MARKET,
        scores={
            DifferentiationAxis.PRODUCT_BREADTH: 9,
            DifferentiationAxis.USER_EXPERIENCE: 8,
            DifferentiationAxis.ODDS_VALUE: 7,
            DifferentiationAxis.LIVE_BETTING: 8,
            DifferentiationAxis.PAYMENT_SPEED: 7,
            DifferentiationAxis.VIP_PROGRAM: 7,
            DifferentiationAxis.CONTENT_EXCLUSIVITY: 8,
            DifferentiationAxis.BRAND_TRUST: 8,
            DifferentiationAxis.MOBILE_FIRST: 9,
            DifferentiationAxis.SOCIAL_FEATURES: 5,
            DifferentiationAxis.LOCALIZATION: 8,
            DifferentiationAxis.STREAMING: 7,
        },
        primary_differentiators=["Exchange model (Betfair)", "Scale & brand portfolio",
                                  "Same Game Parlay+"],
        target_segments=["mass_market", "sports_enthusiasts", "casual_casino"],
        price_positioning="mid",
        brand_strength=9,
    ),
    CompetitorPosition(
        name="bet365",
        archetype=PositioningArchetype.TECH_INNOVATOR,
        scores={
            DifferentiationAxis.PRODUCT_BREADTH: 8,
            DifferentiationAxis.USER_EXPERIENCE: 7,
            DifferentiationAxis.ODDS_VALUE: 8,
            DifferentiationAxis.LIVE_BETTING: 10,
            DifferentiationAxis.PAYMENT_SPEED: 7,
            DifferentiationAxis.VIP_PROGRAM: 6,
            DifferentiationAxis.CONTENT_EXCLUSIVITY: 5,
            DifferentiationAxis.BRAND_TRUST: 8,
            DifferentiationAxis.MOBILE_FIRST: 8,
            DifferentiationAxis.SOCIAL_FEATURES: 3,
            DifferentiationAxis.LOCALIZATION: 9,
            DifferentiationAxis.STREAMING: 10,
        },
        primary_differentiators=["Best live betting globally", "Extensive live streaming",
                                  "Proprietary technology"],
        target_segments=["sports_bettors", "live_betting_enthusiasts", "international"],
        price_positioning="mid",
        brand_strength=9,
    ),
    CompetitorPosition(
        name="Stake.com",
        archetype=PositioningArchetype.SOCIAL_FIRST,
        scores={
            DifferentiationAxis.PRODUCT_BREADTH: 7,
            DifferentiationAxis.USER_EXPERIENCE: 8,
            DifferentiationAxis.ODDS_VALUE: 7,
            DifferentiationAxis.LIVE_BETTING: 6,
            DifferentiationAxis.PAYMENT_SPEED: 9,
            DifferentiationAxis.VIP_PROGRAM: 9,
            DifferentiationAxis.CONTENT_EXCLUSIVITY: 8,
            DifferentiationAxis.BRAND_TRUST: 5,
            DifferentiationAxis.MOBILE_FIRST: 7,
            DifferentiationAxis.SOCIAL_FEATURES: 9,
            DifferentiationAxis.LOCALIZATION: 5,
            DifferentiationAxis.STREAMING: 8,
        },
        primary_differentiators=["Crypto-native", "Influencer marketing",
                                  "Community engagement", "Provably fair games"],
        target_segments=["crypto_users", "young_adults", "streamers"],
        price_positioning="budget",
        brand_strength=7,
    ),
]


class MarketPositioningEngine:
    """Engine for analyzing and recommending market positioning."""

    def __init__(self):
        self.competitors: list[CompetitorPosition] = list(SAMPLE_COMPETITORS)
        self.strategies: dict[PositioningArchetype, PositioningStrategy] = {}
        self._build_strategy_library()

    def _build_strategy_library(self):
        self.strategies = {
            PositioningArchetype.PREMIUM_VIP: PositioningStrategy(
                archetype=PositioningArchetype.PREMIUM_VIP,
                tagline="The ultimate high-stakes experience",
                value_proposition="Exclusive access, personalized service, and premium rewards "
                                  "for discerning players who expect the finest gaming experience.",
                primary_axes=[DifferentiationAxis.VIP_PROGRAM, DifferentiationAxis.CONTENT_EXCLUSIVITY,
                              DifferentiationAxis.USER_EXPERIENCE],
                target_segments=["high_rollers", "vip_players", "luxury_seekers"],
                key_initiatives=[
                    "Launch dedicated VIP account managers (1:50 ratio)",
                    "Negotiate exclusive game titles with top 5 providers",
                    "Build personalized recommendation engine",
                    "Create invite-only VIP events program",
                    "Implement instant withdrawals for VIP tier",
                    "Develop white-glove onboarding for high-value prospects",
                ],
                investment_areas=["VIP team expansion", "Exclusive content deals",
                                   "Personalization AI", "Premium UX design"],
                kpis=["VIP revenue share >40%", "VIP retention >85%",
                       "Average deposit >$500", "NPS (VIP) >60"],
                risks=["Small addressable market", "Regulatory scrutiny on high-value players",
                        "Whale dependency risk", "High operational cost per player"],
                timeline_months=12,
            ),
            PositioningArchetype.TECH_INNOVATOR: PositioningStrategy(
                archetype=PositioningArchetype.TECH_INNOVATOR,
                tagline="Where technology meets entertainment",
                value_proposition="Cutting-edge gaming technology delivering the fastest, smoothest, "
                                  "and most innovative betting experience in the industry.",
                primary_axes=[DifferentiationAxis.LIVE_BETTING, DifferentiationAxis.MOBILE_FIRST,
                              DifferentiationAxis.USER_EXPERIENCE],
                target_segments=["tech_savvy", "mobile_first_users", "live_betting_enthusiasts"],
                key_initiatives=[
                    "Build sub-100ms live betting engine",
                    "Launch AI-powered bet builder with natural language",
                    "Develop AR/VR casino experience prototype",
                    "Create real-time personalized odds",
                    "Implement WebAssembly-based instant game loading",
                    "Build social betting features (follow, copy, compete)",
                ],
                investment_areas=["Engineering team expansion", "R&D lab",
                                   "Infrastructure (edge computing)", "Patent portfolio"],
                kpis=["App load time <2s", "Live bet latency <200ms",
                       "Feature adoption rate >30%", "App store rating >4.5"],
                risks=["High R&D cost", "Feature overload", "Regulatory lag on innovation",
                        "Talent acquisition difficulty"],
                timeline_months=18,
            ),
            PositioningArchetype.SAFETY_TRUST: PositioningStrategy(
                archetype=PositioningArchetype.SAFETY_TRUST,
                tagline="Play safe, play smart",
                value_proposition="The most trusted and responsible gaming platform, where player "
                                  "protection is built into every feature.",
                primary_axes=[DifferentiationAxis.BRAND_TRUST, DifferentiationAxis.USER_EXPERIENCE,
                              DifferentiationAxis.PAYMENT_SPEED],
                target_segments=["safety_conscious", "casual_players", "regulated_market_players"],
                key_initiatives=[
                    "Implement real-time player behavior monitoring with AI",
                    "Launch transparent RTP dashboard for all games",
                    "Create industry-leading responsible gaming toolkit",
                    "Obtain GamCare, eCOGRA, and additional certifications",
                    "Build open-source responsible gaming SDK",
                    "Publish annual transparency report",
                ],
                investment_areas=["Responsible gaming AI", "Compliance team",
                                   "Player protection features", "Industry partnerships"],
                kpis=["Problem gambling rate <0.5%", "Regulator relationship score",
                       "Self-exclusion tool adoption >15%", "Zero regulatory fines"],
                risks=["Higher cost base", "Perception as restrictive",
                        "Slower feature velocity", "Premium pricing may limit market"],
                timeline_months=12,
            ),
            PositioningArchetype.MASS_MARKET: PositioningStrategy(
                archetype=PositioningArchetype.MASS_MARKET,
                tagline="Entertainment for everyone",
                value_proposition="The widest selection of games, sports, and entertainment options "
                                  "with competitive value for players of all levels.",
                primary_axes=[DifferentiationAxis.PRODUCT_BREADTH, DifferentiationAxis.ODDS_VALUE,
                              DifferentiationAxis.LOCALIZATION],
                target_segments=["mass_market", "casual_players", "sports_fans"],
                key_initiatives=[
                    "Expand to 100+ game providers",
                    "Launch localized experiences in 20+ markets",
                    "Implement dynamic promotions engine",
                    "Build cross-sell between sports and casino",
                    "Create freemium entry points (free-to-play)",
                    "Optimize CPA through performance marketing",
                ],
                investment_areas=["Marketing & acquisition", "Content partnerships",
                                   "Localization", "Promotions engine"],
                kpis=["MAU >500K", "Revenue per market >$5M", "CPA <$80",
                       "Cross-sell rate >25%"],
                risks=["Race to bottom on margins", "High marketing spend",
                        "Commoditization", "Regulatory complexity across markets"],
                timeline_months=18,
            ),
            PositioningArchetype.REGIONAL_CHAMPION: PositioningStrategy(
                archetype=PositioningArchetype.REGIONAL_CHAMPION,
                tagline="Built for [region], by [region]",
                value_proposition="Deep local expertise, local payment methods, local content, "
                                  "and local support — the best gaming experience for your market.",
                primary_axes=[DifferentiationAxis.LOCALIZATION, DifferentiationAxis.PAYMENT_SPEED,
                              DifferentiationAxis.BRAND_TRUST],
                target_segments=["local_market_players", "first_time_bettors", "mobile_users"],
                key_initiatives=[
                    "Launch with all local payment methods (PIX, M-Pesa, etc.)",
                    "Hire local content and odds teams",
                    "Partner with local sports leagues and celebrities",
                    "Build local-language customer support (24/7)",
                    "Create market-specific game content",
                    "Obtain early-mover licenses in emerging jurisdictions",
                ],
                investment_areas=["Local market teams", "Payment integrations",
                                   "Local partnerships", "Regulatory affairs"],
                kpis=["Local market share >15%", "Local NPS >40",
                       "Payment success rate >95%", "Support CSAT >85%"],
                risks=["Market size limitation", "Regulatory uncertainty",
                        "Currency/FX risk", "Local competition from incumbents"],
                timeline_months=12,
            ),
        }

    # ------ Analysis methods ------

    def find_market_gaps(self) -> list[MarketGap]:
        """Identify underserved areas in the competitive landscape."""
        gaps = []

        # Average score per axis across competitors
        axis_averages = {}
        for axis in DifferentiationAxis:
            scores = [c.scores.get(axis, 5) for c in self.competitors if axis in c.scores]
            if scores:
                axis_averages[axis] = sum(scores) / len(scores)

        # Find axes where average is low (gap opportunity)
        for axis, avg in sorted(axis_averages.items(), key=lambda x: x[1]):
            if avg < 6:
                gap = MarketGap(
                    axis=axis,
                    description=f"Competitors average {avg:.1f}/10 on {axis.value} — opportunity to differentiate",
                    opportunity_size="large" if avg < 5 else "medium",
                    investment_required="high" if axis in (DifferentiationAxis.LIVE_BETTING,
                                                           DifferentiationAxis.STREAMING) else "medium",
                    time_to_market_months=12 if avg < 5 else 6,
                    risk_level="medium",
                    potential_revenue_uplift_pct=15.0 if avg < 5 else 8.0,
                )
                gaps.append(gap)

        # Check for unoccupied archetypes
        occupied = {c.archetype for c in self.competitors}
        for arch in PositioningArchetype:
            if arch not in occupied:
                gaps.append(MarketGap(
                    axis=DifferentiationAxis.PRODUCT_BREADTH,
                    description=f"No competitor occupies the {arch.value} positioning archetype",
                    opportunity_size="large",
                    investment_required="high",
                    time_to_market_months=18,
                    risk_level="medium",
                    potential_revenue_uplift_pct=20.0,
                ))

        return gaps

    def generate_positioning_map(self) -> dict:
        """Generate data for a 2D positioning map visualization."""
        # Use two most differentiating axes
        axis_variance = {}
        for axis in DifferentiationAxis:
            scores = [c.scores.get(axis, 5) for c in self.competitors]
            if len(scores) > 1:
                mean = sum(scores) / len(scores)
                variance = sum((s - mean) ** 2 for s in scores) / len(scores)
                axis_variance[axis] = variance

        sorted_axes = sorted(axis_variance.items(), key=lambda x: x[1], reverse=True)
        x_axis = sorted_axes[0][0] if sorted_axes else DifferentiationAxis.PRODUCT_BREADTH
        y_axis = sorted_axes[1][0] if len(sorted_axes) > 1 else DifferentiationAxis.USER_EXPERIENCE

        positions = []
        for c in self.competitors:
            positions.append({
                "name": c.name,
                "archetype": c.archetype.value,
                "x": c.scores.get(x_axis, 5),
                "y": c.scores.get(y_axis, 5),
                "brand_strength": c.brand_strength,
                "price": c.price_positioning,
            })

        return {
            "x_axis": x_axis.value,
            "y_axis": y_axis.value,
            "positions": positions,
            "gaps": [{"x": x, "y": y} for x in range(1, 11) for y in range(1, 11)
                     if not any(abs(p["x"] - x) < 2 and abs(p["y"] - y) < 2 for p in positions)],
        }

    def recommend_positioning(self, priorities: Optional[list[str]] = None) -> dict:
        """Recommend a positioning strategy based on market analysis."""
        gaps = self.find_market_gaps()
        positioning_map = self.generate_positioning_map()

        # Score each strategy based on gap alignment
        strategy_scores = {}
        for arch, strategy in self.strategies.items():
            score = 50.0
            # Check if archetype is unoccupied
            occupied = {c.archetype for c in self.competitors}
            if arch not in occupied:
                score += 20

            # Check alignment with gaps
            for gap in gaps:
                if gap.axis in strategy.primary_axes:
                    score += 10 if gap.opportunity_size == "large" else 5

            # Adjust for priorities
            if priorities:
                for p in priorities:
                    if p.lower() in strategy.tagline.lower() or p.lower() in strategy.value_proposition.lower():
                        score += 15

            strategy_scores[arch] = score

        # Rank strategies
        ranked = sorted(strategy_scores.items(), key=lambda x: x[1], reverse=True)

        recommended = self.strategies[ranked[0][0]]
        alternatives = [self.strategies[r[0]] for r in ranked[1:3]]

        return {
            "recommended": asdict(recommended),
            "score": ranked[0][1],
            "alternatives": [asdict(a) for a in alternatives],
            "market_gaps": [asdict(g) for g in gaps],
            "positioning_map": positioning_map,
            "rationale": f"Recommended '{recommended.archetype.value}' positioning due to "
                         f"{len([g for g in gaps if g.axis in recommended.primary_axes])} aligned market gaps "
                         f"and {'unoccupied' if recommended.archetype not in {c.archetype for c in self.competitors} else 'competitive'} "
                         f"strategic space.",
        }

    def differentiation_scorecard(self, operator_name: str, scores: dict) -> dict:
        """Score an operator's differentiation vs competitors."""
        axes_map: dict[str, dict] = {}

        for axis in DifferentiationAxis:
            operator_score = scores.get(axis, scores.get(axis.value, 5))
            competitor_scores = [c.scores.get(axis, 5) for c in self.competitors]
            avg = sum(competitor_scores) / len(competitor_scores) if competitor_scores else 5
            best = max(competitor_scores) if competitor_scores else 5

            axes_map[axis.value] = {
                "operator_score": operator_score,
                "competitor_avg": round(avg, 1),
                "competitor_best": best,
                "vs_avg": round(operator_score - avg, 1),
                "vs_best": operator_score - best,
                "position": "leader" if operator_score >= best else
                            "competitive" if operator_score >= avg else "lagging",
            }

        leading = sum(1 for v in axes_map.values() if v["position"] == "leader")
        competitive = sum(1 for v in axes_map.values() if v["position"] == "competitive")
        lagging = sum(1 for v in axes_map.values() if v["position"] == "lagging")

        return {
            "operator": operator_name,
            "axes": axes_map,
            "summary": {
                "leading_axes": leading,
                "competitive_axes": competitive,
                "lagging_axes": lagging,
                "overall_position": "strong" if leading >= 4 else "competitive" if lagging <= 3 else "weak",
            },
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="iGaming Market Positioning Strategy Tool")
    parser.add_argument("--analyze", action="store_true", help="Full market analysis")
    parser.add_argument("--gaps", action="store_true", help="Find market gaps")
    parser.add_argument("--positioning-map", action="store_true", help="Generate positioning map data")
    parser.add_argument("--strategy", type=str, help="View specific strategy archetype")
    parser.add_argument("--recommend", action="store_true", help="Get positioning recommendation")
    parser.add_argument("--priorities", nargs="*", help="Priorities for recommendation")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    engine = MarketPositioningEngine()

    if args.gaps:
        gaps = engine.find_market_gaps()
        result = [asdict(g) for g in gaps]
    elif args.positioning_map:
        result = engine.generate_positioning_map()
    elif args.strategy:
        try:
            arch = PositioningArchetype(args.strategy)
            result = asdict(engine.strategies[arch])
        except (ValueError, KeyError):
            print(f"Unknown strategy. Options: {[a.value for a in PositioningArchetype]}")
            return
    elif args.recommend:
        result = engine.recommend_positioning(args.priorities)
    elif args.analyze:
        result = {
            "competitors": len(engine.competitors),
            "recommendation": engine.recommend_positioning(args.priorities),
        }
    else:
        print("=== iGaming Market Positioning Tool ===\n")
        print("Available archetypes:")
        for arch in PositioningArchetype:
            strategy = engine.strategies.get(arch)
            tag = f' — "{strategy.tagline}"' if strategy else ""
            occupied = any(c.archetype == arch for c in engine.competitors)
            status = " [OCCUPIED]" if occupied else " [OPEN]"
            print(f"  {arch.value:25s}{tag}{status}")

        gaps = engine.find_market_gaps()
        print(f"\nMarket gaps identified: {len(gaps)}")
        for g in gaps[:5]:
            print(f"  - {g.axis.value}: {g.description}")
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
