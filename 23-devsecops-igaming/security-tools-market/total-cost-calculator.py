#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Total Cost of Ownership (TCO) Calculator for Security Tooling in Casino Operations
==================================================================================

Calculates the full cost of security tooling across license, infrastructure,
personnel, training, and integration dimensions. Provides ROI analysis against
regulatory fines and breach costs to justify security investments.

This calculator is designed for iGaming operators making build-vs-buy decisions
and planning multi-year security budgets across jurisdictions.

Templates:
    - Small operator:     1 jurisdiction, 10-50 employees
    - Medium operator:    2-3 jurisdictions, 50-200 employees
    - Enterprise operator: 5+ jurisdictions, 200+ employees

Usage:
    python3 total-cost-calculator.py
    python3 total-cost-calculator.py --size medium --years 3
    python3 total-cost-calculator.py --size enterprise --jurisdictions 7
    python3 total-cost-calculator.py --json

Author: Chapter 30 -- FinOps for iGaming
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TextIO


# ---------------------------------------------------------------------------
# Cost model data classes
# ---------------------------------------------------------------------------
@dataclass
class LicenseCost:
    """Software license costs broken down by tool category."""

    sast: float = 0.0
    dast: float = 0.0
    sca: float = 0.0
    container_security: float = 0.0
    cspm: float = 0.0
    siem: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.sast
            + self.dast
            + self.sca
            + self.container_security
            + self.cspm
            + self.siem
        )


@dataclass
class InfrastructureCost:
    """Infrastructure costs for hosting security tooling."""

    compute: float = 0.0  # VMs / containers for running tools
    storage: float = 0.0  # Log storage, scan results, SBOM archives
    network: float = 0.0  # Data transfer, VPN for on-prem tools
    backup: float = 0.0  # Backup and DR for security data

    @property
    def total(self) -> float:
        return self.compute + self.storage + self.network + self.backup


@dataclass
class PersonnelCost:
    """Personnel costs for operating security tools."""

    security_engineers: float = 0.0  # FTEs dedicated to security tooling
    devops_overhead: float = 0.0  # Fraction of DevOps time on security
    management: float = 0.0  # Security management / CISO fraction
    on_call: float = 0.0  # On-call costs for security incidents

    @property
    def total(self) -> float:
        return (
            self.security_engineers
            + self.devops_overhead
            + self.management
            + self.on_call
        )


@dataclass
class TrainingCost:
    """Training and certification costs."""

    tool_training: float = 0.0  # Vendor-specific training
    certifications: float = 0.0  # CISSP, CEH, vendor certs
    conferences: float = 0.0  # Security conferences and events
    knowledge_base: float = 0.0  # Internal documentation effort

    @property
    def total(self) -> float:
        return (
            self.tool_training
            + self.certifications
            + self.conferences
            + self.knowledge_base
        )


@dataclass
class IntegrationCost:
    """Integration and maintenance overhead."""

    initial_setup: float = 0.0  # One-time setup cost
    annual_maintenance: float = 0.0  # Ongoing maintenance
    custom_rules: float = 0.0  # Gaming-specific rule development
    reporting: float = 0.0  # Compliance reporting automation

    @property
    def total(self) -> float:
        return (
            self.initial_setup
            + self.annual_maintenance
            + self.custom_rules
            + self.reporting
        )


@dataclass
class TCOResult:
    """Complete TCO calculation result."""

    operator_size: str
    jurisdictions: int
    projection_years: int
    license: LicenseCost
    infrastructure: InfrastructureCost
    personnel: PersonnelCost
    training: TrainingCost
    integration: IntegrationCost
    annual_growth_rate: float = 0.15  # 15% annual growth default

    @property
    def year_one_total(self) -> float:
        return (
            self.license.total
            + self.infrastructure.total
            + self.personnel.total
            + self.training.total
            + self.integration.total
        )

    def projected_annual_cost(self, year: int) -> float:
        """Calculate projected cost for a given year with growth."""
        if year == 1:
            return self.year_one_total
        # Year 1 includes setup costs; subsequent years have ongoing costs only
        ongoing = self.year_one_total - self.integration.initial_setup
        return ongoing * ((1 + self.annual_growth_rate) ** (year - 1))

    @property
    def total_tco(self) -> float:
        return sum(
            self.projected_annual_cost(y) for y in range(1, self.projection_years + 1)
        )

    @property
    def compliance_coverage_score(self) -> float:
        """Score 0-100 based on tool coverage of compliance frameworks."""
        # Weighted by framework importance to iGaming
        weights = {
            "pci_dss": 0.30,
            "iso27001": 0.25,
            "gli_33": 0.20,
            "soc2": 0.15,
            "gdpr": 0.10,
        }
        # Larger operators with more tools get higher coverage
        size_coverage = {"small": 0.60, "medium": 0.80, "enterprise": 0.95}
        base = size_coverage.get(self.operator_size, 0.70)
        return min(base * 100, 100.0)


