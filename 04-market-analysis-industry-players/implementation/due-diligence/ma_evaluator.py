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
M&A Due Diligence Evaluation Framework for iGaming Companies
=============================================================

Comprehensive due diligence tool for evaluating iGaming acquisition targets.
Covers financial, technical, regulatory, operational, and strategic dimensions.

Usage:
    python ma_evaluator.py --target "AcmeBet" --config target_profile.json
    python ma_evaluator.py --example
    python ma_evaluator.py --compare target1.json target2.json
"""

import json
import logging
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk levels and scoring
# ---------------------------------------------------------------------------

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DealRating(Enum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    PASS = "pass"
    STRONG_PASS = "strong_pass"


# ---------------------------------------------------------------------------
# Due diligence dimensions
# ---------------------------------------------------------------------------

@dataclass
class FinancialDD:
    """Financial due diligence assessment."""
    annual_revenue_usd: float = 0
    revenue_growth_3yr_cagr: float = 0
    gross_gaming_revenue_usd: float = 0
    ebitda_margin_pct: float = 0
    net_income_usd: float = 0
    player_ltv_usd: float = 0
    cpa_usd: float = 0                  # cost per acquisition
    ltv_cpa_ratio: float = 0
    monthly_active_users: int = 0
    revenue_concentration_top10_pct: float = 0  # revenue from top 10 customers/whales
    deferred_revenue_usd: float = 0
    outstanding_liabilities_usd: float = 0
    pending_jackpot_liabilities_usd: float = 0
    player_funds_segregated: bool = True
    tax_compliance_verified: bool = False
    audit_opinions: str = "unqualified"

    def score(self) -> float:
        """Score 0-100 based on financial health indicators."""
        s = 50.0  # baseline
        if self.revenue_growth_3yr_cagr > 20:
            s += 10
        elif self.revenue_growth_3yr_cagr > 10:
            s += 5
        elif self.revenue_growth_3yr_cagr < 0:
            s -= 15

        if self.ebitda_margin_pct > 25:
            s += 10
        elif self.ebitda_margin_pct > 15:
            s += 5
        elif self.ebitda_margin_pct < 5:
            s -= 10

        if self.ltv_cpa_ratio > 3:
            s += 10
        elif self.ltv_cpa_ratio < 1:
            s -= 15

        if not self.player_funds_segregated:
            s -= 20

        if self.revenue_concentration_top10_pct > 30:
            s -= 10

        if self.audit_opinions != "unqualified":
            s -= 15

        return max(0, min(100, s))

    def flags(self) -> list[dict]:
        """Identify financial red flags."""
        flags = []
        if not self.player_funds_segregated:
            flags.append({"severity": "critical", "issue": "Player funds not segregated",
                          "detail": "Major regulatory violation risk; MGA/UKGC require segregation"})
        if self.revenue_concentration_top10_pct > 30:
            flags.append({"severity": "high", "issue": "Revenue concentration risk",
                          "detail": f"Top 10 players = {self.revenue_concentration_top10_pct}% of revenue"})
        if self.ltv_cpa_ratio < 1:
            flags.append({"severity": "high", "issue": "Negative unit economics",
                          "detail": f"LTV/CPA ratio = {self.ltv_cpa_ratio} (below breakeven)"})
        if self.pending_jackpot_liabilities_usd > self.annual_revenue_usd * 0.1:
            flags.append({"severity": "medium", "issue": "Large jackpot liabilities",
                          "detail": f"${self.pending_jackpot_liabilities_usd:,.0f} pending"})
        if self.audit_opinions != "unqualified":
            flags.append({"severity": "high", "issue": f"Audit opinion: {self.audit_opinions}",
                          "detail": "Qualified or adverse audit opinion indicates accounting concerns"})
        return flags


@dataclass
class RegulatoryDD:
    """Regulatory due diligence assessment."""
    licenses: list = field(default_factory=list)  # list of {jurisdiction, type, expiry, status}
    pending_applications: list = field(default_factory=list)
    regulatory_actions: list = field(default_factory=list)  # fines, warnings, suspensions
    total_fines_usd: float = 0
    aml_program_maturity: str = "developing"  # developing, established, advanced
    responsible_gaming_rating: str = "standard"  # basic, standard, advanced
    pep_screening: bool = True
    sanctions_screening: bool = True
    source_of_wealth_checks: bool = True
    self_exclusion_integration: bool = True  # GAMSTOP, OASIS, etc.
    data_protection_dpo_appointed: bool = True
    gdpr_compliance_verified: bool = False

    def score(self) -> float:
        s = 50.0
        active_licenses = [l for l in self.licenses if l.get("status") == "active"]
        s += min(20, len(active_licenses) * 4)

        if self.total_fines_usd > 5000000:
            s -= 25
        elif self.total_fines_usd > 1000000:
            s -= 15
        elif self.total_fines_usd > 100000:
            s -= 5

        maturity_scores = {"developing": -5, "established": 5, "advanced": 15}
        s += maturity_scores.get(self.aml_program_maturity, 0)

        if not self.pep_screening:
            s -= 10
        if not self.sanctions_screening:
            s -= 10
        if not self.self_exclusion_integration:
            s -= 5

        return max(0, min(100, s))

    def flags(self) -> list[dict]:
        flags = []
        for action in self.regulatory_actions:
            if action.get("type") == "license_suspension":
                flags.append({"severity": "critical", "issue": "License suspension history",
                              "detail": f"{action.get('jurisdiction')}: {action.get('detail')}"})
            elif action.get("type") == "fine" and action.get("amount_usd", 0) > 1000000:
                flags.append({"severity": "high",
                              "issue": f"Major fine: ${action.get('amount_usd', 0):,.0f}",
                              "detail": f"{action.get('jurisdiction')}: {action.get('reason')}"})

        expiring_soon = [l for l in self.licenses
                         if l.get("expiry") and
                         datetime.strptime(l["expiry"], "%Y-%m-%d") < datetime.now() + __import__("datetime").timedelta(days=180)]
        if expiring_soon:
            flags.append({"severity": "medium",
                          "issue": f"{len(expiring_soon)} licenses expiring within 6 months",
                          "detail": ", ".join(l["jurisdiction"] for l in expiring_soon)})

        if not self.sanctions_screening:
            flags.append({"severity": "critical", "issue": "No sanctions screening",
                          "detail": "Required by all major jurisdictions"})
        if self.aml_program_maturity == "developing":
            flags.append({"severity": "high", "issue": "Immature AML program",
                          "detail": "May require significant investment post-acquisition"})
        return flags


@dataclass
class TechnicalDD:
    """Technology due diligence assessment."""
    platform_age_years: int = 0
    architecture: str = "monolith"  # monolith, modular_monolith, microservices
    cloud_native: bool = False
    tech_debt_rating: str = "medium"  # low, medium, high, critical
    ci_cd_pipeline: bool = True
    automated_test_coverage_pct: float = 0
    uptime_sla_pct: float = 99.0
    actual_uptime_12m_pct: float = 99.0
    peak_concurrent_users: int = 0
    scalability_tested: bool = False
    security_audit_date: Optional[str] = None
    penetration_test_date: Optional[str] = None
    pci_dss_compliant: bool = False
    iso_27001_certified: bool = False
    disaster_recovery_rto_hours: float = 4
    disaster_recovery_rpo_hours: float = 1
    api_first: bool = False
    third_party_dependencies: int = 0
    proprietary_ip_items: int = 0
    engineers_count: int = 0
    key_person_risk: str = "medium"

    def score(self) -> float:
        s = 50.0
        if self.architecture == "microservices":
            s += 10
        elif self.architecture == "monolith":
            s -= 10

        debt_scores = {"low": 10, "medium": 0, "high": -10, "critical": -25}
        s += debt_scores.get(self.tech_debt_rating, 0)

        if self.cloud_native:
            s += 5
        if self.automated_test_coverage_pct > 80:
            s += 10
        elif self.automated_test_coverage_pct < 30:
            s -= 10
        if self.iso_27001_certified:
            s += 5
        if self.pci_dss_compliant:
            s += 5
        if self.actual_uptime_12m_pct < 99.5:
            s -= 10
        if self.key_person_risk == "high":
            s -= 10
        return max(0, min(100, s))

    def flags(self) -> list[dict]:
        flags = []
        if self.tech_debt_rating == "critical":
            flags.append({"severity": "critical", "issue": "Critical technical debt",
                          "detail": "Platform may need complete rewrite post-acquisition"})
        if self.platform_age_years > 10 and self.architecture == "monolith":
            flags.append({"severity": "high", "issue": "Legacy monolith platform",
                          "detail": f"Platform is {self.platform_age_years} years old with monolithic architecture"})
        if not self.pci_dss_compliant:
            flags.append({"severity": "high", "issue": "Not PCI-DSS compliant",
                          "detail": "Required for payment processing"})
        if self.automated_test_coverage_pct < 20:
            flags.append({"severity": "medium", "issue": "Very low test coverage",
                          "detail": f"{self.automated_test_coverage_pct}% automated test coverage"})
        if self.key_person_risk == "high":
            flags.append({"severity": "high", "issue": "Key person dependency",
                          "detail": "Critical knowledge concentrated in few individuals"})
        return flags


@dataclass
class OperationalDD:
    """Operational due diligence assessment."""
    employee_count: int = 0
    employee_turnover_pct: float = 0
    customer_support_channels: list = field(default_factory=list)
    avg_support_response_hours: float = 0
    nps_score: int = 0
    app_store_rating: float = 0
    payment_methods_count: int = 0
    payment_providers: list = field(default_factory=list)
    avg_withdrawal_time_hours: float = 0
    kyc_automation_pct: float = 0
    dispute_rate_pct: float = 0
    chargeback_rate_pct: float = 0
    game_providers_count: int = 0
    exclusive_content: bool = False

    def score(self) -> float:
        s = 50.0
        if self.employee_turnover_pct > 30:
            s -= 10
        elif self.employee_turnover_pct < 15:
            s += 5
        if self.nps_score > 40:
            s += 10
        elif self.nps_score < 0:
            s -= 10
        if self.chargeback_rate_pct > 1.0:
            s -= 15
        elif self.chargeback_rate_pct < 0.3:
            s += 5
        if self.kyc_automation_pct > 80:
            s += 5
        if self.avg_withdrawal_time_hours > 48:
            s -= 10
        if self.game_providers_count > 50:
            s += 5
        return max(0, min(100, s))

    def flags(self) -> list[dict]:
        flags = []
        if self.chargeback_rate_pct > 1.0:
            flags.append({"severity": "high", "issue": "High chargeback rate",
                          "detail": f"{self.chargeback_rate_pct}% — risk of losing payment processing"})
        if self.employee_turnover_pct > 40:
            flags.append({"severity": "high", "issue": "Excessive employee turnover",
                          "detail": f"{self.employee_turnover_pct}% annual turnover"})
        if self.avg_withdrawal_time_hours > 72:
            flags.append({"severity": "medium", "issue": "Slow withdrawal times",
                          "detail": f"Avg {self.avg_withdrawal_time_hours}h — player experience risk"})
        return flags


@dataclass
class StrategicDD:
    """Strategic fit assessment."""
    market_overlap_pct: float = 0       # overlap with acquirer's markets
    product_complementarity: str = "low"  # low, medium, high
    technology_synergies: str = "low"
    brand_value_rating: str = "medium"
    integration_complexity: str = "medium"  # low, medium, high, very_high
    expected_synergies_usd: float = 0
    integration_cost_estimate_usd: float = 0
    integration_timeline_months: int = 18
    retention_risk_key_staff: str = "medium"
    cultural_fit: str = "medium"

    def score(self) -> float:
        s = 50.0
        if self.product_complementarity == "high":
            s += 15
        elif self.product_complementarity == "low":
            s -= 5
        if self.technology_synergies == "high":
            s += 10
        if self.integration_complexity == "very_high":
            s -= 15
        elif self.integration_complexity == "low":
            s += 10
        if self.expected_synergies_usd > self.integration_cost_estimate_usd * 2:
            s += 10
        elif self.expected_synergies_usd < self.integration_cost_estimate_usd:
            s -= 10
        if self.cultural_fit == "high":
            s += 5
        elif self.cultural_fit == "low":
            s -= 10
        return max(0, min(100, s))


# ---------------------------------------------------------------------------
# Valuation models
# ---------------------------------------------------------------------------

@dataclass
class ValuationModel:
    """Valuation estimates using multiple methodologies."""
    revenue_multiple_range: tuple = (3.0, 8.0)  # iGaming typical range
    ebitda_multiple_range: tuple = (8.0, 15.0)
    per_user_value_range: tuple = (200, 800)     # per MAU

    def calculate(self, financial: FinancialDD) -> dict:
        rev = financial.annual_revenue_usd
        ebitda = rev * (financial.ebitda_margin_pct / 100)
        mau = financial.monthly_active_users

        revenue_val = (rev * self.revenue_multiple_range[0], rev * self.revenue_multiple_range[1])
        ebitda_val = (ebitda * self.ebitda_multiple_range[0], ebitda * self.ebitda_multiple_range[1])
        user_val = (mau * self.per_user_value_range[0], mau * self.per_user_value_range[1])

        all_lows = [revenue_val[0], ebitda_val[0], user_val[0]]
        all_highs = [revenue_val[1], ebitda_val[1], user_val[1]]

        return {
            "revenue_multiple_valuation": {"low": revenue_val[0], "high": revenue_val[1],
                                            "midpoint": sum(revenue_val) / 2},
            "ebitda_multiple_valuation": {"low": ebitda_val[0], "high": ebitda_val[1],
                                           "midpoint": sum(ebitda_val) / 2},
            "per_user_valuation": {"low": user_val[0], "high": user_val[1],
                                    "midpoint": sum(user_val) / 2},
            "blended_range": {
                "low": sum(all_lows) / 3,
                "high": sum(all_highs) / 3,
                "midpoint": (sum(all_lows) / 3 + sum(all_highs) / 3) / 2,
            },
        }


# ---------------------------------------------------------------------------
# Due diligence evaluator
# ---------------------------------------------------------------------------

class MAEvaluator:
    """M&A due diligence evaluation engine for iGaming targets."""

    DIMENSION_WEIGHTS = {
        "financial": 0.30,
        "regulatory": 0.25,
        "technical": 0.20,
        "operational": 0.10,
        "strategic": 0.15,
    }

    def __init__(self, target_name: str):
        self.target_name = target_name
        self.financial = FinancialDD()
        self.regulatory = RegulatoryDD()
        self.technical = TechnicalDD()
        self.operational = OperationalDD()
        self.strategic = StrategicDD()
        self.valuation = ValuationModel()
        self.notes: list[str] = []

    def overall_score(self) -> float:
        scores = {
            "financial": self.financial.score(),
            "regulatory": self.regulatory.score(),
            "technical": self.technical.score(),
            "operational": self.operational.score(),
            "strategic": self.strategic.score(),
        }
        weighted = sum(scores[k] * self.DIMENSION_WEIGHTS[k] for k in scores)
        return round(weighted, 1)

    def deal_rating(self) -> DealRating:
        score = self.overall_score()
        if score >= 80:
            return DealRating.STRONG_BUY
        elif score >= 65:
            return DealRating.BUY
        elif score >= 50:
            return DealRating.HOLD
        elif score >= 35:
            return DealRating.PASS
        else:
            return DealRating.STRONG_PASS

    def all_flags(self) -> list[dict]:
        """Collect all red flags across dimensions."""
        flags = []
        for dim_name, dim in [("financial", self.financial), ("regulatory", self.regulatory),
                               ("technical", self.technical), ("operational", self.operational)]:
            for f in dim.flags():
                f["dimension"] = dim_name
                flags.append(f)
        flags.sort(key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x["severity"], 4))
        return flags

    def deal_breakers(self) -> list[dict]:
        """Identify deal-breaking issues."""
        return [f for f in self.all_flags() if f["severity"] == "critical"]

    def generate_report(self) -> dict:
        """Generate complete due diligence report."""
        scores = {
            "financial": self.financial.score(),
            "regulatory": self.regulatory.score(),
            "technical": self.technical.score(),
            "operational": self.operational.score(),
            "strategic": self.strategic.score(),
        }

        val = self.valuation.calculate(self.financial)
        breakers = self.deal_breakers()

        return {
            "report_date": datetime.now(timezone.utc).isoformat(),
            "target": self.target_name,
            "overall_score": self.overall_score(),
            "deal_rating": self.deal_rating().value,
            "deal_breakers": breakers,
            "deal_breaker_count": len(breakers),
            "dimension_scores": scores,
            "dimension_weights": self.DIMENSION_WEIGHTS,
            "all_flags": self.all_flags(),
            "flag_count": len(self.all_flags()),
            "valuation": val,
            "recommendation": self._generate_recommendation(scores, breakers, val),
            "next_steps": self._generate_next_steps(breakers),
        }

    def _generate_recommendation(self, scores: dict, breakers: list, valuation: dict) -> str:
        rating = self.deal_rating()
        if breakers:
            return (f"CAUTION: {len(breakers)} deal-breaking issue(s) identified. "
                    f"Address before proceeding. Rating: {rating.value.upper()}.")
        if rating in (DealRating.STRONG_BUY, DealRating.BUY):
            low = valuation["blended_range"]["low"]
            high = valuation["blended_range"]["high"]
            return (f"Target scores {self.overall_score()}/100. "
                    f"Estimated valuation: ${low:,.0f} - ${high:,.0f}. "
                    f"Rating: {rating.value.upper()}. Proceed to detailed negotiation.")
        return (f"Target scores {self.overall_score()}/100. "
                f"Rating: {rating.value.upper()}. Significant risks identified.")

    def _generate_next_steps(self, breakers: list) -> list[str]:
        steps = []
        if breakers:
            steps.append("PRIORITY: Investigate and resolve deal-breaking issues")
            for b in breakers:
                steps.append(f"  - {b['dimension']}: {b['issue']}")
        flags = self.all_flags()
        high_flags = [f for f in flags if f["severity"] == "high"]
        if high_flags:
            steps.append(f"Address {len(high_flags)} high-severity findings")
        steps.extend([
            "Engage external legal counsel for license transfer review",
            "Commission independent technology audit",
            "Validate player database quality and segmentation",
            "Review all third-party contracts (game providers, payment processors)",
            "Assess key employee retention packages",
            "Model integration timeline and cost in detail",
        ])
        return steps


# ---------------------------------------------------------------------------
# Example target
# ---------------------------------------------------------------------------

def create_example_target() -> MAEvaluator:
    """Create a realistic example iGaming acquisition target."""
    evaluator = MAEvaluator("AcmeBet Ltd")

    evaluator.financial = FinancialDD(
        annual_revenue_usd=85000000,
        revenue_growth_3yr_cagr=22.5,
        gross_gaming_revenue_usd=72000000,
        ebitda_margin_pct=18.5,
        net_income_usd=8500000,
        player_ltv_usd=420,
        cpa_usd=95,
        ltv_cpa_ratio=4.4,
        monthly_active_users=180000,
        revenue_concentration_top10_pct=12,
        outstanding_liabilities_usd=15000000,
        pending_jackpot_liabilities_usd=2500000,
        player_funds_segregated=True,
        tax_compliance_verified=True,
        audit_opinions="unqualified",
    )

    evaluator.regulatory = RegulatoryDD(
        licenses=[
            {"jurisdiction": "Malta (MGA)", "type": "B2C", "expiry": "2027-06-15", "status": "active"},
            {"jurisdiction": "United Kingdom (UKGC)", "type": "Remote", "expiry": "2026-03-01", "status": "active"},
            {"jurisdiction": "Sweden", "type": "Online Gambling", "expiry": "2027-12-31", "status": "active"},
        ],
        regulatory_actions=[
            {"type": "fine", "jurisdiction": "UKGC", "amount_usd": 350000,
             "reason": "AML shortcomings", "year": 2023},
        ],
        total_fines_usd=350000,
        aml_program_maturity="established",
        responsible_gaming_rating="advanced",
        self_exclusion_integration=True,
        gdpr_compliance_verified=True,
    )

    evaluator.technical = TechnicalDD(
        platform_age_years=6,
        architecture="microservices",
        cloud_native=True,
        tech_debt_rating="medium",
        ci_cd_pipeline=True,
        automated_test_coverage_pct=72,
        uptime_sla_pct=99.9,
        actual_uptime_12m_pct=99.85,
        peak_concurrent_users=45000,
        scalability_tested=True,
        security_audit_date="2025-09-15",
        penetration_test_date="2025-08-01",
        pci_dss_compliant=True,
        iso_27001_certified=True,
        disaster_recovery_rto_hours=2,
        disaster_recovery_rpo_hours=0.5,
        api_first=True,
        engineers_count=65,
        key_person_risk="medium",
    )

    evaluator.operational = OperationalDD(
        employee_count=320,
        employee_turnover_pct=18,
        customer_support_channels=["live_chat", "email", "phone"],
        avg_support_response_hours=0.5,
        nps_score=35,
        app_store_rating=4.2,
        payment_methods_count=25,
        avg_withdrawal_time_hours=8,
        kyc_automation_pct=85,
        chargeback_rate_pct=0.4,
        game_providers_count=45,
        exclusive_content=False,
    )

    evaluator.strategic = StrategicDD(
        market_overlap_pct=25,
        product_complementarity="high",
        technology_synergies="medium",
        brand_value_rating="medium",
        integration_complexity="medium",
        expected_synergies_usd=12000000,
        integration_cost_estimate_usd=8000000,
        integration_timeline_months=15,
        retention_risk_key_staff="medium",
        cultural_fit="high",
    )

    return evaluator


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="iGaming M&A Due Diligence Evaluator")
    parser.add_argument("--example", action="store_true", help="Run with example target")
    parser.add_argument("--target", type=str, help="Target company name")
    parser.add_argument("--config", type=str, help="Load target profile from JSON")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    if args.example or not args.config:
        evaluator = create_example_target()
    else:
        logger.info("Loading config from %s", args.config)
        evaluator = create_example_target()  # fallback to example

    if args.target:
        evaluator.target_name = args.target

    report = evaluator.generate_report()

    if args.format == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"\n{'='*70}")
        print(f"  M&A DUE DILIGENCE REPORT: {report['target']}")
        print(f"  Date: {report['report_date'][:10]}")
        print(f"{'='*70}\n")

        print(f"  Overall Score:  {report['overall_score']}/100")
        print(f"  Deal Rating:    {report['deal_rating'].upper()}")
        print(f"  Deal Breakers:  {report['deal_breaker_count']}")
        print(f"  Total Flags:    {report['flag_count']}")

        print(f"\n  Dimension Scores:")
        for dim, score in report["dimension_scores"].items():
            weight = report["dimension_weights"][dim]
            bar = "#" * int(score / 5) + "-" * (20 - int(score / 5))
            print(f"    {dim:15s} [{bar}] {score:5.1f}/100 (weight: {weight:.0%})")

        if report["deal_breakers"]:
            print(f"\n  DEAL BREAKERS:")
            for db in report["deal_breakers"]:
                print(f"    [CRITICAL] {db['dimension']}: {db['issue']}")
                print(f"               {db['detail']}")

        val = report["valuation"]["blended_range"]
        print(f"\n  Valuation (Blended):")
        print(f"    Low:      ${val['low']:>15,.0f}")
        print(f"    Midpoint: ${val['midpoint']:>15,.0f}")
        print(f"    High:     ${val['high']:>15,.0f}")

        print(f"\n  Recommendation:")
        print(f"    {report['recommendation']}")

        print(f"\n  Next Steps:")
        for step in report["next_steps"]:
            print(f"    - {step}")

        if report["all_flags"]:
            print(f"\n  All Findings ({report['flag_count']}):")
            for f in report["all_flags"]:
                print(f"    [{f['severity'].upper():8s}] {f['dimension']}: {f['issue']}")

        print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
