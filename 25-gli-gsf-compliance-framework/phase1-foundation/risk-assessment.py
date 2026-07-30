#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
risk-assessment.py - GLI-GSF Risk Assessment Tool

Implements risk assessment methodology per GLI-GSF-1, Section 2.3.7 using:
  - CVSS v3.1 scoring for vulnerability severity
  - ISO 31010 risk treatment framework for risk evaluation
  - iGaming-specific threat modeling (game manipulation, RNG compromise,
    payment fraud, player data breach, DDoS, bot attacks)

The tool supports:
  1. Interactive risk assessment with guided questionnaires
  2. Import from vulnerability scanner output (Nessus CSV, Qualys XML)
  3. Automated risk scoring and treatment recommendations
  4. Report generation in JSON/CSV/Markdown for ISF evidence packages

GLI-GSF remediation timelines:
  - Critical (CVSS 9.0-10.0): 24 hours
  - High (CVSS 7.0-8.9): 7 days
  - Medium (CVSS 4.0-6.9): 30 days
  - Low (CVSS 0.1-3.9): Next quarterly cycle

Usage:
    python3 risk-assessment.py --interactive
    python3 risk-assessment.py --import-nessus scan_results.csv
    python3 risk-assessment.py --demo --output report.json

Requirements:
    No external dependencies (standard library only)