# ---------------------------------------------------------------------------
# Regulatory fine data
# ---------------------------------------------------------------------------
@dataclass
class RegulatoryFine:
    """Regulatory fine benchmarks by jurisdiction."""

    jurisdiction: str
    regulator: str
    max_fine_eur: float
    avg_fine_eur: float
    common_causes: list[str] = field(default_factory=list)


REGULATORY_FINES: list[RegulatoryFine] = [
    RegulatoryFine(
        jurisdiction="Malta",
        regulator="MGA",
        max_fine_eur=500_000,
        avg_fine_eur=150_000,
        common_causes=["AML failures", "data breach", "inadequate KYC"],
    ),
    RegulatoryFine(
        jurisdiction="United Kingdom",
        regulator="UKGC",
        max_fine_eur=35_000_000,
        avg_fine_eur=2_500_000,
        common_causes=["AML failures", "responsible gaming", "data protection"],
    ),
    RegulatoryFine(
        jurisdiction="Ontario, Canada",
        regulator="AGCO",
        max_fine_eur=1_000_000,
        avg_fine_eur=250_000,
        common_causes=["Technical standards", "player protection", "data security"],
    ),
    RegulatoryFine(
        jurisdiction="Sweden",
        regulator="Spelinspektionen",
        max_fine_eur=10_000_000,
        avg_fine_eur=500_000,
        common_causes=["Bonus violations", "AML", "self-exclusion failures"],
    ),
    RegulatoryFine(
        jurisdiction="Netherlands",
        regulator="KSA",
        max_fine_eur=20_000_000,
        avg_fine_eur=1_000_000,
        common_causes=["Unlicensed operations", "advertising violations"],
    ),
]


# ---------------------------------------------------------------------------
# TCO templates by operator size
# ---------------------------------------------------------------------------
def calculate_small_operator(
    jurisdictions: int = 1,
    years: int = 3,
) -> TCOResult:
    """
    Small operator: 1 jurisdiction, <50 employees.

    Strategy: Maximise open-source tools, minimal commercial licenses.
    Typical stack: SonarQube Community, OWASP ZAP, Trivy, Prowler, Wazuh.
    """
    return TCOResult(
        operator_size="small",
        jurisdictions=jurisdictions,
        projection_years=years,
        license=LicenseCost(
            sast=0,  # SonarQube Community (free)
            dast=0,  # OWASP ZAP (free)
            sca=0,  # Grype + Syft (free)
            container_security=0,  # Trivy (free)
            cspm=0,  # Prowler (free)
            siem=0,  # Wazuh (free)
        ),
        infrastructure=InfrastructureCost(
            compute=12_000,  # 2-3 VMs for SonarQube, Wazuh
            storage=3_600,  # 1 TB log storage
            network=1_200,  # Minimal data transfer
            backup=600,  # Basic backup
        ),
        personnel=PersonnelCost(
            security_engineers=45_000,  # 0.5 FTE security engineer
            devops_overhead=15_000,  # 0.2 FTE DevOps on security
            management=10_000,  # CTO handles security part-time
            on_call=5_000,  # Shared on-call rotation
        ),
        training=TrainingCost(
            tool_training=2_000,  # Online courses
            certifications=3_000,  # 1 cert per year
            conferences=1_500,  # 1 virtual conference
            knowledge_base=500,  # Internal wiki
        ),
        integration=IntegrationCost(
            initial_setup=15_000,  # Consultant for initial setup
            annual_maintenance=6_000,  # Part-time maintenance
            custom_rules=3_000,  # Basic gaming rules
            reporting=2_000,  # Manual reporting
        ),
        annual_growth_rate=0.10,
    )


