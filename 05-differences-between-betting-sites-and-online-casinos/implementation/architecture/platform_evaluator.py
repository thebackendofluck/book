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
Chapter 3: Betting vs Casino - Platform Architecture Decision Tool

Systematic evaluation framework for choosing between betting-only, casino-only,
or hybrid platform architectures. Scores options across multiple dimensions:
- Technical complexity and time-to-market
- Regulatory compliance requirements
- Infrastructure costs (build vs buy)
- Team expertise requirements
- Integration ecosystem (providers, feeds, payments)
- Scalability and growth potential
- Risk profile assessment

Usage:
    evaluator = PlatformEvaluator()
    evaluator.set_context(jurisdiction="uk", budget=2_000_000, team_size=20)
    result = evaluator.evaluate()
    print(result.recommendation)
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class PlatformType(Enum):
    BETTING_ONLY = "betting_only"
    CASINO_ONLY = "casino_only"
    HYBRID = "hybrid"


class BuildApproach(Enum):
    BUILD = "build"              # Custom development
    BUY = "buy"                  # Turnkey platform
    HYBRID_BUILD = "hybrid"      # Core custom + buy components


class TeamExpertise(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── Scoring Criteria ──────────────────────────────────────────────────

@dataclass
class CriterionScore:
    name: str
    weight: float          # 0-1 importance weight
    betting_score: float   # 1-10 score for betting-only
    casino_score: float    # 1-10 score for casino-only
    hybrid_score: float    # 1-10 score for hybrid
    notes: str = ""

    @property
    def weighted_betting(self) -> float:
        return self.betting_score * self.weight

    @property
    def weighted_casino(self) -> float:
        return self.casino_score * self.weight

    @property
    def weighted_hybrid(self) -> float:
        return self.hybrid_score * self.weight


# ── Architecture Components ──────────────────────────────────────────

BETTING_COMPONENTS = {
    "odds_engine": {
        "name": "Odds Compilation Engine",
        "build_cost": 300_000, "build_months": 6,
        "buy_cost_monthly": 8_000,
        "complexity": "high",
        "required": True,
    },
    "trading_platform": {
        "name": "Trading & Risk Management",
        "build_cost": 500_000, "build_months": 9,
        "buy_cost_monthly": 12_000,
        "complexity": "high",
        "required": True,
    },
    "data_feeds": {
        "name": "Live Data Feeds (Betradar/LSports)",
        "build_cost": 0, "build_months": 0,
        "buy_cost_monthly": 15_000,
        "complexity": "low",
        "required": True,
    },
    "bet_settlement": {
        "name": "Bet Settlement Engine",
        "build_cost": 200_000, "build_months": 4,
        "buy_cost_monthly": 5_000,
        "complexity": "medium",
        "required": True,
    },
    "live_betting": {
        "name": "In-Play Betting Module",
        "build_cost": 400_000, "build_months": 8,
        "buy_cost_monthly": 10_000,
        "complexity": "high",
        "required": False,
    },
    "cash_out": {
        "name": "Cash Out Engine",
        "build_cost": 150_000, "build_months": 3,
        "buy_cost_monthly": 4_000,
        "complexity": "medium",
        "required": False,
    },
    "bet_builder": {
        "name": "Bet Builder / SGP",
        "build_cost": 250_000, "build_months": 5,
        "buy_cost_monthly": 7_000,
        "complexity": "high",
        "required": False,
    },
}

CASINO_COMPONENTS = {
    "game_aggregation": {
        "name": "Game Aggregation Platform (GAP)",
        "build_cost": 200_000, "build_months": 4,
        "buy_cost_monthly": 3_000,
        "complexity": "medium",
        "required": True,
    },
    "rgs_integration": {
        "name": "Remote Game Server Integration",
        "build_cost": 100_000, "build_months": 3,
        "buy_cost_monthly": 0,   # Per-provider integration
        "complexity": "medium",
        "required": True,
    },
    "bonus_engine": {
        "name": "Bonus & Promotion Engine",
        "build_cost": 250_000, "build_months": 5,
        "buy_cost_monthly": 6_000,
        "complexity": "high",
        "required": True,
    },
    "rng_certification": {
        "name": "RNG Testing & Certification",
        "build_cost": 50_000, "build_months": 2,
        "buy_cost_monthly": 0,
        "complexity": "low",
        "required": True,
    },
    "live_casino": {
        "name": "Live Casino Integration (Evolution/Pragmatic)",
        "build_cost": 80_000, "build_months": 2,
        "buy_cost_monthly": 0,   # Revenue share model
        "complexity": "low",
        "required": False,
    },
    "jackpot_system": {
        "name": "Progressive Jackpot System",
        "build_cost": 150_000, "build_months": 3,
        "buy_cost_monthly": 2_000,
        "complexity": "medium",
        "required": False,
    },
}

SHARED_COMPONENTS = {
    "player_account": {
        "name": "Player Account Management (PAM)",
        "build_cost": 300_000, "build_months": 5,
        "buy_cost_monthly": 8_000,
        "complexity": "high",
        "required": True,
    },
    "kyc_aml": {
        "name": "KYC/AML Compliance",
        "build_cost": 150_000, "build_months": 3,
        "buy_cost_monthly": 5_000,
        "complexity": "medium",
        "required": True,
    },
    "payment_gateway": {
        "name": "Payment Processing Gateway",
        "build_cost": 200_000, "build_months": 4,
        "buy_cost_monthly": 3_000,
        "complexity": "medium",
        "required": True,
    },
    "responsible_gambling": {
        "name": "Responsible Gambling Tools",
        "build_cost": 100_000, "build_months": 2,
        "buy_cost_monthly": 2_000,
        "complexity": "medium",
        "required": True,
    },
    "crm_marketing": {
        "name": "CRM & Marketing Automation",
        "build_cost": 200_000, "build_months": 4,
        "buy_cost_monthly": 5_000,
        "complexity": "medium",
        "required": True,
    },
    "analytics_bi": {
        "name": "Analytics & BI Platform",
        "build_cost": 150_000, "build_months": 3,
        "buy_cost_monthly": 4_000,
        "complexity": "medium",
        "required": False,
    },
    "frontend_web": {
        "name": "Web Frontend (Responsive)",
        "build_cost": 250_000, "build_months": 4,
        "buy_cost_monthly": 0,   # White-label included in PAM buy
        "complexity": "medium",
        "required": True,
    },
    "mobile_apps": {
        "name": "Native Mobile Apps (iOS/Android)",
        "build_cost": 400_000, "build_months": 6,
        "buy_cost_monthly": 0,
        "complexity": "high",
        "required": False,
    },
}


@dataclass
class OperatorContext:
    """Context information about the operator for scoring."""
    jurisdiction: str = "uk"
    total_budget: float = 2_000_000
    monthly_opex_budget: float = 150_000
    team_size: int = 20
    betting_expertise: TeamExpertise = TeamExpertise.MEDIUM
    casino_expertise: TeamExpertise = TeamExpertise.MEDIUM
    time_to_market_months: int = 12
    target_markets: list[str] = field(default_factory=lambda: ["uk"])
    has_existing_license: bool = False
    build_approach: BuildApproach = BuildApproach.HYBRID_BUILD
    priority_live_betting: bool = True
    priority_mobile: bool = True


@dataclass
class EvaluationResult:
    platform_type: PlatformType
    total_score: float
    criteria_scores: list[dict]
    architecture_cost: dict
    team_requirements: dict
    time_to_market: dict
    risk_assessment: dict
    components: list[dict]


class PlatformEvaluator:
    """
    Decision tool for evaluating platform architecture choices.

    Analyzes operator context and produces scored recommendations
    across betting-only, casino-only, and hybrid architectures.
    """

    def __init__(self):
        self.context = OperatorContext()

    def set_context(self, **kwargs):
        """Set operator context parameters."""
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)
            else:
                logger.warning(f"Unknown context parameter: {key}")

    def _build_criteria(self) -> list[CriterionScore]:
        """Build scoring criteria based on operator context."""
        ctx = self.context

        # Adjust scores based on context
        expertise_bonus = {TeamExpertise.LOW: -1, TeamExpertise.MEDIUM: 0, TeamExpertise.HIGH: 1}
        bet_exp = expertise_bonus[ctx.betting_expertise]
        cas_exp = expertise_bonus[ctx.casino_expertise]

        budget_tier = "high" if ctx.total_budget > 3_000_000 else "medium" if ctx.total_budget > 1_000_000 else "low"

        criteria = [
            CriterionScore(
                name="Time to Market",
                weight=0.15,
                betting_score=5 + bet_exp,
                casino_score=7 + cas_exp,
                hybrid_score=4,
                notes="Casino typically faster with turnkey providers; hybrid requires both verticals"
            ),
            CriterionScore(
                name="Technical Complexity",
                weight=0.12,
                betting_score=4,     # Odds, trading, live = complex
                casino_score=7,      # Integration-heavy but less custom logic
                hybrid_score=3,      # Both complexities combined
                notes="Sportsbook requires real-time trading expertise; casino is integration-focused"
            ),
            CriterionScore(
                name="Revenue Potential (3yr)",
                weight=0.18,
                betting_score=7,
                casino_score=7,
                hybrid_score=9,     # Cross-sell advantage
                notes="Hybrid benefits from 15-20% cross-sell uplift and improved retention"
            ),
            CriterionScore(
                name="Regulatory Compliance",
                weight=0.10,
                betting_score=6,
                casino_score=7,
                hybrid_score=5,
                notes="Both require licensing; hybrid needs compliance for both verticals"
            ),
            CriterionScore(
                name="Team & Expertise Required",
                weight=0.10,
                betting_score=5 + bet_exp,
                casino_score=7 + cas_exp,
                hybrid_score=4 + min(bet_exp, cas_exp),
                notes="Betting needs traders; casino needs provider relationship managers"
            ),
            CriterionScore(
                name="Infrastructure Cost",
                weight=0.10,
                betting_score=5 if budget_tier != "low" else 3,
                casino_score=7 if budget_tier != "low" else 5,
                hybrid_score=4 if budget_tier == "high" else 2,
                notes="Hybrid shares infrastructure but total cost is higher"
            ),
            CriterionScore(
                name="Competitive Differentiation",
                weight=0.08,
                betting_score=6,
                casino_score=5,     # Harder to differentiate in casino
                hybrid_score=8,     # One-stop shop is strong positioning
                notes="Hybrid offers unique cross-product promotions and user experience"
            ),
            CriterionScore(
                name="Scalability",
                weight=0.07,
                betting_score=6,
                casino_score=8,     # Game providers handle scaling
                hybrid_score=7,
                notes="Casino scales via provider integration; betting needs infrastructure investment"
            ),
            CriterionScore(
                name="Player Retention",
                weight=0.10,
                betting_score=7,    # Event-driven engagement
                casino_score=6,
                hybrid_score=9,     # Multiple engagement hooks
                notes="Hybrid retains players through diverse product offering"
            ),
        ]

        # Clamp scores to 1-10
        for c in criteria:
            c.betting_score = max(1, min(10, c.betting_score))
            c.casino_score = max(1, min(10, c.casino_score))
            c.hybrid_score = max(1, min(10, c.hybrid_score))

        return criteria

    def _estimate_costs(self, platform: PlatformType) -> dict:
        """Estimate build and operating costs for each platform type."""
        components = dict(SHARED_COMPONENTS)

        if platform == PlatformType.BETTING_ONLY:
            components.update(BETTING_COMPONENTS)
        elif platform == PlatformType.CASINO_ONLY:
            components.update(CASINO_COMPONENTS)
        else:  # HYBRID
            components.update(BETTING_COMPONENTS)
            components.update(CASINO_COMPONENTS)

        build_cost = sum(c["build_cost"] for c in components.values() if c["required"])
        optional_build = sum(c["build_cost"] for c in components.values() if not c["required"])
        monthly_buy = sum(c["buy_cost_monthly"] for c in components.values())
        max_build_months = max((c["build_months"] for c in components.values() if c["required"]), default=0)

        # Build vs Buy cost comparison (24 months)
        build_total_24m = build_cost  # One-time
        buy_total_24m = monthly_buy * 24

        return {
            "build_cost_required": build_cost,
            "build_cost_optional": optional_build,
            "build_cost_total": build_cost + optional_build,
            "buy_monthly_cost": monthly_buy,
            "build_timeline_months": max_build_months,
            "build_total_24m": build_total_24m,
            "buy_total_24m": buy_total_24m,
            "recommended_approach": "build" if build_total_24m < buy_total_24m else "buy",
            "component_count": len(components),
            "required_components": sum(1 for c in components.values() if c["required"]),
        }

    def _estimate_team(self, platform: PlatformType) -> dict:
        """Estimate team requirements per platform type."""
        base_team = {
            "engineering": {"backend": 3, "frontend": 2, "devops": 1, "qa": 1},
            "operations": {"compliance": 1, "customer_support": 3, "payments": 1},
            "management": {"product": 1, "project": 1},
            "marketing": {"acquisition": 1, "crm": 1, "content": 1},
        }

        if platform == PlatformType.BETTING_ONLY:
            base_team["trading"] = {"head_trader": 1, "traders": 3, "risk_analyst": 1}
            base_team["engineering"]["backend"] += 2  # Odds/trading systems
        elif platform == PlatformType.CASINO_ONLY:
            base_team["casino_ops"] = {"game_manager": 1, "provider_relations": 1, "vip_manager": 1}
        else:  # HYBRID
            base_team["trading"] = {"head_trader": 1, "traders": 3, "risk_analyst": 1}
            base_team["casino_ops"] = {"game_manager": 1, "provider_relations": 1, "vip_manager": 1}
            base_team["engineering"]["backend"] += 3
            base_team["engineering"]["frontend"] += 1

        total = sum(sum(roles.values()) for roles in base_team.values())
        annual_cost = total * 70_000  # Average annual salary estimate

        return {
            "departments": base_team,
            "total_headcount": total,
            "estimated_annual_cost": annual_cost,
            "feasible_with_current_team": total <= self.context.team_size,
            "hiring_needed": max(0, total - self.context.team_size),
        }

    def _assess_risk(self, platform: PlatformType) -> dict:
        """Risk assessment per platform type."""
        risks = {
            PlatformType.BETTING_ONLY: {
                "regulatory_risk": "MEDIUM - Single vertical, simpler compliance",
                "technology_risk": "HIGH - Real-time trading systems, latency-critical",
                "market_risk": "MEDIUM - Seasonal (sport calendars), event-dependent revenue",
                "fraud_risk": "HIGH - Matched betting, arbitrage, insider info",
                "operational_risk": "HIGH - Trading errors can cause significant losses",
                "overall": "MEDIUM-HIGH",
                "score": 6,
            },
            PlatformType.CASINO_ONLY: {
                "regulatory_risk": "MEDIUM - Game certification, RTP monitoring",
                "technology_risk": "LOW - Provider-hosted games, integration-focused",
                "market_risk": "LOW - Consistent year-round demand, no seasonality",
                "fraud_risk": "MEDIUM - Bonus abuse, multi-accounting",
                "operational_risk": "LOW - Less real-time decision making",
                "overall": "LOW-MEDIUM",
                "score": 4,
            },
            PlatformType.HYBRID: {
                "regulatory_risk": "HIGH - Dual compliance, complex bonus rules",
                "technology_risk": "HIGH - Both systems + integration complexity",
                "market_risk": "LOW - Diversified revenue streams",
                "fraud_risk": "HIGH - Cross-product exploitation vectors",
                "operational_risk": "MEDIUM - More complex but diversified",
                "overall": "MEDIUM",
                "score": 5,
            },
        }
        return risks[platform]

    def evaluate(self) -> dict:
        """
        Run full platform evaluation and return recommendation.

        Returns scored comparison across all three platform types
        with detailed analysis per dimension.
        """
        criteria = self._build_criteria()

        results = {}
        for platform in PlatformType:
            scores_attr = {
                PlatformType.BETTING_ONLY: "weighted_betting",
                PlatformType.CASINO_ONLY: "weighted_casino",
                PlatformType.HYBRID: "weighted_hybrid",
            }

            total_score = sum(getattr(c, scores_attr[platform]) for c in criteria)
            costs = self._estimate_costs(platform)
            team = self._estimate_team(platform)
            risk = self._assess_risk(platform)

            # Budget feasibility check
            feasible = costs["build_cost_required"] <= self.context.total_budget

            results[platform.value] = EvaluationResult(
                platform_type=platform,
                total_score=round(total_score, 2),
                criteria_scores=[
                    {
                        "criterion": c.name,
                        "weight": c.weight,
                        "score": getattr(c, scores_attr[platform].replace("weighted_", "") + "_score"),
                        "weighted_score": round(getattr(c, scores_attr[platform]), 3),
                        "notes": c.notes,
                    }
                    for c in criteria
                ],
                architecture_cost=costs,
                team_requirements=team,
                time_to_market={
                    "build_months": costs["build_timeline_months"],
                    "within_target": costs["build_timeline_months"] <= self.context.time_to_market_months,
                },
                risk_assessment=risk,
                components=[],
            )

        # Determine recommendation
        ranked = sorted(results.items(), key=lambda x: x[1].total_score, reverse=True)
        winner = ranked[0]

        recommendation = {
            "recommended_platform": winner[0],
            "confidence": "HIGH" if winner[1].total_score - ranked[1][1].total_score > 0.5 else "MEDIUM",
            "score_gap": round(winner[1].total_score - ranked[1][1].total_score, 2),
            "rationale": self._generate_rationale(winner[0], results),
        }

        # Build output
        output = {
            "context": {
                "jurisdiction": self.context.jurisdiction,
                "budget": self.context.total_budget,
                "team_size": self.context.team_size,
                "time_to_market": self.context.time_to_market_months,
                "betting_expertise": self.context.betting_expertise.value,
                "casino_expertise": self.context.casino_expertise.value,
            },
            "recommendation": recommendation,
            "scores": {
                name: {
                    "total_score": r.total_score,
                    "criteria": r.criteria_scores,
                    "cost_summary": {
                        "build_cost": r.architecture_cost["build_cost_required"],
                        "monthly_opex": r.architecture_cost["buy_monthly_cost"],
                        "timeline_months": r.architecture_cost["build_timeline_months"],
                    },
                    "team_headcount": r.team_requirements["total_headcount"],
                    "risk_level": r.risk_assessment["overall"],
                }
                for name, r in results.items()
            },
        }

        return output

    def _generate_rationale(self, winner: str, results: dict) -> str:
        """Generate human-readable recommendation rationale."""
        r = results[winner]
        if winner == "hybrid":
            return (
                f"Hybrid platform recommended (score: {r.total_score:.1f}/10). "
                f"Despite higher complexity and cost (${r.architecture_cost['build_cost_required']:,.0f}), "
                f"the cross-sell revenue uplift (15-20%), improved player retention, "
                f"and competitive differentiation outweigh the additional investment. "
                f"Requires {r.team_requirements['total_headcount']} FTEs and "
                f"{r.architecture_cost['build_timeline_months']} months to build."
            )
        elif winner == "casino_only":
            return (
                f"Casino-only platform recommended (score: {r.total_score:.1f}/10). "
                f"Lower technical risk, faster time-to-market "
                f"({r.architecture_cost['build_timeline_months']} months), "
                f"and lower build cost (${r.architecture_cost['build_cost_required']:,.0f}). "
                f"Best for teams with casino expertise and limited trading capability."
            )
        else:
            return (
                f"Betting-only platform recommended (score: {r.total_score:.1f}/10). "
                f"Strong fit for teams with trading expertise. "
                f"Build cost: ${r.architecture_cost['build_cost_required']:,.0f}, "
                f"timeline: {r.architecture_cost['build_timeline_months']} months. "
                f"Higher operational risk from trading but strong differentiation potential."
            )


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 72)
    print("PLATFORM ARCHITECTURE DECISION TOOL - iGaming")
    print("=" * 72)

    # Scenario 1: Well-funded UK operator with balanced expertise
    print("\n--- Scenario 1: Well-funded UK startup ---")
    evaluator = PlatformEvaluator()
    evaluator.set_context(
        jurisdiction="uk",
        total_budget=3_000_000,
        monthly_opex_budget=200_000,
        team_size=25,
        betting_expertise=TeamExpertise.MEDIUM,
        casino_expertise=TeamExpertise.MEDIUM,
        time_to_market_months=12,
    )
    result = evaluator.evaluate()
    print(f"\nRecommendation: {result['recommendation']['recommended_platform']}")
    print(f"Confidence: {result['recommendation']['confidence']}")
    print(f"Rationale: {result['recommendation']['rationale']}")
    print(f"\nScores:")
    for name, data in result["scores"].items():
        print(f"  {name:15s}: {data['total_score']:.2f} | "
              f"Build: ${data['cost_summary']['build_cost']:>10,.0f} | "
              f"Team: {data['team_headcount']} | "
              f"Risk: {data['risk_level']}")

    # Scenario 2: Small team, casino-focused
    print("\n\n--- Scenario 2: Small casino-focused team ---")
    evaluator2 = PlatformEvaluator()
    evaluator2.set_context(
        jurisdiction="malta",
        total_budget=800_000,
        team_size=10,
        betting_expertise=TeamExpertise.LOW,
        casino_expertise=TeamExpertise.HIGH,
        time_to_market_months=6,
    )
    result2 = evaluator2.evaluate()
    print(f"\nRecommendation: {result2['recommendation']['recommended_platform']}")
    print(f"Confidence: {result2['recommendation']['confidence']}")
    print(f"Rationale: {result2['recommendation']['rationale']}")

    # Scenario 3: Trading-heavy team, Curacao
    print("\n\n--- Scenario 3: Betting-focused, Curacao ---")
    evaluator3 = PlatformEvaluator()
    evaluator3.set_context(
        jurisdiction="curacao",
        total_budget=1_500_000,
        team_size=18,
        betting_expertise=TeamExpertise.HIGH,
        casino_expertise=TeamExpertise.LOW,
        time_to_market_months=9,
    )
    result3 = evaluator3.evaluate()
    print(f"\nRecommendation: {result3['recommendation']['recommended_platform']}")
    print(f"Confidence: {result3['recommendation']['confidence']}")
    print(f"Rationale: {result3['recommendation']['rationale']}")

    # Full detail dump for scenario 1
    print("\n\n--- Full Evaluation Detail (Scenario 1) ---")
    print(json.dumps(result, indent=2))