"""

import argparse
import csv
import io
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VERSION = "1.0.0"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("risk-assessment")


# ---------------------------------------------------------------------------
# CVSS v3.1 Implementation
# ---------------------------------------------------------------------------
class AttackVector(str, Enum):
    NETWORK = "N"
    ADJACENT = "A"
    LOCAL = "L"
    PHYSICAL = "P"


class AttackComplexity(str, Enum):
    LOW = "L"
    HIGH = "H"


class PrivilegesRequired(str, Enum):
    NONE = "N"
    LOW = "L"
    HIGH = "H"


class UserInteraction(str, Enum):
    NONE = "N"
    REQUIRED = "R"


class Scope(str, Enum):
    UNCHANGED = "U"
    CHANGED = "C"


class Impact(str, Enum):
    NONE = "N"
    LOW = "L"
    HIGH = "H"


@dataclass
class CVSSVector:
    """CVSS v3.1 Base Score vector."""

    attack_vector: AttackVector = AttackVector.NETWORK
    attack_complexity: AttackComplexity = AttackComplexity.LOW
    privileges_required: PrivilegesRequired = PrivilegesRequired.NONE
    user_interaction: UserInteraction = UserInteraction.NONE
    scope: Scope = Scope.UNCHANGED
    confidentiality: Impact = Impact.HIGH
    integrity: Impact = Impact.HIGH
    availability: Impact = Impact.HIGH

    def calculate_score(self) -> float:
        """Calculate CVSS v3.1 base score per specification."""
        # Exploitability metrics weights
        av_weights = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20}
        ac_weights = {"L": 0.77, "H": 0.44}
        pr_weights_unchanged = {"N": 0.85, "L": 0.62, "H": 0.27}
        pr_weights_changed = {"N": 0.85, "L": 0.68, "H": 0.50}
        ui_weights = {"N": 0.85, "R": 0.62}

        # Impact metrics weights
        impact_weights = {"N": 0, "L": 0.22, "H": 0.56}

        # Exploitability sub-score
        av = av_weights[self.attack_vector.value]
        ac = ac_weights[self.attack_complexity.value]

        if self.scope == Scope.CHANGED:
            pr = pr_weights_changed[self.privileges_required.value]
        else:
            pr = pr_weights_unchanged[self.privileges_required.value]

        ui = ui_weights[self.user_interaction.value]
        exploitability = 8.22 * av * ac * pr * ui

        # Impact sub-score
        isc_conf = impact_weights[self.confidentiality.value]
        isc_integ = impact_weights[self.integrity.value]
        isc_avail = impact_weights[self.availability.value]

        isc_base = 1 - ((1 - isc_conf) * (1 - isc_integ) * (1 - isc_avail))

        if self.scope == Scope.UNCHANGED:
            impact = 6.42 * isc_base
        else:
            impact = 7.52 * (isc_base - 0.029) - 3.25 * (isc_base - 0.02) ** 15

        if impact <= 0:
            return 0.0

        if self.scope == Scope.UNCHANGED:
            score = min(impact + exploitability, 10.0)
        else:
            score = min(1.08 * (impact + exploitability), 10.0)

        # Round up to one decimal per CVSS spec
        return math.ceil(score * 10) / 10

    def to_string(self) -> str:
        """Generate CVSS vector string."""
        return (
            f"CVSS:3.1/AV:{self.attack_vector.value}"
            f"/AC:{self.attack_complexity.value}"
            f"/PR:{self.privileges_required.value}"
            f"/UI:{self.user_interaction.value}"
            f"/S:{self.scope.value}"
            f"/C:{self.confidentiality.value}"
            f"/I:{self.integrity.value}"
            f"/A:{self.availability.value}"
        )


# ---------------------------------------------------------------------------
# Risk Models
# ---------------------------------------------------------------------------
class RiskSeverity(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFO = "Informational"


class TreatmentOption(str, Enum):
    """ISO 31010 risk treatment options."""
    MITIGATE = "Mitigate"      # Reduce likelihood or impact
    TRANSFER = "Transfer"      # Insurance, outsource
    ACCEPT = "Accept"          # Documented acceptance with rationale
    AVOID = "Avoid"            # Eliminate the risk source


class RiskCategory(str, Enum):
    """iGaming-specific risk categories."""
    GAME_INTEGRITY = "Game Integrity"
    RNG_COMPROMISE = "RNG Compromise"
    PAYMENT_FRAUD = "Payment Fraud"
    PLAYER_DATA = "Player Data Breach"
    PLATFORM_AVAILABILITY = "Platform Availability"
    REGULATORY_COMPLIANCE = "Regulatory Compliance"
    VENDOR_RISK = "Vendor/Third-Party Risk"
    BOT_ABUSE = "Bot/Automation Abuse"
    INSIDER_THREAT = "Insider Threat"
    MOBILE_SECURITY = "Mobile Application Security"


# GLI-GSF remediation timelines
REMEDIATION_TIMELINES = {
    RiskSeverity.CRITICAL: {"days": 1, "label": "24 hours", "regulatory_notice": "Immediate"},
    RiskSeverity.HIGH: {"days": 7, "label": "7 days", "regulatory_notice": "Within 30 days"},
    RiskSeverity.MEDIUM: {"days": 30, "label": "30 days", "regulatory_notice": "Within 30 days"},
    RiskSeverity.LOW: {"days": 90, "label": "Next quarterly cycle", "regulatory_notice": "Annual report"},
}


@dataclass
class RiskEntry:
    """A single risk assessment entry."""

    risk_id: str
    title: str
    description: str
    category: str
    affected_csc: str                   # CSC ID from inventory
    ogis_domain: str                    # Which OGIS control domain
    threat_scenario: str                # Concrete attack scenario
    cvss_vector: str                    # CVSS string
    cvss_score: float
    severity: str
    likelihood: str                     # High/Medium/Low
    business_impact: str                # Description of business impact
    existing_controls: List[str]        # Controls already in place
    treatment: str                      # ISO 31010 treatment option
    treatment_plan: str                 # What to do
    remediation_deadline: str           # ISO date
    owner: str                          # Responsible person/team
    status: str = "Open"               # Open, In Progress, Remediated, Accepted
    regulatory_notice_required: bool = False
    notes: str = ""
    assessed_date: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class RiskAssessment:
    """Complete risk assessment report."""

    organization: str
    assessment_date: str
    assessor: str
    scope: str
    methodology: str = "CVSS v3.1 + ISO 31010"
    gsf_reference: str = "GLI-GSF-1, Section 2.3.7"
    entries: List[RiskEntry] = field(default_factory=list)

    @property
    def total_risks(self) -> int:
        return len(self.entries)

    @property
    def critical_count(self) -> int:
        return sum(1 for e in self.entries if e.severity == RiskSeverity.CRITICAL.value)

    @property
    def high_count(self) -> int:
        return sum(1 for e in self.entries if e.severity == RiskSeverity.HIGH.value)

    def by_severity(self) -> Dict[str, List[RiskEntry]]:
        result: Dict[str, List[RiskEntry]] = {}
        for entry in self.entries:
            result.setdefault(entry.severity, []).append(entry)
        return result

    def by_category(self) -> Dict[str, List[RiskEntry]]:
        result: Dict[str, List[RiskEntry]] = {}
        for entry in self.entries:
            result.setdefault(entry.category, []).append(entry)
        return result


# ---------------------------------------------------------------------------
# Severity classifier
# ---------------------------------------------------------------------------
def classify_severity(cvss_score: float) -> RiskSeverity:
    """Classify risk severity based on CVSS score per GLI-GSF thresholds."""
    if cvss_score >= 9.0:
        return RiskSeverity.CRITICAL
    elif cvss_score >= 7.0:
        return RiskSeverity.HIGH
    elif cvss_score >= 4.0:
        return RiskSeverity.MEDIUM
    elif cvss_score > 0:
        return RiskSeverity.LOW
    return RiskSeverity.INFO


def calculate_deadline(severity: RiskSeverity) -> str:
    """Calculate remediation deadline based on GLI-GSF timelines."""
    timeline = REMEDIATION_TIMELINES.get(severity)
    if timeline:
        deadline = datetime.now(timezone.utc) + timedelta(days=timeline["days"])  # ty:ignore[invalid-argument-type]
        return deadline.isoformat()
    return ""


# ---------------------------------------------------------------------------
# iGaming Threat Library
# ---------------------------------------------------------------------------
IGAMING_THREATS = [
    {
        "title": "RNG Output Prediction via Side-Channel Attack",
        "category": RiskCategory.RNG_COMPROMISE.value,
        "ogis_domain": "OGIS-1",
        "threat_scenario": (
            "Attacker exploits timing side-channels or memory access patterns "
            "to predict RNG output sequences, enabling systematic game exploitation. "
            "This is the highest-impact threat for any online gaming platform."
        ),
        "cvss_vector": CVSSVector(
            attack_vector=AttackVector.NETWORK,
            attack_complexity=AttackComplexity.HIGH,
            privileges_required=PrivilegesRequired.NONE,
            user_interaction=UserInteraction.NONE,
            scope=Scope.CHANGED,
            confidentiality=Impact.HIGH,
            integrity=Impact.HIGH,
            availability=Impact.LOW,
        ),
        "affected_csc": "CSC-0001 (RNG Primary)",
        "business_impact": (
            "Complete loss of game integrity. Regulatory license revocation. "
            "Potential liability for fraudulent payouts. Reputational destruction."
        ),
        "existing_controls": [
            "AIS-31 certified hardware entropy source",
            "24-hour signature verification (OGIS-1)",
            "Network segmentation isolating RNG service",
        ],
        "treatment": TreatmentOption.MITIGATE.value,
        "treatment_plan": (
            "1. Implement constant-time RNG algorithms\n"
            "2. Deploy hardware security modules (HSM) for entropy\n"
            "3. Add OGIS-1 signature verification with 24h cycle\n"
            "4. Conduct quarterly RNG statistical testing (NIST SP 800-22)\n"
            "5. Annual third-party RNG audit by accredited lab"
        ),
    },
    {
        "title": "Game Logic Manipulation via Client-Side Execution",
        "category": RiskCategory.GAME_INTEGRITY.value,
        "ogis_domain": "OGIS-3",
        "threat_scenario": (
            "Game client performs win/loss calculations locally and sends "
            "results to server. Attacker reverse-engineers client, modifies "
            "game logic to force winning outcomes."
        ),
        "cvss_vector": CVSSVector(
            attack_vector=AttackVector.NETWORK,
            attack_complexity=AttackComplexity.LOW,
            privileges_required=PrivilegesRequired.LOW,
            user_interaction=UserInteraction.NONE,
            scope=Scope.CHANGED,
            confidentiality=Impact.LOW,
            integrity=Impact.HIGH,
            availability=Impact.LOW,
        ),
        "affected_csc": "CSC-0002 (Game Engine)",
        "business_impact": (
            "Financial losses from fraudulent payouts. OGIS-3 non-compliance. "
            "Regulatory investigation and potential license suspension."
        ),
        "existing_controls": [
            "Server-side game logic execution",
            "Code obfuscation on game clients",
            "State transition validation",
        ],
        "treatment": TreatmentOption.MITIGATE.value,
        "treatment_plan": (
            "1. Validate ALL game logic executes server-side only\n"
            "2. Implement server-side state machine for game flow\n"
            "3. Add input validation and rate limiting on game APIs\n"
            "4. Deploy code obfuscation on client-side code\n"
            "5. Implement tamper detection on game clients"
        ),
    },
    {
        "title": "Payment Gateway Credential Theft and Fraudulent Withdrawals",
        "category": RiskCategory.PAYMENT_FRAUD.value,
        "ogis_domain": "OGIS-2",
        "threat_scenario": (
            "Attacker compromises back-office admin credentials to access "
            "payment gateway and initiate unauthorized withdrawals. "
            "OGIS-2 MFA bypass via session hijacking or social engineering."
        ),
        "cvss_vector": CVSSVector(
            attack_vector=AttackVector.NETWORK,
            attack_complexity=AttackComplexity.LOW,
            privileges_required=PrivilegesRequired.HIGH,
            user_interaction=UserInteraction.NONE,
            scope=Scope.UNCHANGED,
            confidentiality=Impact.HIGH,
            integrity=Impact.HIGH,
            availability=Impact.LOW,
        ),
        "affected_csc": "CSC-0003 (Payment Gateway)",
        "business_impact": (
            "Direct financial loss. Player trust damage. "
            "PCI DSS non-compliance. Regulatory sanctions."
        ),
        "existing_controls": [
            "MFA on all admin accounts",
            "RBAC with segregation of duties",
            "Session recording for back-office access",
            "15-minute idle timeout",
        ],
        "treatment": TreatmentOption.MITIGATE.value,
        "treatment_plan": (
            "1. Enforce hardware MFA tokens for payment admin roles\n"
            "2. Implement dual-approval for withdrawals above threshold\n"
            "3. Deploy behavioral analytics on admin sessions\n"
            "4. Quarterly RBAC review with segregation-of-duties validation\n"
            "5. Automated orphaned account detection"
        ),
    },
    {
        "title": "Player Database Breach via SQL Injection",
        "category": RiskCategory.PLAYER_DATA.value,
        "ogis_domain": "OGIS-3",
        "threat_scenario": (
            "SQL injection vulnerability in player registration or profile "
            "update endpoint allows extraction of player PII, KYC documents, "
            "and financial data."
        ),
        "cvss_vector": CVSSVector(
            attack_vector=AttackVector.NETWORK,
            attack_complexity=AttackComplexity.LOW,
            privileges_required=PrivilegesRequired.NONE,
            user_interaction=UserInteraction.NONE,
            scope=Scope.UNCHANGED,
            confidentiality=Impact.HIGH,
            integrity=Impact.HIGH,
            availability=Impact.NONE,
        ),
        "affected_csc": "CSC-0004 (Player Database)",
        "business_impact": (
            "GDPR/ePrivacy breach notification required. Regulatory fines "
            "(up to 4% of global turnover under GDPR). Mass player churn. "
            "Reputational damage."
        ),
        "existing_controls": [
            "Parameterized queries",
            "WAF with SQL injection rules",
            "Database encryption at rest",
        ],
        "treatment": TreatmentOption.MITIGATE.value,
        "treatment_plan": (
            "1. Code review all database queries for parameterization\n"
            "2. Deploy DAST scanning in CI/CD pipeline\n"
            "3. Implement database activity monitoring\n"
            "4. Encrypt sensitive fields (PII) at application layer\n"
            "5. Quarterly penetration testing per GTS requirements"
        ),
    },
    {
        "title": "DDoS Attack During Peak Betting Event",
        "category": RiskCategory.PLATFORM_AVAILABILITY.value,
        "ogis_domain": "OGIS-5",
        "threat_scenario": (
            "Volumetric DDoS attack targeting the platform during a major "
            "sporting event (e.g., Champions League final). Active bets and "
            "live game sessions disrupted, creating regulatory liability."
        ),
        "cvss_vector": CVSSVector(
            attack_vector=AttackVector.NETWORK,
            attack_complexity=AttackComplexity.LOW,
            privileges_required=PrivilegesRequired.NONE,
            user_interaction=UserInteraction.NONE,
            scope=Scope.UNCHANGED,
            confidentiality=Impact.NONE,
            integrity=Impact.NONE,
            availability=Impact.HIGH,
        ),
        "affected_csc": "CSC-0006 (Sportsbook), CDN Infrastructure",
        "business_impact": (
            "Revenue loss during peak betting period. Active bet disruption "
            "creates regulatory liability. 15-minute outage threshold triggers "
            "GIS incident classification."
        ),
        "existing_controls": [
            "CDN with DDoS scrubbing",
            "Auto-scaling infrastructure",
            "Geographic rate limiting",
        ],
        "treatment": TreatmentOption.MITIGATE.value,
        "treatment_plan": (
            "1. Deploy multi-layer DDoS protection (network + application)\n"
            "2. Implement adaptive rate limiting with ML traffic analysis\n"
            "3. Validate IP obfuscation via CDN and reverse proxy\n"
            "4. Test failover procedures quarterly\n"
            "5. Maintain backup DDoS provider for redundancy"
        ),
    },
    {
        "title": "Automated Bot Abuse on Bonus System",
        "category": RiskCategory.BOT_ABUSE.value,
        "ogis_domain": "OGIS-4",
        "threat_scenario": (
            "Bot network creates thousands of fake accounts to exploit "
            "welcome bonuses and wagering requirements. Coordinated "
            "multi-accounting with automated gameplay."
        ),
        "cvss_vector": CVSSVector(
            attack_vector=AttackVector.NETWORK,
            attack_complexity=AttackComplexity.LOW,
            privileges_required=PrivilegesRequired.NONE,
            user_interaction=UserInteraction.NONE,
            scope=Scope.UNCHANGED,
            confidentiality=Impact.NONE,
            integrity=Impact.HIGH,
            availability=Impact.LOW,
        ),
        "affected_csc": "CSC-0005 (Bonus Engine), CSC-0009 (Mobile Backend)",
        "business_impact": (
            "Financial loss from bonus abuse. OGIS-4 requires 99%+ bot "
            "block rate. Failed bot mitigation is a compliance finding."
        ),
        "existing_controls": [
            "Device fingerprinting",
            "CAPTCHA on registration",
            "Basic rate limiting",
        ],
        "treatment": TreatmentOption.MITIGATE.value,
        "treatment_plan": (
            "1. Deploy bot mitigation platform with JS challenges\n"
            "2. Implement behavioral analysis for gameplay patterns\n"
            "3. Add ML-based detection with known-bot signature database\n"
            "4. Enforce device fingerprinting with multi-account detection\n"
            "5. Rate limit bonus claims per device/IP/identity"
        ),
    },
    {
        "title": "Vendor Game Provider Supply Chain Compromise",
        "category": RiskCategory.VENDOR_RISK.value,
        "ogis_domain": "OGIS-1",
        "threat_scenario": (
            "Third-party game provider's build pipeline is compromised. "
            "Malicious code injected into game binaries deployed to the "
            "platform, bypassing signature verification because the "
            "provider's signing key was used."
        ),
        "cvss_vector": CVSSVector(
            attack_vector=AttackVector.NETWORK,
            attack_complexity=AttackComplexity.HIGH,
            privileges_required=PrivilegesRequired.HIGH,
            user_interaction=UserInteraction.NONE,
            scope=Scope.CHANGED,
            confidentiality=Impact.HIGH,
            integrity=Impact.HIGH,
            availability=Impact.LOW,
        ),
        "affected_csc": "CSC-0002 (Game Engine), Third-party game providers",
        "business_impact": (
            "Game integrity compromised. Player data exposure. "
            "GLI-GSF-3 non-compliance. Regulatory investigation."
        ),
        "existing_controls": [
            "Vendor GTS assessment requirement",
            "SOC 2 / ISO 27001 evidence collection",
            "Vendor access lifecycle management",
        ],
        "treatment": TreatmentOption.MITIGATE.value,
        "treatment_plan": (
            "1. Require vendors to provide SBOM for all game binaries\n"
            "2. Implement independent signature verification (not vendor's key alone)\n"
            "3. Sandbox new game deployments for 24h observation\n"
            "4. Annual vendor GTS assessment or equivalent evidence\n"
            "5. Contractual right-to-audit and incident notification clauses"
        ),
    },
]


# ---------------------------------------------------------------------------
# Interactive Assessment
# ---------------------------------------------------------------------------
def run_interactive_assessment(org: str) -> RiskAssessment:
    """Run an interactive risk assessment session."""
    print("\n" + "=" * 60)
    print("  GLI-GSF Risk Assessment Tool (Interactive Mode)")
    print("  Methodology: CVSS v3.1 + ISO 31010")
    print("  Reference: GLI-GSF-1, Section 2.3.7")
    print("=" * 60 + "\n")

    assessor = input("Assessor name: ").strip() or "GIS Officer"
    scope = input("Assessment scope: ").strip() or "Full GPE"

    assessment = RiskAssessment(
        organization=org,
        assessment_date=datetime.now(timezone.utc).isoformat(),
        assessor=assessor,
        scope=scope,
    )

    risk_num = 1
    while True:
        print(f"\n--- Risk #{risk_num} ---")
        title = input("Risk title (or 'done'): ").strip()
        if title.lower() == "done":
            break

        description = input("Description: ").strip()

        print("\nCategories:")
        for i, cat in enumerate(RiskCategory, 1):
            print(f"  {i}. {cat.value}")
        cat_idx = int(input("Category number: ").strip() or "1") - 1
        category = list(RiskCategory)[min(cat_idx, len(RiskCategory) - 1)]

        ogis = input("OGIS domain (e.g., OGIS-1): ").strip() or "OGIS-1"
        affected_csc = input("Affected CSC ID: ").strip()
        threat_scenario = input("Threat scenario: ").strip()
        business_impact = input("Business impact: ").strip()

        # Simplified CVSS input
        print("\nCVSS v3.1 Base Score (simplified):")
        print("  Attack Vector: (N)etwork, (A)djacent, (L)ocal, (P)hysical")
        av = input("  AV [N]: ").strip().upper() or "N"
        print("  Attack Complexity: (L)ow, (H)igh")
        ac = input("  AC [L]: ").strip().upper() or "L"
        print("  Privileges Required: (N)one, (L)ow, (H)igh")
        pr = input("  PR [N]: ").strip().upper() or "N"
        print("  Confidentiality Impact: (N)one, (L)ow, (H)igh")
        c = input("  C [H]: ").strip().upper() or "H"
        print("  Integrity Impact: (N)one, (L)ow, (H)igh")
        i_val = input("  I [H]: ").strip().upper() or "H"
        print("  Availability Impact: (N)one, (L)ow, (H)igh")
        a = input("  A [L]: ").strip().upper() or "L"

        vector = CVSSVector(
            attack_vector=AttackVector(av),
            attack_complexity=AttackComplexity(ac),
            privileges_required=PrivilegesRequired(pr),
            user_interaction=UserInteraction.NONE,
            scope=Scope.UNCHANGED,
            confidentiality=Impact(c),
            integrity=Impact(i_val),
            availability=Impact(a),
        )

        score = vector.calculate_score()
        severity = classify_severity(score)
        deadline = calculate_deadline(severity)

        print(f"\n  CVSS Score: {score} ({severity.value})")
        print(f"  Remediation deadline: {REMEDIATION_TIMELINES[severity]['label']}")

        treatment = input("\nTreatment (Mitigate/Transfer/Accept/Avoid) [Mitigate]: ").strip() or "Mitigate"
        treatment_plan = input("Treatment plan: ").strip()
        owner = input("Owner: ").strip()

        entry = RiskEntry(
            risk_id=f"RISK-{risk_num:04d}",
            title=title,
            description=description,
            category=category.value,
            affected_csc=affected_csc,
            ogis_domain=ogis,
            threat_scenario=threat_scenario,
            cvss_vector=vector.to_string(),
            cvss_score=score,
            severity=severity.value,
            likelihood="High" if score >= 7.0 else ("Medium" if score >= 4.0 else "Low"),
            business_impact=business_impact,
            existing_controls=[],
            treatment=treatment,
            treatment_plan=treatment_plan,
            remediation_deadline=deadline,
            owner=owner,
            regulatory_notice_required=severity in (RiskSeverity.CRITICAL, RiskSeverity.HIGH),
        )
        assessment.entries.append(entry)
        risk_num += 1

    return assessment


# ---------------------------------------------------------------------------
# Demo Assessment (iGaming threat library)
# ---------------------------------------------------------------------------
def generate_demo_assessment(org: str) -> RiskAssessment:
    """Generate a demo assessment using the iGaming threat library."""
    assessment = RiskAssessment(
        organization=org,
        assessment_date=datetime.now(timezone.utc).isoformat(),
        assessor="GIS Officer (Demo)",
        scope="Full Gaming Production Environment (GPE)",
    )

    for i, threat in enumerate(IGAMING_THREATS, 1):
        vector: CVSSVector = threat["cvss_vector"]  # ty:ignore[invalid-assignment]
        score = vector.calculate_score()
        severity = classify_severity(score)
        deadline = calculate_deadline(severity)

        entry = RiskEntry(
            risk_id=f"RISK-{i:04d}",
            title=threat["title"],  # ty:ignore[invalid-argument-type]
            description=threat["threat_scenario"],  # ty:ignore[invalid-argument-type]
            category=threat["category"],  # ty:ignore[invalid-argument-type]
            affected_csc=threat["affected_csc"],  # ty:ignore[invalid-argument-type]
            ogis_domain=threat["ogis_domain"],  # ty:ignore[invalid-argument-type]
            threat_scenario=threat["threat_scenario"],  # ty:ignore[invalid-argument-type]
            cvss_vector=vector.to_string(),
            cvss_score=score,
            severity=severity.value,
            likelihood="High" if score >= 7.0 else ("Medium" if score >= 4.0 else "Low"),
            business_impact=threat["business_impact"],  # ty:ignore[invalid-argument-type]
            existing_controls=threat["existing_controls"],  # ty:ignore[invalid-argument-type]
            treatment=threat["treatment"],  # ty:ignore[invalid-argument-type]
            treatment_plan=threat["treatment_plan"],  # ty:ignore[invalid-argument-type]
            remediation_deadline=deadline,
            owner="Security Team",
            regulatory_notice_required=severity in (RiskSeverity.CRITICAL, RiskSeverity.HIGH),
        )
        assessment.entries.append(entry)

    return assessment


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
def report_json(assessment: RiskAssessment) -> str:
    """Generate JSON report for ISF evidence package."""
    data = {
        "document_type": "Risk Assessment Report",
        "gsf_reference": assessment.gsf_reference,
        "methodology": assessment.methodology,
        "organization": assessment.organization,
        "assessment_date": assessment.assessment_date,
        "assessor": assessment.assessor,
        "scope": assessment.scope,
        "summary": {
            "total_risks": assessment.total_risks,
            "critical": assessment.critical_count,
            "high": assessment.high_count,
            "by_category": {
                k: len(v) for k, v in assessment.by_category().items()
            },
        },
        "remediation_timelines": {
            k.value: v for k, v in REMEDIATION_TIMELINES.items()
        },
        "risks": [asdict(e) for e in assessment.entries],
    }
    return json.dumps(data, indent=2, default=str)


def report_markdown(assessment: RiskAssessment) -> str:
    """Generate Markdown report."""
    lines = [
        f"# Risk Assessment Report",
        f"",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Organization** | {assessment.organization} |",
        f"| **Date** | {assessment.assessment_date} |",
        f"| **Assessor** | {assessment.assessor} |",
        f"| **Scope** | {assessment.scope} |",
        f"| **Methodology** | {assessment.methodology} |",
        f"| **GLI-GSF Reference** | {assessment.gsf_reference} |",
        f"",
        f"## Executive Summary",
        f"",
        f"| Severity | Count | Remediation Timeline |",
        f"|----------|-------|---------------------|",
    ]

    for severity in [RiskSeverity.CRITICAL, RiskSeverity.HIGH, RiskSeverity.MEDIUM, RiskSeverity.LOW]:
        count = len(assessment.by_severity().get(severity.value, []))
        timeline = REMEDIATION_TIMELINES[severity]["label"]
        lines.append(f"| **{severity.value}** | {count} | {timeline} |")

    lines.extend([
        f"",
        f"**Total Risks Identified: {assessment.total_risks}**",
        f"",
        f"## Risk Register",
        f"",
    ])

    for entry in assessment.entries:
        lines.extend([
            f"### {entry.risk_id}: {entry.title}",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| **Category** | {entry.category} |",
            f"| **OGIS Domain** | {entry.ogis_domain} |",
            f"| **CVSS Score** | {entry.cvss_score} ({entry.severity}) |",
            f"| **CVSS Vector** | `{entry.cvss_vector}` |",
            f"| **Affected CSC** | {entry.affected_csc} |",
            f"| **Likelihood** | {entry.likelihood} |",
            f"| **Treatment** | {entry.treatment} |",
            f"| **Deadline** | {REMEDIATION_TIMELINES.get(RiskSeverity(entry.severity), {}).get('label', 'N/A')} |",
            f"| **Owner** | {entry.owner} |",
            f"| **Regulatory Notice** | {'Required' if entry.regulatory_notice_required else 'Not required'} |",
            f"",
            f"**Threat Scenario:** {entry.threat_scenario}",
            f"",
            f"**Business Impact:** {entry.business_impact}",
            f"",
            f"**Existing Controls:**",
        ])
        for ctrl in entry.existing_controls:
            lines.append(f"- {ctrl}")
        lines.extend([
            f"",
            f"**Treatment Plan:**",
            f"",
            f"{entry.treatment_plan}",
            f"",
            f"---",
            f"",
        ])

    lines.extend([
        f"## Remediation Timeline Reference (GLI-GSF)",
        f"",
        f"| Severity | Remediation | Regulatory Notification | ISF Follow-up |",
        f"|----------|------------|------------------------|---------------|",
        f"| Critical | 24 hours | Immediate | Within 7 days |",
        f"| High | 7 days | Within 30 days | Within 30 days |",
        f"| Medium | 30 days | Within 30 days | Next annual |",
        f"| Low | Quarterly | Annual report | Next annual |",
        f"",
        f"---",
        f"*Generated by risk-assessment.py v{VERSION}*",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="GLI-GSF Risk Assessment Tool (CVSS v3.1 + ISO 31010)",
    )
    parser.add_argument("--interactive", action="store_true", help="Interactive assessment mode")
    parser.add_argument("--demo", action="store_true", help="Generate demo assessment with iGaming threats")
    parser.add_argument("--org", default="AcmetoCasino", help="Organization name")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--format", default="markdown", choices=["json", "markdown"])
    parser.add_argument("--version", action="version", version=f"risk-assessment.py v{VERSION}")

    args = parser.parse_args()

    if args.interactive:
        assessment = run_interactive_assessment(args.org)
    elif args.demo:
        assessment = generate_demo_assessment(args.org)
    else:
        logger.info("No mode specified. Use --interactive or --demo. Running demo.")
        assessment = generate_demo_assessment(args.org)

    # Generate report
    if args.format == "json":
        report = report_json(assessment)
    else:
        report = report_markdown(assessment)

    # Output
    if args.output:
        Path(args.output).write_text(report)
        logger.info(f"Report saved to {args.output}")
    else:
        print(report)

    logger.info(
        f"Assessment complete: {assessment.total_risks} risks "
        f"({assessment.critical_count} Critical, {assessment.high_count} High)"
    )


if __name__ == "__main__":
    main()