def calculate_medium_operator(
    jurisdictions: int = 3,
    years: int = 3,
) -> TCOResult:
    """
    Medium operator: 2-3 jurisdictions, 50-200 employees.

    Strategy: Mix of open-source and commercial for critical areas.
    Typical stack: SonarQube Developer, Burp Suite, Snyk, Aqua/Trivy,
                   Prowler + Checkov, Elastic Security.
    """
    return TCOResult(
        operator_size="medium",
        jurisdictions=jurisdictions,
        projection_years=years,
        license=LicenseCost(
            sast=24_000,  # SonarQube Developer Edition
            dast=15_000,  # Burp Suite Professional (3 licenses)
            sca=36_000,  # Snyk Team plan
            container_security=48_000,  # Aqua essentials
            cspm=0,  # Prowler + Checkov (free)
            siem=36_000,  # Elastic Security (self-managed)
        ),
        infrastructure=InfrastructureCost(
            compute=48_000,  # 6-8 VMs / containers
            storage=14_400,  # 4 TB log + scan storage
            network=6_000,  # Multi-region data transfer
            backup=3_600,  # Automated backup
        ),
        personnel=PersonnelCost(
            security_engineers=180_000,  # 2 FTE security engineers
            devops_overhead=45_000,  # 0.5 FTE DevOps
            management=40_000,  # Security lead (part of broader role)
            on_call=15_000,  # Security on-call rotation
        ),
        training=TrainingCost(
            tool_training=10_000,  # Vendor training courses
            certifications=9_000,  # 3 certs per year
            conferences=6_000,  # 1-2 in-person conferences
            knowledge_base=3_000,  # Knowledge base maintenance
        ),
        integration=IntegrationCost(
            initial_setup=40_000,  # Professional services
            annual_maintenance=18_000,  # Ongoing maintenance
            custom_rules=12_000,  # Gaming-specific rule dev
            reporting=8_000,  # Semi-automated reporting
        ),
        annual_growth_rate=0.15,
    )


def calculate_enterprise_operator(
    jurisdictions: int = 7,
    years: int = 3,
) -> TCOResult:
    """
    Enterprise operator: 5+ jurisdictions, 200+ employees.

    Strategy: Best-of-breed commercial tools with full coverage.
    Typical stack: Checkmarx, Acunetix + Nessus, Black Duck, Aqua Enterprise,
                   Wiz, Splunk Enterprise Security.
    """
    return TCOResult(
        operator_size="enterprise",
        jurisdictions=jurisdictions,
        projection_years=years,
        license=LicenseCost(
            sast=120_000,  # Checkmarx SAST
            dast=85_000,  # Acunetix + Nessus Pro
            sca=96_000,  # Black Duck
            container_security=180_000,  # Aqua Enterprise
            cspm=150_000,  # Wiz
            siem=250_000,  # Splunk Enterprise Security
        ),
        infrastructure=InfrastructureCost(
            compute=120_000,  # 15-20 VMs / K8s nodes
            storage=48_000,  # 15+ TB across regions
            network=24_000,  # Multi-region, multi-cloud
            backup=12_000,  # HA backup with DR
        ),
        personnel=PersonnelCost(
            security_engineers=540_000,  # 4-6 FTE security engineers
            devops_overhead=90_000,  # 1 FTE DevSecOps
            management=120_000,  # CISO + security managers
            on_call=36_000,  # 24/7 security on-call
        ),
        training=TrainingCost(
            tool_training=30_000,  # Full vendor training programs
            certifications=24_000,  # Multiple certs per year
            conferences=18_000,  # Multiple conferences
            knowledge_base=8_000,  # Formal knowledge management
        ),
        integration=IntegrationCost(
            initial_setup=100_000,  # Professional services + internal
            annual_maintenance=48_000,  # Dedicated maintenance
            custom_rules=36_000,  # Extensive gaming rule library
            reporting=24_000,  # Automated compliance reporting
        ),
        annual_growth_rate=0.12,
    )


