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
Innovation Portfolio Management and Prioritization for iGaming
================================================================

Manages an innovation portfolio for gambling operators using a
weighted scoring framework. Evaluates innovation initiatives across
business impact, technical feasibility, regulatory risk, and
strategic alignment.

Usage:
    python innovation_portfolio.py --demo
    python innovation_portfolio.py --portfolio
    python innovation_portfolio.py --prioritize
"""

import json
import logging
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class InnovationHorizon(Enum):
    H1 = "h1"  # Core: optimize existing (0-12 months)
    H2 = "h2"  # Adjacent: extend to new areas (12-24 months)
    H3 = "h3"  # Transformational: new capabilities (24-36+ months)


class InnovationStatus(Enum):
    IDEATION = "ideation"
    RESEARCH = "research"
    PROTOTYPE = "prototype"
    PILOT = "pilot"
    SCALING = "scaling"
    LAUNCHED = "launched"
    PARKED = "parked"


@dataclass
class InnovationInitiative:
    id: str
    name: str
    description: str
    horizon: InnovationHorizon
    status: InnovationStatus
    owner: str
    # Scoring dimensions (1-10)
    revenue_impact: int = 5
    cost_reduction: int = 5
    player_experience: int = 5
    competitive_advantage: int = 5
    technical_feasibility: int = 5
    regulatory_risk: int = 5          # 1=high risk, 10=no risk
    time_to_value_months: int = 12
    investment_usd: int = 0
    # Tags
    technologies: list = field(default_factory=list)
    gambling_vertical: str = ""       # casino, sports, poker, all
    dependencies: list = field(default_factory=list)


SCORING_WEIGHTS = {
    "revenue_impact": 0.20,
    "cost_reduction": 0.10,
    "player_experience": 0.20,
    "competitive_advantage": 0.15,
    "technical_feasibility": 0.15,
    "regulatory_risk": 0.20,
}


SAMPLE_PORTFOLIO = [
    InnovationInitiative("INN-001", "AI-Powered Responsible Gaming",
        "ML models detecting problem gambling patterns in real-time with proactive interventions",
        InnovationHorizon.H1, InnovationStatus.PILOT, "Head of Responsible Gaming",
        revenue_impact=3, cost_reduction=6, player_experience=8,
        competitive_advantage=8, technical_feasibility=7, regulatory_risk=9,
        time_to_value_months=6, investment_usd=300000,
        technologies=["ML", "Real-time streaming", "Behavioral analytics"],
        gambling_vertical="all"),

    InnovationInitiative("INN-002", "Natural Language Bet Builder",
        "Players describe bets in natural language, AI constructs the bet slip",
        InnovationHorizon.H1, InnovationStatus.PROTOTYPE, "VP Product (Sports)",
        revenue_impact=7, cost_reduction=3, player_experience=9,
        competitive_advantage=9, technical_feasibility=6, regulatory_risk=7,
        time_to_value_months=9, investment_usd=500000,
        technologies=["NLP", "LLM", "Sports data APIs"],
        gambling_vertical="sports"),

    InnovationInitiative("INN-003", "Blockchain-Verified Game Outcomes",
        "Record game outcomes on blockchain for transparent provably fair verification",
        InnovationHorizon.H2, InnovationStatus.RESEARCH, "CTO",
        revenue_impact=4, cost_reduction=2, player_experience=6,
        competitive_advantage=7, technical_feasibility=7, regulatory_risk=5,
        time_to_value_months=18, investment_usd=400000,
        technologies=["Blockchain", "Smart contracts", "Zero-knowledge proofs"],
        gambling_vertical="casino"),

    InnovationInitiative("INN-004", "AR Live Casino Experience",
        "Augmented reality overlay for live casino tables via mobile device",
        InnovationHorizon.H3, InnovationStatus.IDEATION, "Head of Innovation",
        revenue_impact=6, cost_reduction=1, player_experience=10,
        competitive_advantage=10, technical_feasibility=3, regulatory_risk=4,
        time_to_value_months=30, investment_usd=2000000,
        technologies=["AR", "WebXR", "3D rendering", "Computer vision"],
        gambling_vertical="casino"),

    InnovationInitiative("INN-005", "Dynamic Personalized Odds",
        "ML-optimized odds margins personalized to player risk profile and behavior",
        InnovationHorizon.H1, InnovationStatus.PROTOTYPE, "Head of Trading",
        revenue_impact=9, cost_reduction=5, player_experience=7,
        competitive_advantage=8, technical_feasibility=6, regulatory_risk=6,
        time_to_value_months=8, investment_usd=600000,
        technologies=["ML", "Real-time pricing", "Player modeling"],
        gambling_vertical="sports"),

    InnovationInitiative("INN-006", "NFT Loyalty Program",
        "Blockchain-based loyalty with tradeable achievement NFTs and tier membership",
        InnovationHorizon.H2, InnovationStatus.RESEARCH, "VP CRM",
        revenue_impact=5, cost_reduction=3, player_experience=7,
        competitive_advantage=6, technical_feasibility=7, regulatory_risk=5,
        time_to_value_months=15, investment_usd=350000,
        technologies=["Blockchain", "NFT", "ERC-721", "Layer 2"],
        gambling_vertical="all"),

    InnovationInitiative("INN-007", "Voice-Controlled Betting",
        "Place bets and navigate casino via voice commands (Alexa/Google/native)",
        InnovationHorizon.H2, InnovationStatus.IDEATION, "VP Product",
        revenue_impact=4, cost_reduction=2, player_experience=7,
        competitive_advantage=6, technical_feasibility=5, regulatory_risk=4,
        time_to_value_months=18, investment_usd=400000,
        technologies=["Voice AI", "NLP", "Smart speaker SDKs"],
        gambling_vertical="sports"),

    InnovationInitiative("INN-008", "Edge Computing for Live Betting",
        "Deploy odds calculation and bet validation at edge PoPs for <30ms latency",
        InnovationHorizon.H1, InnovationStatus.PILOT, "VP Engineering",
        revenue_impact=7, cost_reduction=4, player_experience=8,
        competitive_advantage=7, technical_feasibility=8, regulatory_risk=8,
        time_to_value_months=6, investment_usd=450000,
        technologies=["Edge computing", "CDN", "WASM", "Kubernetes"],
        gambling_vertical="sports"),

    InnovationInitiative("INN-009", "Post-Quantum Cryptography",
        "Migrate critical crypto (RNG, game signing) to quantum-resistant algorithms",
        InnovationHorizon.H2, InnovationStatus.RESEARCH, "CISO",
        revenue_impact=1, cost_reduction=1, player_experience=1,
        competitive_advantage=5, technical_feasibility=6, regulatory_risk=10,
        time_to_value_months=24, investment_usd=400000,
        technologies=["PQC", "ML-KEM", "ML-DSA", "HSM"],
        gambling_vertical="all"),

    InnovationInitiative("INN-010", "Metaverse Casino",
        "Virtual casino environment in metaverse platforms with social features",
        InnovationHorizon.H3, InnovationStatus.IDEATION, "Head of Innovation",
        revenue_impact=5, cost_reduction=1, player_experience=9,
        competitive_advantage=8, technical_feasibility=2, regulatory_risk=3,
        time_to_value_months=36, investment_usd=3000000,
        technologies=["VR", "3D engines", "Spatial computing", "Social"],
        gambling_vertical="casino"),
]


class InnovationPortfolioManager:
    """Manage and prioritize innovation initiatives."""

    def __init__(self):
        self.initiatives = {i.id: i for i in SAMPLE_PORTFOLIO}
        self.weights = SCORING_WEIGHTS

    def score_initiative(self, init: InnovationInitiative) -> dict:
        scores = {
            "revenue_impact": init.revenue_impact,
            "cost_reduction": init.cost_reduction,
            "player_experience": init.player_experience,
            "competitive_advantage": init.competitive_advantage,
            "technical_feasibility": init.technical_feasibility,
            "regulatory_risk": init.regulatory_risk,
        }
        weighted = sum(scores[k] * self.weights[k] for k in scores)

        # Time penalty (faster = better)
        time_factor = max(0.5, 1 - (init.time_to_value_months - 6) / 36)
        adjusted = weighted * time_factor

        # ROI estimate
        annual_impact_est = (init.revenue_impact + init.cost_reduction) * 50000
        roi = (annual_impact_est - init.investment_usd) / max(init.investment_usd, 1)

        return {
            "id": init.id, "name": init.name,
            "raw_score": round(weighted, 2),
            "time_adjusted_score": round(adjusted, 2),
            "dimension_scores": scores,
            "time_to_value_months": init.time_to_value_months,
            "investment_usd": init.investment_usd,
            "estimated_roi": round(roi, 2),
            "horizon": init.horizon.value,
            "status": init.status.value,
        }

    def prioritize(self) -> dict:
        scored = [self.score_initiative(i) for i in self.initiatives.values()]
        scored.sort(key=lambda x: x["time_adjusted_score"], reverse=True)

        # Portfolio balance check
        by_horizon = {"h1": [], "h2": [], "h3": []}
        for s in scored:
            by_horizon[s["horizon"]].append(s["name"])

        total_investment = sum(s["investment_usd"] for s in scored)

        return {
            "prioritization_date": datetime.now(timezone.utc).isoformat(),
            "total_initiatives": len(scored),
            "total_investment_usd": total_investment,
            "ranking": scored,
            "portfolio_balance": {h: len(v) for h, v in by_horizon.items()},
            "recommended_balance": {"h1": "70%", "h2": "20%", "h3": "10%"},
            "top_3_priority": [s["name"] for s in scored[:3]],
            "park_candidates": [s["name"] for s in scored if s["time_adjusted_score"] < 4],
        }

    def get_portfolio_view(self) -> dict:
        return {
            "total": len(self.initiatives),
            "by_status": {s.value: sum(1 for i in self.initiatives.values() if i.status == s)
                          for s in InnovationStatus},
            "by_horizon": {h.value: sum(1 for i in self.initiatives.values() if i.horizon == h)
                           for h in InnovationHorizon},
            "initiatives": [{
                "id": i.id, "name": i.name, "horizon": i.horizon.value,
                "status": i.status.value, "investment": i.investment_usd,
                "technologies": i.technologies,
            } for i in self.initiatives.values()],
        }


def main():
    parser = argparse.ArgumentParser(description="iGaming Innovation Portfolio Manager")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--portfolio", action="store_true")
    parser.add_argument("--prioritize", action="store_true")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    mgr = InnovationPortfolioManager()

    if args.prioritize or args.demo:
        result = mgr.prioritize()
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"\n{'='*70}")
            print(f"  Innovation Portfolio Prioritization")
            print(f"  Total: {result['total_initiatives']} initiatives, ${result['total_investment_usd']:,.0f}")
            print(f"{'='*70}\n")
            print(f"  {'Rank':<5} {'Initiative':<40} {'Score':>6} {'Horizon':<5} {'Investment':>12} {'ROI':>6}")
            print(f"  {'-'*80}")
            for i, s in enumerate(result["ranking"], 1):
                print(f"  {i:<5} {s['name'][:39]:<40} {s['time_adjusted_score']:>5.1f} "
                      f"{s['horizon']:<5} ${s['investment_usd']:>10,} {s['estimated_roi']:>5.1f}x")
            print(f"\n  Top 3: {', '.join(result['top_3_priority'])}")
    elif args.portfolio:
        print(json.dumps(mgr.get_portfolio_view(), indent=2))
    else:
        print("Usage: python innovation_portfolio.py --demo")


if __name__ == "__main__":
    main()
