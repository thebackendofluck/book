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
RegTech Vendor Evaluation Framework for iGaming Platforms
==========================================================

Systematic scoring framework for evaluating RegTech vendors across
AML, KYC, player monitoring, and compliance automation capabilities.
Produces weighted scores, gap analyses, and TCO comparisons.

Covers:
- Vendor capability scoring across 8 regulatory domains
- AML/CFT solution evaluation (transaction monitoring, SAR filing)
- KYC/identity verification vendor comparison
- Player protection and affordability check assessment
- Regulatory reporting automation evaluation
- Total Cost of Ownership (TCO) modeling
- Integration complexity scoring
- Vendor risk assessment

Feasibility Assessment:
- Scoring framework uses weighted averages - no ML required
- Criteria based on real regulatory requirements (UK GC, MGA, AGCO)
- TCO model covers license, integration, and operational costs
- Output is structured data for executive decision-making
- No external dependencies

Dependencies: None
"""

import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

class RegDomain(Enum):
    AML_CFT = "aml_cft"
    KYC_IDENTITY = "kyc_identity"
    PLAYER_PROTECTION = "player_protection"
    AFFORDABILITY = "affordability"
    REGULATORY_REPORTING = "regulatory_reporting"
    DATA_PRIVACY = "data_privacy"
    RESPONSIBLE_GAMBLING = "responsible_gambling"
    FRAUD_DETECTION = "fraud_detection"


class IntegrationMethod(Enum):
    REST_API = "rest_api"
    BATCH_FILE = "batch_file"
    REAL_TIME_STREAMING = "real_time_streaming"
    SDK = "sdk"
    IFRAME = "iframe"
    WEBHOOK = "webhook"


class VendorTier(Enum):
    ENTERPRISE = "enterprise"      # Full-suite, high cost
    MID_MARKET = "mid_market"      # Modular, medium cost
    SPECIALIST = "specialist"      # Single-domain expert
    STARTUP = "startup"            # Innovative, lower maturity


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class VendorProfile:
    """Profile of a RegTech vendor under evaluation."""
    vendor_id: str
    name: str
    tier: VendorTier
    domains: list[RegDomain] = field(default_factory=list)
    jurisdictions_supported: list[str] = field(default_factory=list)
    integration_methods: list[IntegrationMethod] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    years_in_market: int = 0
    gambling_clients: int = 0
    headquarters: str = ""
    data_residency_options: list[str] = field(default_factory=list)
    sla_uptime_pct: float = 99.9
    api_response_time_ms: int = 500
    pricing_model: str = ""  # per-check, monthly, annual
    annual_cost_estimate: float = 0.0
    integration_weeks_estimate: int = 4
    notes: str = ""


@dataclass
class CapabilityScore:
    """Score for a single capability within a domain."""
    capability: str
    weight: float  # 0.0 to 1.0
    score: int  # 1-5 scale
    max_score: int = 5
    evidence: str = ""
    gap: str = ""


@dataclass
class DomainEvaluation:
    """Evaluation of a vendor across a single regulatory domain."""
    domain: RegDomain
    capabilities: list[CapabilityScore] = field(default_factory=list)
    weighted_score: float = 0.0
    max_possible: float = 0.0
    percentage: float = 0.0
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


@dataclass
class TCOAnalysis:
    """Total Cost of Ownership analysis."""
    vendor_id: str
    year_1_cost: float = 0.0
    year_3_cost: float = 0.0
    year_5_cost: float = 0.0
    breakdown: dict = field(default_factory=dict)
    cost_per_check: float = 0.0
    cost_per_player: float = 0.0
    hidden_costs: list[str] = field(default_factory=list)


@dataclass
class VendorEvaluation:
    """Complete evaluation result for a vendor."""
    vendor: VendorProfile
    domain_evaluations: list[DomainEvaluation] = field(default_factory=list)
    overall_score: float = 0.0
    overall_percentage: float = 0.0
    tco: Optional[TCOAnalysis] = None
    vendor_risk: RiskLevel = RiskLevel.MEDIUM
    recommendation: str = ""
    evaluation_date: str = ""


# ---------------------------------------------------------------------------
# Evaluation criteria
# ---------------------------------------------------------------------------

# Domain -> [(capability_name, weight, description)]
EVALUATION_CRITERIA: dict[RegDomain, list[tuple[str, float, str]]] = {
    RegDomain.AML_CFT: [
        ("transaction_monitoring", 0.20, "Real-time transaction monitoring with configurable rules"),
        ("sar_filing", 0.15, "Automated SAR/STR filing with regulatory submission"),
        ("sanctions_screening", 0.15, "Real-time sanctions list screening (OFAC, EU, UN, HMT)"),
        ("pep_screening", 0.12, "Politically Exposed Person identification and monitoring"),
        ("risk_scoring", 0.12, "Dynamic customer risk scoring with ML capabilities"),
        ("case_management", 0.10, "Investigation workflow and case management"),
        ("regulatory_updates", 0.08, "Automatic sanctions/PEP list updates"),
        ("audit_trail", 0.08, "Complete audit trail for regulatory examination"),
    ],
    RegDomain.KYC_IDENTITY: [
        ("document_verification", 0.20, "ID document verification (passport, driving licence, national ID)"),
        ("biometric_verification", 0.15, "Facial recognition and liveness detection"),
        ("address_verification", 0.12, "Proof of address verification"),
        ("age_verification", 0.15, "Reliable age verification for gambling compliance"),
        ("database_checks", 0.10, "Cross-reference against credit bureaus and databases"),
        ("ongoing_monitoring", 0.10, "Continuous identity monitoring post-onboarding"),
        ("global_coverage", 0.10, "Document coverage across target jurisdictions"),
        ("conversion_rate", 0.08, "Verification pass rate and player friction metrics"),
    ],
    RegDomain.PLAYER_PROTECTION: [
        ("behavioral_analytics", 0.20, "Player behavior analysis for harm detection"),
        ("marker_detection", 0.18, "Problem gambling marker identification"),
        ("interaction_triggers", 0.15, "Automated intervention triggers based on risk indicators"),
        ("self_exclusion_integration", 0.12, "Integration with GAMSTOP/national exclusion registers"),
        ("reality_checks", 0.10, "Session time and spend notifications"),
        ("deposit_limits", 0.10, "Configurable deposit/loss/session limits"),
        ("affordability_signals", 0.08, "Open banking and affordability data integration"),
        ("reporting", 0.07, "Regulatory reporting on player protection measures"),
    ],
    RegDomain.AFFORDABILITY: [
        ("open_banking", 0.25, "Open banking integration for income/expenditure verification"),
        ("credit_reference", 0.20, "Credit reference agency data for affordability assessment"),
        ("threshold_triggers", 0.15, "Configurable affordability check triggers"),
        ("frictionless_flow", 0.15, "Low-friction player experience during checks"),
        ("data_accuracy", 0.10, "Accuracy and coverage of financial data"),
        ("regulatory_alignment", 0.10, "Alignment with UK GC affordability requirements"),
        ("audit_trail", 0.05, "Audit trail for affordability decisions"),
    ],
    RegDomain.REGULATORY_REPORTING: [
        ("automated_returns", 0.25, "Automated regulatory return generation (UK GC, MGA)"),
        ("multi_jurisdiction", 0.20, "Support for multiple jurisdictions' reporting formats"),
        ("real_time_dashboards", 0.15, "Real-time compliance dashboards"),
        ("data_aggregation", 0.15, "Data aggregation from multiple platform sources"),
        ("submission_management", 0.10, "Direct submission to regulatory portals"),
        ("change_management", 0.10, "Rapid adaptation to regulatory format changes"),
        ("historical_archive", 0.05, "Archival and retrieval of historical submissions"),
    ],
    RegDomain.FRAUD_DETECTION: [
        ("real_time_scoring", 0.20, "Real-time fraud risk scoring on transactions"),
        ("multi_accounting", 0.18, "Multi-account detection (device fingerprint, behavioral)"),
        ("bonus_abuse", 0.15, "Bonus abuse and collusion detection"),
        ("payment_fraud", 0.15, "Payment fraud detection (stolen cards, chargebacks)"),
        ("ip_geo_analysis", 0.10, "IP/geolocation analysis and VPN detection"),
        ("device_intelligence", 0.12, "Device fingerprinting and anomaly detection"),
        ("ml_models", 0.10, "Machine learning model customization and retraining"),
    ],
}

# Domain weights for overall score
DOMAIN_WEIGHTS = {
    RegDomain.AML_CFT: 0.20,
    RegDomain.KYC_IDENTITY: 0.18,
    RegDomain.PLAYER_PROTECTION: 0.18,
    RegDomain.AFFORDABILITY: 0.12,
    RegDomain.REGULATORY_REPORTING: 0.10,
    RegDomain.FRAUD_DETECTION: 0.15,
    RegDomain.DATA_PRIVACY: 0.04,
    RegDomain.RESPONSIBLE_GAMBLING: 0.03,
}


# ---------------------------------------------------------------------------
# Evaluator engine
# ---------------------------------------------------------------------------

class RegTechEvaluator:
    """
    Systematic vendor evaluation engine for RegTech solutions.

    Usage:
        1. Create vendor profiles with known capabilities
        2. Score each vendor against evaluation criteria
        3. Compare vendors side-by-side
        4. Generate TCO analysis
        5. Produce recommendation report

    Production integration:
        - Use as internal procurement tool during RFP/RFI process
        - Feed scores into GRC platform for vendor management
        - Schedule annual re-evaluation for existing vendors
    """

    def __init__(self):
        self.vendors: dict[str, VendorProfile] = {}
        self.evaluations: dict[str, VendorEvaluation] = {}

    def register_vendor(self, vendor: VendorProfile):
        """Register a vendor for evaluation."""
        self.vendors[vendor.vendor_id] = vendor
        logger.info(f"Registered vendor: {vendor.name} ({vendor.tier.value})")

    def evaluate_vendor(
        self,
        vendor_id: str,
        scores: dict[RegDomain, dict[str, int]],
    ) -> VendorEvaluation:
        """
        Evaluate a vendor by providing scores for each capability.

        Args:
            vendor_id: The vendor to evaluate
            scores: {domain: {capability_name: score_1_to_5}}

        Returns:
            Complete VendorEvaluation with weighted scores and gaps.
        """
        vendor = self.vendors.get(vendor_id)
        if not vendor:
            raise ValueError(f"Vendor {vendor_id} not registered")

        domain_evaluations = []

        for domain, criteria in EVALUATION_CRITERIA.items():
            domain_scores = scores.get(domain, {})
            capabilities = []
            strengths = []
            gaps = []

            for cap_name, weight, description in criteria:
                score = domain_scores.get(cap_name, 0)
                cap = CapabilityScore(
                    capability=cap_name,
                    weight=weight,
                    score=score,
                    evidence=description,
                )

                if score == 0:
                    cap.gap = f"Not evaluated or not offered: {cap_name}"
                    gaps.append(cap_name)
                elif score <= 2:
                    cap.gap = f"Below minimum threshold: {cap_name} ({score}/5)"
                    gaps.append(f"{cap_name} (scored {score}/5)")
                elif score >= 4:
                    strengths.append(f"{cap_name} ({score}/5)")

                capabilities.append(cap)

            # Calculate weighted score
            weighted_score = sum(c.weight * c.score for c in capabilities)
            max_possible = sum(c.weight * c.max_score for c in capabilities)
            percentage = (weighted_score / max_possible * 100) if max_possible > 0 else 0

            domain_evaluations.append(DomainEvaluation(
                domain=domain,
                capabilities=capabilities,
                weighted_score=round(weighted_score, 3),
                max_possible=round(max_possible, 3),
                percentage=round(percentage, 1),
                strengths=strengths,
                gaps=gaps,
            ))

        # Calculate overall score
        overall_weighted = 0.0
        overall_max = 0.0
        for de in domain_evaluations:
            domain_weight = DOMAIN_WEIGHTS.get(de.domain, 0.05)
            overall_weighted += de.weighted_score * domain_weight
            overall_max += de.max_possible * domain_weight

        overall_pct = (overall_weighted / overall_max * 100) if overall_max > 0 else 0

        # TCO analysis
        tco = self._calculate_tco(vendor)

        # Vendor risk assessment
        vendor_risk = self._assess_vendor_risk(vendor, domain_evaluations)

        # Recommendation
        recommendation = self._generate_recommendation(
            vendor, overall_pct, domain_evaluations, vendor_risk
        )

        evaluation = VendorEvaluation(
            vendor=vendor,
            domain_evaluations=domain_evaluations,
            overall_score=round(overall_weighted, 3),
            overall_percentage=round(overall_pct, 1),
            tco=tco,
            vendor_risk=vendor_risk,
            recommendation=recommendation,
            evaluation_date=datetime.now(timezone.utc).isoformat(),
        )

        self.evaluations[vendor_id] = evaluation
        return evaluation

    def compare_vendors(self, vendor_ids: list[str]) -> dict:
        """
        Side-by-side comparison of evaluated vendors.
        Returns structured comparison data for decision-making.
        """
        comparisons = []
        for vid in vendor_ids:
            ev = self.evaluations.get(vid)
            if not ev:
                continue

            domain_scores = {
                de.domain.value: {
                    "score": de.weighted_score,
                    "percentage": de.percentage,
                    "gaps": len(de.gaps),
                }
                for de in ev.domain_evaluations
            }

            comparisons.append({
                "vendor_id": ev.vendor.vendor_id,
                "vendor_name": ev.vendor.name,
                "tier": ev.vendor.tier.value,
                "overall_score": ev.overall_percentage,
                "risk_level": ev.vendor_risk.value,
                "year_1_cost": ev.tco.year_1_cost if ev.tco else 0,
                "year_3_cost": ev.tco.year_3_cost if ev.tco else 0,
                "domains": domain_scores,
                "jurisdictions": len(ev.vendor.jurisdictions_supported),
                "integration_weeks": ev.vendor.integration_weeks_estimate,
                "recommendation": ev.recommendation,
            })

        # Rank by overall score
        comparisons.sort(key=lambda x: x["overall_score"], reverse=True)

        return {
            "comparison_date": datetime.now(timezone.utc).isoformat(),
            "vendors_compared": len(comparisons),
            "rankings": comparisons,
            "best_overall": comparisons[0]["vendor_name"] if comparisons else "N/A",
            "best_value": min(comparisons, key=lambda x: x["year_3_cost"])["vendor_name"]
            if comparisons else "N/A",
        }

    def _calculate_tco(self, vendor: VendorProfile) -> TCOAnalysis:
        """Calculate Total Cost of Ownership over 1, 3, and 5 years."""
        base_annual = vendor.annual_cost_estimate

        # Integration cost (one-time)
        integration_cost = vendor.integration_weeks_estimate * 15000  # ~15K/week
        if vendor.tier == VendorTier.ENTERPRISE:
            integration_cost *= 1.5
        elif vendor.tier == VendorTier.STARTUP:
            integration_cost *= 0.8

        # Annual operational cost (staff time for vendor management)
        ops_annual = 25000  # 0.5 FTE compliance analyst
        if vendor.tier == VendorTier.ENTERPRISE:
            ops_annual = 15000  # better self-service
        elif vendor.tier == VendorTier.STARTUP:
            ops_annual = 40000  # more hand-holding needed

        # Annual maintenance/upgrade cost
        maintenance_annual = base_annual * 0.10  # 10% of license for updates

        # Hidden costs
        hidden_costs = []
        if IntegrationMethod.BATCH_FILE in vendor.integration_methods:
            hidden_costs.append("Manual data reconciliation for batch integrations (+5K/year)")
        if vendor.years_in_market < 3:
            hidden_costs.append("Higher risk of vendor pivot/shutdown (consider escrow)")
        if len(vendor.data_residency_options) < 2:
            hidden_costs.append("Limited data residency may require additional infrastructure")
        if vendor.sla_uptime_pct < 99.9:
            hidden_costs.append(f"SLA {vendor.sla_uptime_pct}% may cause compliance gaps during downtime")

        year_1 = base_annual + integration_cost + ops_annual + maintenance_annual
        year_3 = base_annual * 3 + integration_cost + ops_annual * 3 + maintenance_annual * 3
        year_5 = base_annual * 5 + integration_cost + ops_annual * 5 + maintenance_annual * 5

        return TCOAnalysis(
            vendor_id=vendor.vendor_id,
            year_1_cost=round(year_1),
            year_3_cost=round(year_3),
            year_5_cost=round(year_5),
            breakdown={
                "license_annual": base_annual,
                "integration_one_time": integration_cost,
                "operations_annual": ops_annual,
                "maintenance_annual": maintenance_annual,
            },
            cost_per_check=round(base_annual / 500000, 4) if base_annual else 0,  # est 500K checks/year
            cost_per_player=round(base_annual / 100000, 2) if base_annual else 0,  # est 100K active players
            hidden_costs=hidden_costs,
        )

    def _assess_vendor_risk(
        self, vendor: VendorProfile, evaluations: list[DomainEvaluation]
    ) -> RiskLevel:
        """Assess operational risk of selecting this vendor."""
        risk_factors = 0

        # Business viability
        if vendor.years_in_market < 2:
            risk_factors += 2
        elif vendor.years_in_market < 5:
            risk_factors += 1

        if vendor.gambling_clients < 5:
            risk_factors += 2
        elif vendor.gambling_clients < 20:
            risk_factors += 1

        # Technical risk
        if vendor.sla_uptime_pct < 99.5:
            risk_factors += 2
        elif vendor.sla_uptime_pct < 99.9:
            risk_factors += 1

        if vendor.api_response_time_ms > 2000:
            risk_factors += 1

        # Compliance coverage gaps
        critical_gaps = 0
        for ev in evaluations:
            if ev.domain in (RegDomain.AML_CFT, RegDomain.KYC_IDENTITY, RegDomain.PLAYER_PROTECTION):
                if ev.percentage < 50:
                    critical_gaps += 1

        risk_factors += critical_gaps * 2

        # Jurisdiction coverage
        if len(vendor.jurisdictions_supported) < 3:
            risk_factors += 1

        if risk_factors >= 6:
            return RiskLevel.CRITICAL
        elif risk_factors >= 4:
            return RiskLevel.HIGH
        elif risk_factors >= 2:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _generate_recommendation(
        self,
        vendor: VendorProfile,
        overall_pct: float,
        evaluations: list[DomainEvaluation],
        risk: RiskLevel,
    ) -> str:
        """Generate a structured recommendation."""
        if risk == RiskLevel.CRITICAL:
            return (
                f"NOT RECOMMENDED. {vendor.name} presents critical risk factors "
                f"that may jeopardize regulatory compliance. "
                f"Overall score: {overall_pct:.0f}%."
            )

        if overall_pct >= 80:
            qualifier = "STRONGLY RECOMMENDED" if risk == RiskLevel.LOW else "RECOMMENDED WITH MONITORING"
        elif overall_pct >= 60:
            qualifier = "CONDITIONALLY RECOMMENDED"
        elif overall_pct >= 40:
            qualifier = "NOT RECOMMENDED without significant gap remediation"
        else:
            qualifier = "NOT RECOMMENDED"

        # Identify top gaps
        worst_domains = sorted(evaluations, key=lambda e: e.percentage)[:2]
        gap_note = ""
        if worst_domains and worst_domains[0].percentage < 60:
            gap_note = (
                f" Key gap: {worst_domains[0].domain.value} at {worst_domains[0].percentage:.0f}%."
            )

        return f"{qualifier}. {vendor.name} scored {overall_pct:.0f}% overall.{gap_note}"


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    """Simulate RegTech vendor evaluation for an iGaming operator."""

    evaluator = RegTechEvaluator()

    print("\n" + "=" * 70)
    print("  RegTech Vendor Evaluation Framework - Simulation")
    print("=" * 70)

    # Register vendors
    vendors = [
        VendorProfile(
            vendor_id="V001",
            name="ComplyAdvantage",
            tier=VendorTier.ENTERPRISE,
            domains=[RegDomain.AML_CFT, RegDomain.KYC_IDENTITY, RegDomain.FRAUD_DETECTION],
            jurisdictions_supported=["UK", "Malta", "Gibraltar", "Sweden", "Denmark", "Ontario", "US-NJ"],
            integration_methods=[IntegrationMethod.REST_API, IntegrationMethod.WEBHOOK],
            certifications=["ISO 27001", "SOC 2 Type II"],
            years_in_market=10,
            gambling_clients=45,
            headquarters="London, UK",
            data_residency_options=["EU", "US", "UK"],
            sla_uptime_pct=99.95,
            api_response_time_ms=200,
            pricing_model="per-check + platform fee",
            annual_cost_estimate=180000,
            integration_weeks_estimate=6,
        ),
        VendorProfile(
            vendor_id="V002",
            name="Nuvei Shield",
            tier=VendorTier.MID_MARKET,
            domains=[RegDomain.AML_CFT, RegDomain.FRAUD_DETECTION, RegDomain.PLAYER_PROTECTION],
            jurisdictions_supported=["UK", "Malta", "Gibraltar", "Ontario"],
            integration_methods=[IntegrationMethod.REST_API, IntegrationMethod.SDK],
            certifications=["SOC 2 Type II"],
            years_in_market=6,
            gambling_clients=25,
            headquarters="Montreal, Canada",
            data_residency_options=["EU", "US"],
            sla_uptime_pct=99.9,
            api_response_time_ms=350,
            pricing_model="monthly platform + per-check",
            annual_cost_estimate=120000,
            integration_weeks_estimate=4,
        ),
        VendorProfile(
            vendor_id="V003",
            name="BetBuddy Analytics",
            tier=VendorTier.SPECIALIST,
            domains=[RegDomain.PLAYER_PROTECTION, RegDomain.RESPONSIBLE_GAMBLING],
            jurisdictions_supported=["UK", "Malta", "Sweden"],
            integration_methods=[IntegrationMethod.REST_API, IntegrationMethod.BATCH_FILE],
            certifications=["ISO 27001"],
            years_in_market=8,
            gambling_clients=30,
            headquarters="London, UK",
            data_residency_options=["UK", "EU"],
            sla_uptime_pct=99.9,
            api_response_time_ms=500,
            pricing_model="annual license per active player",
            annual_cost_estimate=85000,
            integration_weeks_estimate=3,
        ),
        VendorProfile(
            vendor_id="V004",
            name="RegShield AI",
            tier=VendorTier.STARTUP,
            domains=[RegDomain.AML_CFT, RegDomain.REGULATORY_REPORTING],
            jurisdictions_supported=["UK", "Malta"],
            integration_methods=[IntegrationMethod.REST_API],
            certifications=[],
            years_in_market=1,
            gambling_clients=3,
            headquarters="Dublin, Ireland",
            data_residency_options=["EU"],
            sla_uptime_pct=99.5,
            api_response_time_ms=800,
            pricing_model="monthly subscription",
            annual_cost_estimate=45000,
            integration_weeks_estimate=8,
        ),
    ]

    for v in vendors:
        evaluator.register_vendor(v)

    # Score each vendor (in production, these come from RFP responses and demos)
    vendor_scores = {
        "V001": {
            RegDomain.AML_CFT: {
                "transaction_monitoring": 5, "sar_filing": 4, "sanctions_screening": 5,
                "pep_screening": 5, "risk_scoring": 4, "case_management": 4,
                "regulatory_updates": 5, "audit_trail": 5,
            },
            RegDomain.KYC_IDENTITY: {
                "document_verification": 4, "biometric_verification": 4,
                "address_verification": 4, "age_verification": 5,
                "database_checks": 4, "ongoing_monitoring": 3,
                "global_coverage": 5, "conversion_rate": 4,
            },
            RegDomain.PLAYER_PROTECTION: {
                "behavioral_analytics": 3, "marker_detection": 3,
                "interaction_triggers": 2, "self_exclusion_integration": 3,
                "reality_checks": 2, "deposit_limits": 2,
                "affordability_signals": 2, "reporting": 3,
            },
            RegDomain.FRAUD_DETECTION: {
                "real_time_scoring": 5, "multi_accounting": 4,
                "bonus_abuse": 3, "payment_fraud": 5,
                "ip_geo_analysis": 4, "device_intelligence": 4,
                "ml_models": 4,
            },
        },
        "V002": {
            RegDomain.AML_CFT: {
                "transaction_monitoring": 4, "sar_filing": 3, "sanctions_screening": 4,
                "pep_screening": 3, "risk_scoring": 4, "case_management": 3,
                "regulatory_updates": 3, "audit_trail": 4,
            },
            RegDomain.KYC_IDENTITY: {
                "document_verification": 3, "biometric_verification": 3,
                "address_verification": 3, "age_verification": 4,
                "database_checks": 3, "ongoing_monitoring": 3,
                "global_coverage": 3, "conversion_rate": 4,
            },
            RegDomain.PLAYER_PROTECTION: {
                "behavioral_analytics": 4, "marker_detection": 4,
                "interaction_triggers": 4, "self_exclusion_integration": 3,
                "reality_checks": 3, "deposit_limits": 4,
                "affordability_signals": 3, "reporting": 3,
            },
            RegDomain.FRAUD_DETECTION: {
                "real_time_scoring": 4, "multi_accounting": 4,
                "bonus_abuse": 4, "payment_fraud": 4,
                "ip_geo_analysis": 3, "device_intelligence": 3,
                "ml_models": 3,
            },
        },
        "V003": {
            RegDomain.PLAYER_PROTECTION: {
                "behavioral_analytics": 5, "marker_detection": 5,
                "interaction_triggers": 5, "self_exclusion_integration": 4,
                "reality_checks": 5, "deposit_limits": 4,
                "affordability_signals": 4, "reporting": 5,
            },
        },
        "V004": {
            RegDomain.AML_CFT: {
                "transaction_monitoring": 3, "sar_filing": 2, "sanctions_screening": 3,
                "pep_screening": 2, "risk_scoring": 4, "case_management": 2,
                "regulatory_updates": 2, "audit_trail": 3,
            },
            RegDomain.REGULATORY_REPORTING: {
                "automated_returns": 4, "multi_jurisdiction": 2,
                "real_time_dashboards": 4, "data_aggregation": 3,
                "submission_management": 3, "change_management": 2,
                "historical_archive": 3,
            },
        },
    }

    # Evaluate each vendor
    print("\n  Evaluating vendors...\n")
    for vendor_id, scores in vendor_scores.items():
        evaluation = evaluator.evaluate_vendor(vendor_id, scores)
        vendor = evaluation.vendor

        print(f"  {'=' * 55}")
        print(f"  {vendor.name} ({vendor.tier.value})")
        print(f"  Overall Score: {evaluation.overall_percentage:.0f}% | "
              f"Risk: {evaluation.vendor_risk.value.upper()}")

        for de in evaluation.domain_evaluations:
            if de.weighted_score > 0:
                bar_len = int(de.percentage / 5)
                bar = "#" * bar_len + "." * (20 - bar_len)
                print(f"    {de.domain.value:25s} [{bar}] {de.percentage:.0f}%")
                if de.gaps:
                    print(f"      Gaps: {', '.join(de.gaps[:3])}")

        if evaluation.tco:
            print(f"    TCO Year 1: ${evaluation.tco.year_1_cost:,.0f} | "
                  f"Year 3: ${evaluation.tco.year_3_cost:,.0f}")
            if evaluation.tco.hidden_costs:
                print(f"    Hidden costs: {evaluation.tco.hidden_costs[0]}")

        print(f"    >> {evaluation.recommendation}")

    # Side-by-side comparison
    print(f"\n  {'=' * 55}")
    print("  Vendor Comparison Summary")
    print(f"  {'=' * 55}")

    comparison = evaluator.compare_vendors(["V001", "V002", "V003", "V004"])
    print(f"  Best Overall: {comparison['best_overall']}")
    print(f"  Best Value:   {comparison['best_value']}")

    print("\n  Rankings:")
    for i, vendor in enumerate(comparison["rankings"], 1):
        print(f"    {i}. {vendor['vendor_name']:25s} "
              f"Score: {vendor['overall_score']:.0f}% | "
              f"Risk: {vendor['risk_level']:8s} | "
              f"3Y TCO: ${vendor['year_3_cost']:,.0f}")

    print(f"\n  Production usage:")
    print("    1. Use during RFP/RFI process to score vendor responses")
    print("    2. Re-evaluate annually as part of vendor management program")
    print("    3. Export to GRC platform (ServiceNow, OneTrust) for tracking")
    print("    4. Feed scores into board-level compliance reporting\n")


if __name__ == "__main__":
    demo()