# ---------------------------------------------------------------------------
# ROI calculation
# ---------------------------------------------------------------------------
def calculate_roi(
    tco: TCOResult,
    output: TextIO = sys.stdout,
) -> None:
    """Calculate ROI of security tooling vs regulatory fines and breaches."""
    output.write(f"\n{'=' * 80}\n")
    output.write("  ROI Analysis: Security Tooling vs Risk Exposure\n")
    output.write(f"{'=' * 80}\n\n")

    # Average cost of a data breach in gaming (IBM + industry data)
    avg_breach_cost = 4_500_000  # EUR
    breach_probability_without_tools = 0.15  # 15% annual probability
    breach_probability_with_tools = 0.03  # 3% with proper tooling

    # Regulatory fine exposure
    relevant_fines = REGULATORY_FINES[: tco.jurisdictions]
    total_fine_exposure = sum(f.avg_fine_eur for f in relevant_fines)
    fine_probability_without_tools = 0.20  # 20% chance of fine per year
    fine_probability_with_tools = 0.05  # 5% with proper tooling

    # Expected annual loss without tools
    expected_breach_loss = avg_breach_cost * breach_probability_without_tools
    expected_fine_loss = total_fine_exposure * fine_probability_without_tools
    total_expected_loss_without = expected_breach_loss + expected_fine_loss

    # Expected annual loss with tools
    expected_breach_loss_with = avg_breach_cost * breach_probability_with_tools
    expected_fine_loss_with = total_fine_exposure * fine_probability_with_tools
    total_expected_loss_with = expected_breach_loss_with + expected_fine_loss_with

    # Annual risk reduction
    annual_risk_reduction = total_expected_loss_without - total_expected_loss_with

    # ROI percentage
    annual_cost = tco.year_one_total
    roi_pct = ((annual_risk_reduction - annual_cost) / annual_cost) * 100

    output.write("  Risk Exposure (without security tooling):\n")
    output.write(f"    Breach probability:    {breach_probability_without_tools:.0%}/year\n")
    output.write(f"    Expected breach loss:   EUR {expected_breach_loss:>12,.0f}\n")
    output.write(f"    Fine probability:      {fine_probability_without_tools:.0%}/year\n")
    output.write(f"    Expected fine loss:     EUR {expected_fine_loss:>12,.0f}\n")
    output.write(f"    Total expected loss:    EUR {total_expected_loss_without:>12,.0f}\n\n")

    output.write("  Risk Exposure (with security tooling):\n")
    output.write(f"    Breach probability:    {breach_probability_with_tools:.0%}/year\n")
    output.write(f"    Expected breach loss:   EUR {expected_breach_loss_with:>12,.0f}\n")
    output.write(f"    Fine probability:      {fine_probability_with_tools:.0%}/year\n")
    output.write(f"    Expected fine loss:     EUR {expected_fine_loss_with:>12,.0f}\n")
    output.write(f"    Total expected loss:    EUR {total_expected_loss_with:>12,.0f}\n\n")

    output.write("  ROI Calculation:\n")
    output.write(f"    Annual risk reduction:  EUR {annual_risk_reduction:>12,.0f}\n")
    output.write(f"    Annual tooling cost:    EUR {annual_cost:>12,.0f}\n")
    output.write(f"    Net annual benefit:     EUR {annual_risk_reduction - annual_cost:>12,.0f}\n")
    output.write(f"    ROI:                    {roi_pct:>11.1f}%\n\n")

    output.write("  Regulatory Fine Benchmarks:\n")
    for fine in relevant_fines:
        output.write(
            f"    {fine.jurisdiction:<20} ({fine.regulator}): "
            f"avg EUR {fine.avg_fine_eur:>12,.0f}, "
            f"max EUR {fine.max_fine_eur:>12,.0f}\n"
        )
    output.write("\n")


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def print_tco_report(
    tco: TCOResult,
    output: TextIO = sys.stdout,
) -> None:
    """Print a detailed TCO breakdown report."""
    output.write(f"\n{'=' * 80}\n")
    output.write(f"  Security Tooling TCO: {tco.operator_size.upper()} Operator\n")
    output.write(f"  {tco.jurisdictions} jurisdiction(s), {tco.projection_years}-year projection\n")
    output.write(f"{'=' * 80}\n\n")

    # License costs
    output.write("  1. LICENSE COSTS (Annual)\n")
    output.write(f"     SAST:                EUR {tco.license.sast:>10,.0f}\n")
    output.write(f"     DAST:                EUR {tco.license.dast:>10,.0f}\n")
    output.write(f"     SCA:                 EUR {tco.license.sca:>10,.0f}\n")
    output.write(f"     Container Security:  EUR {tco.license.container_security:>10,.0f}\n")
    output.write(f"     CSPM:                EUR {tco.license.cspm:>10,.0f}\n")
    output.write(f"     SIEM:                EUR {tco.license.siem:>10,.0f}\n")
    output.write(f"     {'SUBTOTAL':<21} EUR {tco.license.total:>10,.0f}\n\n")

    # Infrastructure costs
    output.write("  2. INFRASTRUCTURE COSTS (Annual)\n")
    output.write(f"     Compute:             EUR {tco.infrastructure.compute:>10,.0f}\n")
    output.write(f"     Storage:             EUR {tco.infrastructure.storage:>10,.0f}\n")
    output.write(f"     Network:             EUR {tco.infrastructure.network:>10,.0f}\n")
    output.write(f"     Backup/DR:           EUR {tco.infrastructure.backup:>10,.0f}\n")
    output.write(f"     {'SUBTOTAL':<21} EUR {tco.infrastructure.total:>10,.0f}\n\n")

    # Personnel costs
    output.write("  3. PERSONNEL COSTS (Annual)\n")
    output.write(f"     Security Engineers:   EUR {tco.personnel.security_engineers:>10,.0f}\n")
    output.write(f"     DevOps Overhead:      EUR {tco.personnel.devops_overhead:>10,.0f}\n")
    output.write(f"     Management:           EUR {tco.personnel.management:>10,.0f}\n")
    output.write(f"     On-Call:              EUR {tco.personnel.on_call:>10,.0f}\n")
    output.write(f"     {'SUBTOTAL':<21} EUR {tco.personnel.total:>10,.0f}\n\n")

    # Training costs
    output.write("  4. TRAINING & CERTIFICATION (Annual)\n")
    output.write(f"     Tool Training:        EUR {tco.training.tool_training:>10,.0f}\n")
    output.write(f"     Certifications:       EUR {tco.training.certifications:>10,.0f}\n")
    output.write(f"     Conferences:          EUR {tco.training.conferences:>10,.0f}\n")
    output.write(f"     Knowledge Base:       EUR {tco.training.knowledge_base:>10,.0f}\n")
    output.write(f"     {'SUBTOTAL':<21} EUR {tco.training.total:>10,.0f}\n\n")

    # Integration costs
    output.write("  5. INTEGRATION & MAINTENANCE\n")
    output.write(f"     Initial Setup:        EUR {tco.integration.initial_setup:>10,.0f}\n")
    output.write(f"     Annual Maintenance:   EUR {tco.integration.annual_maintenance:>10,.0f}\n")
    output.write(f"     Custom Rules:         EUR {tco.integration.custom_rules:>10,.0f}\n")
    output.write(f"     Reporting:            EUR {tco.integration.reporting:>10,.0f}\n")
    output.write(f"     {'SUBTOTAL':<21} EUR {tco.integration.total:>10,.0f}\n\n")

    # Year 1 total
    output.write(f"  {'─' * 50}\n")
    output.write(f"  YEAR 1 TOTAL:            EUR {tco.year_one_total:>10,.0f}\n")
    output.write(f"  {'─' * 50}\n\n")

    # Multi-year projection
    output.write(f"  {tco.projection_years}-YEAR PROJECTION ({tco.annual_growth_rate:.0%} annual growth)\n")
    for year in range(1, tco.projection_years + 1):
        annual = tco.projected_annual_cost(year)
        output.write(f"     Year {year}:               EUR {annual:>10,.0f}\n")
    output.write(f"     {'TOTAL TCO':<21} EUR {tco.total_tco:>10,.0f}\n\n")

    # Compliance coverage
    output.write(f"  COMPLIANCE COVERAGE SCORE: {tco.compliance_coverage_score:.0f}/100\n\n")


