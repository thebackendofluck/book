#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 03, Global Market Analysis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Market Entry Risk Scoring Matrix for iGaming
==============================================

Evaluates market entry risk across six dimensions:
  1. Regulatory stability   - How stable and predictable is the regulatory framework
  2. Tax burden             - GGR tax + corporate tax + hidden costs
  3. Competition intensity  - Market concentration, operator count, barriers
  4. Infrastructure quality - Internet, payments, mobile, data centers
  5. Payment availability   - Payment method diversity, processing success rates
  6. Cultural acceptance    - Public attitude toward gambling, stigma, tradition

Usage:
    python market_risk_scorer.py --jurisdiction UK
    python market_risk_scorer.py --all --sort risk_score
    python market_risk_scorer.py --compare UK,BR,NG,JP
    python market_risk_scorer.py --heatmap
"""

import argparse
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk dimension scoring (1-10 scale, 10 = highest risk)
# ---------------------------------------------------------------------------

@dataclass
class RiskProfile:
    """Complete risk profile for a jurisdiction."""
    code: str
    name: str
    region: str

    # Dimension 1: Regulatory Stability (1 = very stable, 10 = very unstable)
    reg_framework_age_years: int  # how long online gambling has been regulated
    reg_clarity: int  # 1-10, how clear the rules are
    reg_change_frequency: int  # 1-10, how often rules change
    reg_enforcement_consistency: int  # 1-10, consistency of enforcement
    reg_political_risk: int  # 1-10, likelihood of major political shifts
    reg_notes: str = ""

    # Dimension 2: Tax Burden
    ggr_tax_pct: float = 0
    corporate_tax_pct: float = 0
    vat_pct: float = 0
    license_cost_annual_usd: float = 0
    hidden_costs_rating: int = 5  # 1-10, compliance costs beyond stated taxes
    tax_notes: str = ""

    # Dimension 3: Competition Intensity
    num_operators: int = 0
    top_3_share_pct: float = 0
    barriers_to_entry: int = 5  # 1-10 (10 = very high barriers)
    customer_acquisition_cost: int = 5  # 1-10 (10 = very expensive)
    brand_loyalty_strength: int = 5  # 1-10 (10 = very loyal to incumbents)
    competition_notes: str = ""

    # Dimension 4: Infrastructure Quality
    internet_penetration_pct: float = 0
    avg_speed_mbps: float = 0
    mobile_penetration_pct: float = 0
    data_center_availability: int = 5  # 1-10 (10 = excellent)
    power_reliability: int = 5  # 1-10 (10 = excellent)
    infra_notes: str = ""

    # Dimension 5: Payment Availability
    payment_method_diversity: int = 5  # 1-10 (10 = many options)
    processing_success_rate: int = 5  # 1-10 (10 = very reliable)
    banking_restrictions: int = 5  # 1-10 (10 = no restrictions)
    crypto_acceptance: int = 1  # 1-10
    unbanked_population_pct: float = 0
    payment_notes: str = ""

    # Dimension 6: Cultural Acceptance
    public_attitude: int = 5  # 1-10 (10 = very positive)
    gambling_tradition: int = 5  # 1-10 (10 = strong tradition)
    religious_opposition: int = 5  # 1-10 (10 = no opposition)
    media_sentiment: int = 5  # 1-10 (10 = very favorable)
    responsible_gambling_maturity: int = 5  # 1-10 (10 = very mature)
    cultural_notes: str = ""


# ---------------------------------------------------------------------------
# Jurisdiction risk database
# ---------------------------------------------------------------------------

RISK_PROFILES = [
    RiskProfile(
        code="UK", name="United Kingdom", region="europe",
        reg_framework_age_years=20, reg_clarity=9, reg_change_frequency=4,
        reg_enforcement_consistency=9, reg_political_risk=3,
        reg_notes="Gambling Act 2005 reformed 2023 (White Paper). UKGC is "
                  "gold standard regulator. Frequent updates but predictable.",
        ggr_tax_pct=21, corporate_tax_pct=25, vat_pct=20,
        license_cost_annual_usd=120000, hidden_costs_rating=7,
        tax_notes="RGD 21% on GGR. Affordability checks add compliance cost. "
                  "Source of funds checks expensive to implement.",
        num_operators=280, top_3_share_pct=42, barriers_to_entry=8,
        customer_acquisition_cost=9, brand_loyalty_strength=7,
        competition_notes="Hyper-competitive mature market. High CAC (~$200-400/FTD). "
                          "Affiliate restrictions tightening.",
        internet_penetration_pct=97, avg_speed_mbps=75.5,
        mobile_penetration_pct=92, data_center_availability=10,
        power_reliability=10,
        payment_method_diversity=9, processing_success_rate=9,
        banking_restrictions=8, crypto_acceptance=2, unbanked_population_pct=3,
        public_attitude=6, gambling_tradition=8, religious_opposition=8,
        media_sentiment=4, responsible_gambling_maturity=10,
        cultural_notes="Strong betting culture (horse racing, football). "
                       "Growing responsible gambling concerns in media.",
    ),
    RiskProfile(
        code="DE", name="Germany", region="europe",
        reg_framework_age_years=4, reg_clarity=5, reg_change_frequency=7,
        reg_enforcement_consistency=5, reg_political_risk=4,
        reg_notes="GluNeuRStV (2021). GGL regulator still maturing. "
                  "Slot stake limits and monthly deposit caps controversial.",
        ggr_tax_pct=5.3, corporate_tax_pct=30, vat_pct=19,
        license_cost_annual_usd=50000, hidden_costs_rating=6,
        tax_notes="5.3% turnover tax on sports (effectively much higher than GGR tax). "
                  "Slot 5.3% on stakes. High effective rate.",
        num_operators=52, top_3_share_pct=35, barriers_to_entry=7,
        customer_acquisition_cost=7, brand_loyalty_strength=5,
        competition_notes="Grey-to-regulated transition ongoing. "
                          "Many unlicensed operators still active.",
        internet_penetration_pct=93, avg_speed_mbps=65.2,
        mobile_penetration_pct=88, data_center_availability=9,
        power_reliability=10,
        payment_method_diversity=8, processing_success_rate=8,
        banking_restrictions=7, crypto_acceptance=2, unbanked_population_pct=1,
        public_attitude=5, gambling_tradition=6, religious_opposition=7,
        media_sentiment=4, responsible_gambling_maturity=6,
        cultural_notes="Sports betting popular (Bundesliga). Casino culture exists. "
                       "Regulatory uncertainty dampens sentiment.",
    ),
    RiskProfile(
        code="BR", name="Brazil", region="latin_america",
        reg_framework_age_years=1, reg_clarity=4, reg_change_frequency=8,
        reg_enforcement_consistency=3, reg_political_risk=6,
        reg_notes="Regulated from Jan 2025 under SPA/MF. Framework still evolving. "
                  "Enforcement against unlicensed operators started late 2024.",
        ggr_tax_pct=12, corporate_tax_pct=34, vat_pct=0,
        license_cost_annual_usd=1200000, hidden_costs_rating=8,
        tax_notes="R$30M license fee (5 years). 12% GGR tax. SIGAP reporting system "
                  "adds significant compliance overhead. Local servers required.",
        num_operators=65, top_3_share_pct=55, barriers_to_entry=6,
        customer_acquisition_cost=5, brand_loyalty_strength=4,
        competition_notes="Massive gold rush. Bet365, Betano, Sportiingbet lead. "
                          "Many local operators emerging.",
        internet_penetration_pct=84, avg_speed_mbps=42.5,
        mobile_penetration_pct=78, data_center_availability=7,
        power_reliability=7,
        payment_method_diversity=7, processing_success_rate=7,
        banking_restrictions=6, crypto_acceptance=3, unbanked_population_pct=16,
        payment_notes="PIX dominates (~80% of deposits). Boleto still used. "
                      "Credit card gambling restricted by some banks.",
        public_attitude=7, gambling_tradition=7, religious_opposition=5,
        media_sentiment=5, responsible_gambling_maturity=3,
        cultural_notes="Strong sports betting culture (futebol). Jogo do bicho tradition. "
                       "Evangelical church opposition exists but hasn't blocked regulation.",
    ),
    RiskProfile(
        code="US", name="United States", region="north_america",
        reg_framework_age_years=6, reg_clarity=4, reg_change_frequency=8,
        reg_enforcement_consistency=7, reg_political_risk=5,
        reg_notes="State-by-state regulation since PASPA repeal 2018. 38 states sports, "
                  "7 iGaming. Federal Wire Act uncertainty remains.",
        ggr_tax_pct=20, corporate_tax_pct=21, vat_pct=0,
        license_cost_annual_usd=500000, hidden_costs_rating=9,
        tax_notes="State taxes 10-51% (NY highest). Multi-state compliance extremely "
                  "expensive. Each state has separate requirements.",
        num_operators=120, top_3_share_pct=72, barriers_to_entry=9,
        customer_acquisition_cost=10, brand_loyalty_strength=8,
        competition_notes="FanDuel + DraftKings = ~65% sports. Very expensive market. "
                          "iGaming only in NJ, MI, PA, WV, CT, DE, RI.",
        internet_penetration_pct=95, avg_speed_mbps=95.3,
        mobile_penetration_pct=90, data_center_availability=10,
        power_reliability=9,
        payment_method_diversity=7, processing_success_rate=8,
        banking_restrictions=6, crypto_acceptance=2, unbanked_population_pct=5,
        payment_notes="Play+ prepaid, ACH, debit cards. Credit cards largely blocked. "
                      "PayPal available in some states.",
        public_attitude=6, gambling_tradition=7, religious_opposition=5,
        media_sentiment=6, responsible_gambling_maturity=7,
        cultural_notes="Sports betting rapidly normalizing. Vegas/Atlantic City tradition. "
                       "Some states still have strong anti-gambling lobbies.",
    ),
    RiskProfile(
        code="NG", name="Nigeria", region="africa",
        reg_framework_age_years=8, reg_clarity=3, reg_change_frequency=6,
        reg_enforcement_consistency=2, reg_political_risk=7,
        reg_notes="State-level regulation (Lagos SB 2004). National Lottery Regulatory "
                  "Commission oversees lotteries. Online betting in regulatory grey area.",
        ggr_tax_pct=5, corporate_tax_pct=30, vat_pct=7.5,
        license_cost_annual_usd=50000, hidden_costs_rating=5,
        tax_notes="Low formal tax rate but informal costs (compliance, local partnerships). "
                  "Withholding tax on winnings varies by state.",
        num_operators=80, top_3_share_pct=60, barriers_to_entry=4,
        customer_acquisition_cost=3, brand_loyalty_strength=3,
        competition_notes="Bet9ja, Sportybet, 1xBet dominate. Low barriers but "
                          "distribution (agents) is key competitive advantage.",
        internet_penetration_pct=55, avg_speed_mbps=18.5,
        mobile_penetration_pct=42, data_center_availability=4,
        power_reliability=3,
        infra_notes="USSD betting critical for feature phones. Power outages common. "
                    "Mobile-first market (80%+ via mobile).",
        payment_method_diversity=5, processing_success_rate=5,
        banking_restrictions=4, crypto_acceptance=4, unbanked_population_pct=40,
        payment_notes="Mobile money (OPay, PalmPay), bank transfer, USSD. "
                      "Agent network for cash deposits essential.",
        public_attitude=7, gambling_tradition=6, religious_opposition=4,
        media_sentiment=5, responsible_gambling_maturity=2,
        cultural_notes="Sports betting hugely popular among youth. Pool betting tradition. "
                       "Limited responsible gambling infrastructure.",
    ),
    RiskProfile(
        code="JP", name="Japan", region="asia_pacific",
        reg_framework_age_years=0, reg_clarity=2, reg_change_frequency=3,
        reg_enforcement_consistency=9, reg_political_risk=5,
        reg_notes="Online gambling largely prohibited. Only government-sanctioned "
                  "horse/boat/cycle/moto racing. IR (casino resort) legislation pending.",
        ggr_tax_pct=30, corporate_tax_pct=23.2, vat_pct=10,
        license_cost_annual_usd=0, hidden_costs_rating=3,
        num_operators=8, top_3_share_pct=85, barriers_to_entry=10,
        customer_acquisition_cost=8, brand_loyalty_strength=6,
        competition_notes="Government monopoly on allowed forms. No private online "
                          "gambling licenses available.",
        internet_penetration_pct=93, avg_speed_mbps=120,
        mobile_penetration_pct=85, data_center_availability=10,
        power_reliability=10,
        payment_method_diversity=8, processing_success_rate=9,
        banking_restrictions=9, crypto_acceptance=3, unbanked_population_pct=2,
        payment_notes="Excellent infrastructure but banks block gambling transactions. "
                      "Convenience store payments are workaround.",
        public_attitude=4, gambling_tradition=8, religious_opposition=7,
        media_sentiment=3, responsible_gambling_maturity=4,
        cultural_notes="Strong pachinko tradition (~$150B industry). Horse racing popular. "
                       "Social stigma around gambling despite participation.",
    ),
    RiskProfile(
        code="PH", name="Philippines", region="asia_pacific",
        reg_framework_age_years=15, reg_clarity=6, reg_change_frequency=5,
        reg_enforcement_consistency=5, reg_political_risk=6,
        reg_notes="PAGCOR regulates and operates. POGO crackdown 2024 affected B2B "
                  "operators serving offshore markets. Domestic market more stable.",
        ggr_tax_pct=5, corporate_tax_pct=25, vat_pct=12,
        license_cost_annual_usd=100000, hidden_costs_rating=6,
        num_operators=55, top_3_share_pct=45, barriers_to_entry=5,
        customer_acquisition_cost=4, brand_loyalty_strength=4,
        internet_penetration_pct=68, avg_speed_mbps=32.4,
        mobile_penetration_pct=62, data_center_availability=6,
        power_reliability=6,
        payment_method_diversity=7, processing_success_rate=6,
        banking_restrictions=6, crypto_acceptance=4, unbanked_population_pct=34,
        payment_notes="GCash dominant e-wallet. Maya (PayMaya). OTC cash-in at "
                      "7-Eleven/convenience stores important.",
        public_attitude=7, gambling_tradition=8, religious_opposition=5,
        media_sentiment=5, responsible_gambling_maturity=3,
        cultural_notes="Strong gambling culture. Cockfighting (Sabong) moved online. "
                       "Basketball/boxing betting popular.",
    ),
    RiskProfile(
        code="SE", name="Sweden", region="europe",
        reg_framework_age_years=7, reg_clarity=8, reg_change_frequency=5,
        reg_enforcement_consistency=8, reg_political_risk=3,
        reg_notes="Gambling Act 2018 (re-regulation). Spelinspektionen mature regulator. "
                  "Bonus restrictions (welcome only). Spelpaus self-exclusion mandatory.",
        ggr_tax_pct=18, corporate_tax_pct=20.6, vat_pct=25,
        license_cost_annual_usd=20000, hidden_costs_rating=5,
        num_operators=95, top_3_share_pct=48, barriers_to_entry=7,
        customer_acquisition_cost=8, brand_loyalty_strength=6,
        internet_penetration_pct=98, avg_speed_mbps=115,
        mobile_penetration_pct=95, data_center_availability=9,
        power_reliability=10,
        payment_method_diversity=9, processing_success_rate=9,
        banking_restrictions=8, crypto_acceptance=2, unbanked_population_pct=1,
        payment_notes="Swish (mobile payments), Trustly (Open Banking), "
                      "Zimpler. High instant payment adoption.",
        public_attitude=6, gambling_tradition=7, religious_opposition=8,
        media_sentiment=5, responsible_gambling_maturity=9,
        cultural_notes="Strong online gambling culture. State monopoly (Svenska Spel) "
                       "still significant. Channelization ~80%+.",
    ),
    RiskProfile(
        code="AU", name="Australia", region="oceania",
        reg_framework_age_years=20, reg_clarity=7, reg_change_frequency=5,
        reg_enforcement_consistency=8, reg_political_risk=4,
        reg_notes="Interactive Gambling Act 2001 (amended 2017). Online casino prohibited. "
                  "Sports betting via Northern Territory licenses. ACMA blocks unlicensed.",
        ggr_tax_pct=15, corporate_tax_pct=30, vat_pct=10,
        license_cost_annual_usd=80000, hidden_costs_rating=6,
        num_operators=35, top_3_share_pct=65, barriers_to_entry=7,
        customer_acquisition_cost=8, brand_loyalty_strength=8,
        internet_penetration_pct=96, avg_speed_mbps=62.8,
        mobile_penetration_pct=91, data_center_availability=8,
        power_reliability=9,
        payment_method_diversity=8, processing_success_rate=9,
        banking_restrictions=7, crypto_acceptance=2, unbanked_population_pct=3,
        payment_notes="POLi (bank transfer), Visa/MC, PayPal. "
                      "Credit card gambling banned (2020).",
        public_attitude=6, gambling_tradition=9, religious_opposition=7,
        media_sentiment=4, responsible_gambling_maturity=8,
        cultural_notes="Highest gambling spend per capita globally. Horse racing "
                       "deeply cultural (Melbourne Cup). Pokies controversial.",
    ),
    RiskProfile(
        code="IT", name="Italy", region="europe",
        reg_framework_age_years=18, reg_clarity=7, reg_change_frequency=4,
        reg_enforcement_consistency=7, reg_political_risk=4,
        reg_notes="ADM (Agenzia delle Dogane e dei Monopoli). Regulated since 2006. "
                  "Advertising ban (Dignity Decree 2018). Stable framework.",
        ggr_tax_pct=25, corporate_tax_pct=24, vat_pct=22,
        license_cost_annual_usd=300000, hidden_costs_rating=7,
        num_operators=95, top_3_share_pct=38, barriers_to_entry=7,
        customer_acquisition_cost=7, brand_loyalty_strength=6,
        internet_penetration_pct=88, avg_speed_mbps=55.8,
        mobile_penetration_pct=83, data_center_availability=8,
        power_reliability=9,
        payment_method_diversity=8, processing_success_rate=8,
        banking_restrictions=7, crypto_acceptance=2, unbanked_population_pct=6,
        payment_notes="PostePay prepaid dominant. Visa/MC. PayPal. "
                      "Advertising ban limits payment partner visibility.",
        public_attitude=6, gambling_tradition=8, religious_opposition=5,
        media_sentiment=4, responsible_gambling_maturity=7,
        cultural_notes="Strong lottery tradition (SuperEnalotto). Sports betting "
                       "(calcio) very popular. Advertising ban a major constraint.",
    ),
]


# ---------------------------------------------------------------------------
# Risk scoring engine
# ---------------------------------------------------------------------------

DIMENSION_WEIGHTS = {
    "regulatory_stability": 0.25,
    "tax_burden": 0.15,
    "competition_intensity": 0.20,
    "infrastructure_quality": 0.10,
    "payment_availability": 0.15,
    "cultural_acceptance": 0.15,
}


class MarketRiskScorer:
    """Score market entry risk across six dimensions."""

    def __init__(self, weights: Optional[dict] = None):
        self.weights = weights or DIMENSION_WEIGHTS
        total = sum(self.weights.values())
        self.weights = {k: v / total for k, v in self.weights.items()}

    def score(self, profile: RiskProfile) -> dict:
        """Score a jurisdiction on all risk dimensions."""
        dimensions = {}

        # 1. Regulatory Stability (higher = more risky)
        reg_risk = (
            (10 - min(profile.reg_framework_age_years, 20) / 2)  # newer = riskier
            * 0.15
            + (10 - profile.reg_clarity) * 0.25
            + profile.reg_change_frequency * 0.20
            + (10 - profile.reg_enforcement_consistency) * 0.20
            + profile.reg_political_risk * 0.20
        )
        dimensions["regulatory_stability"] = {
            "score": round(min(10, max(1, reg_risk)), 1),
            "label": self._label(reg_risk),
            "factors": {
                "framework_age_years": profile.reg_framework_age_years,
                "clarity": profile.reg_clarity,
                "change_frequency": profile.reg_change_frequency,
                "enforcement_consistency": profile.reg_enforcement_consistency,
                "political_risk": profile.reg_political_risk,
            },
            "notes": profile.reg_notes,
        }

        # 2. Tax Burden
        effective_rate = profile.ggr_tax_pct + profile.corporate_tax_pct * 0.3
        tax_risk = min(10, effective_rate / 5) * 0.4 + profile.hidden_costs_rating * 0.3
        annual_cost_factor = min(10, profile.license_cost_annual_usd / 100000)
        tax_risk += annual_cost_factor * 0.3
        dimensions["tax_burden"] = {
            "score": round(min(10, max(1, tax_risk)), 1),
            "label": self._label(tax_risk),
            "factors": {
                "ggr_tax_pct": profile.ggr_tax_pct,
                "corporate_tax_pct": profile.corporate_tax_pct,
                "effective_combined_pct": round(effective_rate, 1),
                "license_annual_usd": profile.license_cost_annual_usd,
                "hidden_costs": profile.hidden_costs_rating,
            },
            "notes": profile.tax_notes,
        }

        # 3. Competition Intensity
        comp_risk = (
            min(10, profile.num_operators / 30) * 0.15
            + (profile.top_3_share_pct / 10) * 0.20
            + profile.barriers_to_entry * 0.25
            + profile.customer_acquisition_cost * 0.25
            + profile.brand_loyalty_strength * 0.15
        )
        dimensions["competition_intensity"] = {
            "score": round(min(10, max(1, comp_risk)), 1),
            "label": self._label(comp_risk),
            "factors": {
                "num_operators": profile.num_operators,
                "top_3_share_pct": profile.top_3_share_pct,
                "barriers_to_entry": profile.barriers_to_entry,
                "cac_difficulty": profile.customer_acquisition_cost,
                "brand_loyalty": profile.brand_loyalty_strength,
            },
            "notes": profile.competition_notes,
        }

        # 4. Infrastructure Quality (inverted: poor infra = higher risk)
        infra_quality = (
            (profile.internet_penetration_pct / 10) * 0.25
            + min(10, profile.avg_speed_mbps / 12) * 0.20
            + (profile.mobile_penetration_pct / 10) * 0.20
            + profile.data_center_availability * 0.20
            + profile.power_reliability * 0.15
        )
        infra_risk = 10 - infra_quality  # invert: good infra = low risk
        dimensions["infrastructure_quality"] = {
            "score": round(min(10, max(1, infra_risk)), 1),
            "label": self._label(infra_risk),
            "factors": {
                "internet_pct": profile.internet_penetration_pct,
                "speed_mbps": profile.avg_speed_mbps,
                "mobile_pct": profile.mobile_penetration_pct,
                "data_centers": profile.data_center_availability,
                "power": profile.power_reliability,
            },
            "notes": profile.infra_notes,
        }

        # 5. Payment Availability (inverted: poor payments = higher risk)
        payment_quality = (
            profile.payment_method_diversity * 0.25
            + profile.processing_success_rate * 0.30
            + profile.banking_restrictions * 0.25
            + (10 - min(10, profile.unbanked_population_pct / 5)) * 0.20
        )
        payment_risk = 10 - payment_quality
        dimensions["payment_availability"] = {
            "score": round(min(10, max(1, payment_risk)), 1),
            "label": self._label(payment_risk),
            "factors": {
                "method_diversity": profile.payment_method_diversity,
                "processing_reliability": profile.processing_success_rate,
                "banking_openness": profile.banking_restrictions,
                "crypto_acceptance": profile.crypto_acceptance,
                "unbanked_pct": profile.unbanked_population_pct,
            },
            "notes": profile.payment_notes,
        }

        # 6. Cultural Acceptance (inverted: low acceptance = higher risk)
        cultural_quality = (
            profile.public_attitude * 0.25
            + profile.gambling_tradition * 0.20
            + profile.religious_opposition * 0.20  # high = no opposition = good
            + profile.media_sentiment * 0.20
            + profile.responsible_gambling_maturity * 0.15
        )
        cultural_risk = 10 - cultural_quality
        dimensions["cultural_acceptance"] = {
            "score": round(min(10, max(1, cultural_risk)), 1),
            "label": self._label(cultural_risk),
            "factors": {
                "public_attitude": profile.public_attitude,
                "gambling_tradition": profile.gambling_tradition,
                "religious_opposition_absent": profile.religious_opposition,
                "media_sentiment": profile.media_sentiment,
                "rg_maturity": profile.responsible_gambling_maturity,
            },
            "notes": profile.cultural_notes,
        }

        # Overall risk score
        overall = sum(
            dimensions[k]["score"] * self.weights[k] for k in dimensions
        )

        # Risk-adjusted opportunity rating
        opportunity_penalty = overall / 10  # 0-1
        opportunity_rating = round(10 * (1 - opportunity_penalty * 0.6), 1)

        return {
            "jurisdiction": profile.name,
            "code": profile.code,
            "region": profile.region,
            "overall_risk_score": round(overall, 1),
            "risk_level": self._overall_label(overall),
            "opportunity_rating": opportunity_rating,
            "dimensions": dimensions,
            "top_risks": self._top_risks(dimensions),
            "top_strengths": self._top_strengths(dimensions),
            "recommendation": self._recommendation(overall, dimensions),
        }

    def _label(self, score: float) -> str:
        if score <= 2.5:
            return "LOW"
        elif score <= 5:
            return "MODERATE"
        elif score <= 7.5:
            return "HIGH"
        else:
            return "VERY HIGH"

    def _overall_label(self, score: float) -> str:
        if score <= 3:
            return "LOW RISK"
        elif score <= 5:
            return "MODERATE RISK"
        elif score <= 7:
            return "HIGH RISK"
        else:
            return "VERY HIGH RISK"

    def _top_risks(self, dimensions: dict) -> list[dict]:
        sorted_dims = sorted(dimensions.items(),
                              key=lambda x: x[1]["score"], reverse=True)
        return [
            {"dimension": k.replace("_", " ").title(),
             "score": v["score"], "label": v["label"]}
            for k, v in sorted_dims[:3]
            if v["score"] >= 5
        ]

    def _top_strengths(self, dimensions: dict) -> list[dict]:
        sorted_dims = sorted(dimensions.items(),
                              key=lambda x: x[1]["score"])
        return [
            {"dimension": k.replace("_", " ").title(),
             "score": v["score"], "label": v["label"]}
            for k, v in sorted_dims[:3]
            if v["score"] <= 5
        ]

    def _recommendation(self, overall: float, dimensions: dict) -> str:
        if overall <= 3:
            return ("FAVORABLE - Low overall risk. Standard market entry "
                    "approach appropriate. Focus on competitive differentiation.")
        elif overall <= 5:
            return ("PROCEED WITH CAUTION - Moderate risk. Conduct detailed "
                    "due diligence on high-risk dimensions. Consider phased entry.")
        elif overall <= 7:
            return ("HIGH RISK - Significant challenges identified. "
                    "Requires strong local partnerships, substantial capital reserves, "
                    "and robust risk mitigation strategy.")
        else:
            return ("AVOID OR DEFER - Very high risk market. Consider waiting for "
                    "regulatory maturation or fundamental market changes before entry.")

    def rank_all(self, sort_by: str = "risk_score") -> list[dict]:
        """Rank all jurisdictions."""
        results = [self.score(p) for p in RISK_PROFILES]
        if sort_by == "risk_score":
            results.sort(key=lambda x: x["overall_risk_score"])
        elif sort_by == "opportunity":
            results.sort(key=lambda x: x["opportunity_rating"], reverse=True)
        return results

    def compare(self, codes: list[str]) -> dict:
        """Side-by-side comparison."""
        profiles = {p.code: p for p in RISK_PROFILES}
        results = []
        for code in codes:
            p = profiles.get(code.upper())
            if p:
                results.append(self.score(p))
        return {
            "comparison": results,
            "lowest_risk": min(results, key=lambda x: x["overall_risk_score"])["jurisdiction"]
            if results else None,
        }

    def heatmap_data(self) -> list[dict]:
        """Generate data for a risk heatmap visualization."""
        results = []
        for p in RISK_PROFILES:
            scored = self.score(p)
            row = {"jurisdiction": p.name, "code": p.code}
            for dim, data in scored["dimensions"].items():
                row[dim] = data["score"]
            row["overall"] = scored["overall_risk_score"]
            results.append(row)
        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="iGaming Market Entry Risk Scorer")
    parser.add_argument("--jurisdiction", "-j", type=str)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--compare", type=str,
                        help="Comma-separated jurisdiction codes")
    parser.add_argument("--heatmap", action="store_true",
                        help="Output heatmap data")
    parser.add_argument("--sort", choices=["risk_score", "opportunity"],
                        default="risk_score")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    scorer = MarketRiskScorer()

    if args.heatmap:
        data = scorer.heatmap_data()
        print(json.dumps(data, indent=2))
        return

    if args.compare:
        codes = [c.strip() for c in args.compare.split(",")]
        result = scorer.compare(codes)
        print(json.dumps(result, indent=2))
        return

    if args.jurisdiction:
        profiles = {p.code: p for p in RISK_PROFILES}
        p = profiles.get(args.jurisdiction.upper())
        if not p:
            print(f"Unknown: {args.jurisdiction}. "
                  f"Available: {', '.join(profiles.keys())}")
            return
        result = scorer.score(p)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            _print_risk_report(result)
        return

    # Default: all
    results = scorer.rank_all(sort_by=args.sort)
    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        print(f"=== Market Entry Risk Ranking (sorted by {args.sort}) ===\n")
        print(f"{'Rank':<5} {'Jurisdiction':<25} {'Risk':>6} {'Level':<15} "
              f"{'Opportunity':>11} {'Top Risk':<30}")
        print("-" * 100)
        for i, r in enumerate(results, 1):
            top = r["top_risks"][0]["dimension"] if r["top_risks"] else "None"
            print(f"{i:<5} {r['jurisdiction']:<25} {r['overall_risk_score']:>5.1f} "
                  f"{r['risk_level']:<15} {r['opportunity_rating']:>10.1f} "
                  f"{top:<30}")


def _print_risk_report(result: dict):
    """Pretty-print a risk assessment."""
    print(f"=== Risk Assessment: {result['jurisdiction']} ({result['code']}) ===\n")
    print(f"Overall Risk: {result['overall_risk_score']:.1f}/10 ({result['risk_level']})")
    print(f"Opportunity Rating: {result['opportunity_rating']:.1f}/10\n")

    print("--- Dimension Scores (1=low risk, 10=high risk) ---")
    for dim, data in result["dimensions"].items():
        bar = "#" * int(data["score"]) + "." * (10 - int(data["score"]))
        print(f"  {dim.replace('_', ' ').title():<25} [{bar}] "
              f"{data['score']:>4.1f} ({data['label']})")
        if data.get("notes"):
            print(f"    {data['notes'][:100]}")
    print()

    if result["top_risks"]:
        print("--- Top Risks ---")
        for r in result["top_risks"]:
            print(f"  {r['dimension']}: {r['score']:.1f} ({r['label']})")

    if result["top_strengths"]:
        print("\n--- Top Strengths ---")
        for s in result["top_strengths"]:
            print(f"  {s['dimension']}: {s['score']:.1f} ({s['label']})")

    print(f"\n--- Recommendation ---\n  {result['recommendation']}")


if __name__ == "__main__":
    main()