def export_tco_json(tco: TCOResult, output: TextIO = sys.stdout) -> None:
    """Export TCO result as JSON."""
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operator_size": tco.operator_size,
        "jurisdictions": tco.jurisdictions,
        "projection_years": tco.projection_years,
        "year_one_total": round(tco.year_one_total, 2),
        "total_tco": round(tco.total_tco, 2),
        "compliance_coverage_score": round(tco.compliance_coverage_score, 1),
        "annual_growth_rate": tco.annual_growth_rate,
        "breakdown": {
            "license": round(tco.license.total, 2),
            "infrastructure": round(tco.infrastructure.total, 2),
            "personnel": round(tco.personnel.total, 2),
            "training": round(tco.training.total, 2),
            "integration": round(tco.integration.total, 2),
        },
        "projection": {
            f"year_{y}": round(tco.projected_annual_cost(y), 2)
            for y in range(1, tco.projection_years + 1)
        },
    }
    output.write(json.dumps(data, indent=2))
    output.write("\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    """Entry point for the TCO calculator."""
    parser = argparse.ArgumentParser(
        description="TCO Calculator for Security Tooling in Casino Operations",
    )
    parser.add_argument(
        "--size",
        default="medium",
        choices=["small", "medium", "enterprise"],
        help="Operator size (default: medium)",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=3,
        help="Projection period in years (default: 3)",
    )
    parser.add_argument(
        "--jurisdictions",
        type=int,
        default=0,
        help="Number of jurisdictions (0 = use template default)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Export results as JSON",
    )
    parser.add_argument(
        "--compare-all",
        action="store_true",
        help="Compare all three operator sizes side by side",
    )

    args = parser.parse_args()

    print(f"Security Tooling TCO Calculator for iGaming")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    if args.compare_all:
        # Side-by-side comparison
        results: list[TCOResult] = []
        for size_name, calc_fn, default_j in [
            ("small", calculate_small_operator, 1),
            ("medium", calculate_medium_operator, 3),
            ("enterprise", calculate_enterprise_operator, 7),
        ]:
            j = args.jurisdictions if args.jurisdictions > 0 else default_j
            result = calc_fn(jurisdictions=j, years=args.years)
            results.append(result)

        # Print comparison table
        print(f"\n{'=' * 80}")
        print("  Side-by-Side TCO Comparison")
        print(f"{'=' * 80}\n")

        header = f"{'Cost Category':<25} {'Small':>15} {'Medium':>15} {'Enterprise':>15}"
        print(header)
        print("-" * len(header))

        categories = [
            ("Licenses", lambda r: r.license.total),
            ("Infrastructure", lambda r: r.infrastructure.total),
            ("Personnel", lambda r: r.personnel.total),
            ("Training", lambda r: r.training.total),
            ("Integration", lambda r: r.integration.total),
        ]

        for label, getter in categories:
            vals = [f"EUR {getter(r):>10,.0f}" for r in results]
            print(f"{label:<25} {vals[0]:>15} {vals[1]:>15} {vals[2]:>15}")

        print("-" * len(header))
        totals = [f"EUR {r.year_one_total:>10,.0f}" for r in results]
        print(f"{'YEAR 1 TOTAL':<25} {totals[0]:>15} {totals[1]:>15} {totals[2]:>15}")

        tco_totals = [f"EUR {r.total_tco:>10,.0f}" for r in results]
        print(
            f"{f'{args.years}-YEAR TCO':<25} "
            f"{tco_totals[0]:>15} {tco_totals[1]:>15} {tco_totals[2]:>15}"
        )

        scores = [f"{r.compliance_coverage_score:>10.0f}/100" for r in results]
        print(
            f"{'COMPLIANCE SCORE':<25} {scores[0]:>15} {scores[1]:>15} {scores[2]:>15}"
        )
        print()

        # ROI for each
        for result in results:
            calculate_roi(result)

    else:
        # Single operator calculation
        size_calculators = {
            "small": (calculate_small_operator, 1),
            "medium": (calculate_medium_operator, 3),
            "enterprise": (calculate_enterprise_operator, 7),
        }

        calc_fn, default_j = size_calculators[args.size]
        j = args.jurisdictions if args.jurisdictions > 0 else default_j
        result = calc_fn(jurisdictions=j, years=args.years)

        if args.json:
            export_tco_json(result)
        else:
            print_tco_report(result)
            calculate_roi(result)


if __name__ == "__main__":
    main()
