#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Cross-Border Data Transfer Compliance Engine
=============================================
Manages Standard Contractual Clauses (SCCs), adequacy decisions,
and transfer impact assessments for iGaming cross-border data flows.

Jurisdictions: UK, Malta, Germany, Ontario.

Usage:
    python cross_border_compliance.py --check-transfer UK MT player_pii
    python cross_border_compliance.py --list-adequacy UK
    python cross_border_compliance.py --generate-tia UK US financial
    python cross_border_compliance.py --demo
"""

import json
import logging
import argparse
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cross-border-compliance")


# ---------------------------------------------------------------------------
# Transfer mechanisms and decisions
# ---------------------------------------------------------------------------
class TransferMechanism(str, Enum):
    ADEQUACY_DECISION = "adequacy_decision"
    SCC = "standard_contractual_clauses"
    BCR = "binding_corporate_rules"
    UK_IDTA = "uk_international_data_transfer_agreement"
    CONSENT = "explicit_consent"
    LEGAL_OBLIGATION = "legal_obligation"
    BLOCKED = "blocked"


class TransferDecision(str, Enum):
    APPROVED = "approved"
    CONDITIONAL = "conditional"
    BLOCKED = "blocked"
    REQUIRES_TIA = "requires_transfer_impact_assessment"


class DataSensitivity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Adequacy decisions by jurisdiction
# ---------------------------------------------------------------------------
# Countries/regions recognized as providing adequate data protection
EU_ADEQUACY_COUNTRIES = [
    "AD",  # Andorra
    "AR",  # Argentina
    "CA",  # Canada (PIPEDA)
    "FO",  # Faroe Islands
    "GG",  # Guernsey
    "IL",  # Israel
    "IM",  # Isle of Man
    "JP",  # Japan
    "JE",  # Jersey
    "NZ",  # New Zealand
    "CH",  # Switzerland
    "UY",  # Uruguay
    "KR",  # South Korea
    "GB",  # UK (post-Brexit adequacy)
    "US",  # US (EU-US Data Privacy Framework -- limited scope)
]

UK_ADEQUACY_COUNTRIES = [
    "AD", "AR", "CA", "FO", "GG", "IL", "IM", "JP", "JE",
    "NZ", "CH", "UY", "KR",
    # EU/EEA countries covered by UK adequacy regulations
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI",
    "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
    "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO",  # EEA
    "US",  # UK Extension to EU-US DPF
]

# Country code to jurisdiction mapping for iGaming
COUNTRY_TO_JURISDICTION = {
    "GB": "UK", "MT": "MT", "DE": "DE", "CA": "ON",
}


# ---------------------------------------------------------------------------
# SCC templates
# ---------------------------------------------------------------------------
@dataclass
class SCCRecord:
    scc_id: str
    source_jurisdiction: str
    target_country: str
    data_types: list[str]
    module: str  # Module 1-4 per EU SCCs
    effective_date: str
    review_date: str
    supplementary_measures: list[str]
    status: str = "active"


SCC_MODULES = {
    "module_1": "Controller to Controller",
    "module_2": "Controller to Processor",
    "module_3": "Processor to Processor",
    "module_4": "Processor to Controller",
}


# ---------------------------------------------------------------------------
# Transfer Impact Assessment
# ---------------------------------------------------------------------------
@dataclass
class TransferImpactAssessment:
    tia_id: str
    source_jurisdiction: str
    target_country: str
    data_types: list[str]
    sensitivity: DataSensitivity
    transfer_mechanism: TransferMechanism
    risk_factors: list[dict]
    supplementary_measures: list[str]
    overall_risk: str  # low / medium / high / unacceptable
    recommendation: TransferDecision
    assessor: str
    assessment_date: str
    review_date: str
    notes: str = ""


# ---------------------------------------------------------------------------
# Compliance engine
# ---------------------------------------------------------------------------
class CrossBorderComplianceEngine:
    """
    Evaluates cross-border data transfers against jurisdiction rules,
    adequacy decisions, and SCC requirements.
    """

    # Sensitivity mapping for iGaming data types
    DATA_SENSITIVITY = {
        "player_pii": DataSensitivity.HIGH,
        "financial": DataSensitivity.CRITICAL,
        "gaming_activity": DataSensitivity.HIGH,
        "kyc": DataSensitivity.CRITICAL,
        "marketing": DataSensitivity.MEDIUM,
        "analytics": DataSensitivity.LOW,
        "system_logs": DataSensitivity.LOW,
    }

    # Jurisdiction-specific transfer rules
    TRANSFER_RULES = {
        "UK": {
            "framework": "UK GDPR + Data Protection Act 2018",
            "adequacy_list": UK_ADEQUACY_COUNTRIES,
            "default_mechanism": TransferMechanism.UK_IDTA,
            "scc_equivalent": TransferMechanism.UK_IDTA,
            "regulator": "Information Commissioner's Office (ICO)",
            "tia_required_for": [
                DataSensitivity.HIGH, DataSensitivity.CRITICAL
            ],
            "blocked_countries": [],  # UK doesn't block specific countries
        },
        "MT": {
            "framework": "GDPR (EU)",
            "adequacy_list": EU_ADEQUACY_COUNTRIES,
            "default_mechanism": TransferMechanism.SCC,
            "scc_equivalent": TransferMechanism.SCC,
            "regulator": "Office of the Information and Data Protection Commissioner",
            "tia_required_for": [
                DataSensitivity.HIGH, DataSensitivity.CRITICAL
            ],
            "blocked_countries": [],
        },
        "DE": {
            "framework": "GDPR (EU) + BDSG (German Federal Data Protection Act)",
            "adequacy_list": EU_ADEQUACY_COUNTRIES,
            "default_mechanism": TransferMechanism.SCC,
            "scc_equivalent": TransferMechanism.SCC,
            "regulator": "BfDI (Bundesbeauftragter fuer den Datenschutz)",
            "tia_required_for": [
                DataSensitivity.MEDIUM, DataSensitivity.HIGH,
                DataSensitivity.CRITICAL,
            ],  # Germany is stricter
            "blocked_countries": [],
        },
        "ON": {
            "framework": "PIPEDA (federal) + Ontario Privacy Act",
            "adequacy_list": [
                # PIPEDA uses "comparable protection" standard
                "GB", "DE", "FR", "MT", "IE", "NL", "AT", "SE", "FI",
                "JP", "KR", "NZ", "CH", "IL",
                "US",  # With appropriate contractual safeguards
            ],
            "default_mechanism": TransferMechanism.SCC,
            "scc_equivalent": TransferMechanism.SCC,
            "regulator": "Office of the Privacy Commissioner of Canada (OPC)",
            "tia_required_for": [DataSensitivity.CRITICAL],
            "blocked_countries": [],
        },
    }

    def __init__(self):
        self.audit_log: list[dict] = []
        self.active_sccs: list[SCCRecord] = []
        self.tia_registry: list[TransferImpactAssessment] = []

    def check_transfer(
        self,
        source_jurisdiction: str,
        target_country: str,
        data_type: str,
    ) -> dict:
        """
        Check whether a cross-border transfer is compliant.

        Returns a decision dict with mechanism, conditions, and actions.
        """
        if source_jurisdiction not in self.TRANSFER_RULES:
            return {
                "decision": TransferDecision.BLOCKED.value,
                "reason": f"Unknown source jurisdiction: {source_jurisdiction}",
            }

        rules = self.TRANSFER_RULES[source_jurisdiction]
        sensitivity = self.DATA_SENSITIVITY.get(data_type, DataSensitivity.HIGH)

        # Same jurisdiction -- always allowed
        jur_countries = {
            "UK": "GB", "MT": "MT", "DE": "DE", "ON": "CA"
        }
        if target_country == jur_countries.get(source_jurisdiction):
            result = {
                "decision": TransferDecision.APPROVED.value,
                "mechanism": "domestic_transfer",
                "reason": "Transfer within same jurisdiction",
                "conditions": [],
            }
            self._audit(source_jurisdiction, target_country, data_type, result)
            return result

        # EU/EEA internal transfers (for MT and DE)
        eu_eea = {
            "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI",
            "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
            "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
            "IS", "LI", "NO",
        }
        if source_jurisdiction in ("MT", "DE") and target_country in eu_eea:
            result = {
                "decision": TransferDecision.APPROVED.value,
                "mechanism": "eu_eea_internal",
                "reason": "Transfer within EU/EEA -- no additional mechanism needed",
                "conditions": ["GDPR obligations still apply at destination"],
            }
            self._audit(source_jurisdiction, target_country, data_type, result)
            return result

        # Blocked countries
        if target_country in rules.get("blocked_countries", []):
            result = {
                "decision": TransferDecision.BLOCKED.value,
                "mechanism": TransferMechanism.BLOCKED.value,
                "reason": f"Transfers to {target_country} are blocked by "
                          f"{source_jurisdiction} regulations",
                "conditions": [],
            }
            self._audit(source_jurisdiction, target_country, data_type, result)
            return result

        # Adequacy decision check
        if target_country in rules["adequacy_list"]:
            conditions = []
            if sensitivity in rules["tia_required_for"]:
                conditions.append(
                    "Transfer Impact Assessment required due to data sensitivity"
                )
            result = {
                "decision": TransferDecision.APPROVED.value
                if not conditions
                else TransferDecision.CONDITIONAL.value,
                "mechanism": TransferMechanism.ADEQUACY_DECISION.value,
                "reason": f"{target_country} has adequacy status under "
                          f"{rules['framework']}",
                "conditions": conditions,
            }
            self._audit(source_jurisdiction, target_country, data_type, result)
            return result

        # No adequacy -- need SCCs or equivalent
        mechanism = rules["default_mechanism"]
        conditions = [
            f"Execute {mechanism.value} before transfer",  # ty:ignore[unresolved-attribute]
            "Conduct Transfer Impact Assessment",
            "Implement supplementary technical measures (encryption in transit/rest)",
            f"Register with {rules['regulator']}",
        ]

        if sensitivity == DataSensitivity.CRITICAL:
            conditions.append(
                "DPO approval required for critical-sensitivity data"
            )
            conditions.append(
                "Document supplementary measures addressing government access risks"
            )

        result = {
            "decision": TransferDecision.REQUIRES_TIA.value,
            "mechanism": mechanism.value,  # ty:ignore[unresolved-attribute]
            "reason": f"No adequacy decision for {target_country}. "
                      f"{mechanism.value} required.",  # ty:ignore[unresolved-attribute]
            "conditions": conditions,
            "supplementary_measures": [
                "End-to-end encryption (AES-256-GCM)",
                "Pseudonymization of personal identifiers",
                "Access controls with jurisdiction-based restrictions",
                "Contractual prohibition on government disclosure without legal basis",
            ],
        }
        self._audit(source_jurisdiction, target_country, data_type, result)
        return result

    def generate_tia(
        self,
        source_jurisdiction: str,
        target_country: str,
        data_types: list[str],
        assessor: str = "Data Protection Officer",
    ) -> TransferImpactAssessment:
        """Generate a Transfer Impact Assessment template."""
        max_sensitivity = DataSensitivity.LOW
        for dt in data_types:
            s = self.DATA_SENSITIVITY.get(dt, DataSensitivity.HIGH)
            if list(DataSensitivity).index(s) > list(DataSensitivity).index(max_sensitivity):
                max_sensitivity = s

        risk_factors = self._assess_risk_factors(target_country)
        supplementary = self._recommend_supplementary_measures(
            max_sensitivity, risk_factors
        )
        overall_risk = self._calculate_overall_risk(risk_factors, max_sensitivity)

        if overall_risk == "unacceptable":
            recommendation = TransferDecision.BLOCKED
        elif overall_risk == "high":
            recommendation = TransferDecision.CONDITIONAL
        else:
            recommendation = TransferDecision.APPROVED

        now = datetime.now(timezone.utc)
        tia = TransferImpactAssessment(
            tia_id=f"TIA-{uuid.uuid4().hex[:8].upper()}",
            source_jurisdiction=source_jurisdiction,
            target_country=target_country,
            data_types=data_types,
            sensitivity=max_sensitivity,
            transfer_mechanism=self.TRANSFER_RULES.get(
                source_jurisdiction, {}
            ).get("default_mechanism", TransferMechanism.SCC),  # ty:ignore[invalid-argument-type]
            risk_factors=risk_factors,
            supplementary_measures=supplementary,
            overall_risk=overall_risk,
            recommendation=recommendation,
            assessor=assessor,
            assessment_date=now.isoformat(),
            review_date=(now + timedelta(days=365)).isoformat(),
        )
        self.tia_registry.append(tia)
        return tia

    def create_scc_record(
        self,
        source_jurisdiction: str,
        target_country: str,
        data_types: list[str],
        module: str = "module_2",
    ) -> SCCRecord:
        """Create an SCC record for tracking."""
        now = datetime.now(timezone.utc)
        scc = SCCRecord(
            scc_id=f"SCC-{uuid.uuid4().hex[:8].upper()}",
            source_jurisdiction=source_jurisdiction,
            target_country=target_country,
            data_types=data_types,
            module=SCC_MODULES.get(module, module),
            effective_date=now.isoformat(),
            review_date=(now + timedelta(days=365)).isoformat(),
            supplementary_measures=[
                "Encryption of data in transit and at rest",
                "Access limited to authorized personnel",
                "Annual review of transfer necessity",
                "Incident notification within 72 hours",
            ],
        )
        self.active_sccs.append(scc)
        return scc

    def _assess_risk_factors(self, target_country: str) -> list[dict]:
        """Assess risk factors for a target country."""
        # Simplified risk assessment -- production would use external data
        high_surveillance = {"US", "CN", "RU", "IN", "BR"}
        moderate_surveillance = {"AU", "NZ", "CA", "JP", "KR", "SG"}

        factors = []

        if target_country in high_surveillance:
            factors.append({
                "factor": "Government surveillance",
                "risk_level": "high",
                "description": f"{target_country} has broad surveillance powers "
                               "that may affect data subject rights",
            })
        elif target_country in moderate_surveillance:
            factors.append({
                "factor": "Government surveillance",
                "risk_level": "medium",
                "description": f"{target_country} has surveillance capabilities "
                               "but with judicial oversight",
            })
        else:
            factors.append({
                "factor": "Government surveillance",
                "risk_level": "low",
                "description": "Limited government surveillance concerns",
            })

        # Rule of law assessment
        strong_rule_of_law = {
            "GB", "DE", "FR", "NL", "SE", "NO", "DK", "FI", "IE",
            "AT", "CH", "JP", "CA", "NZ", "AU",
        }
        if target_country in strong_rule_of_law:
            factors.append({
                "factor": "Rule of law",
                "risk_level": "low",
                "description": "Strong independent judiciary and legal framework",
            })
        else:
            factors.append({
                "factor": "Rule of law",
                "risk_level": "medium",
                "description": "Rule of law assessment required -- consult legal",
            })

        # Data protection legislation
        gdpr_equivalent = {
            "GB", "CH", "JP", "KR", "NZ", "IL", "AR", "UY", "CA",
        }
        if target_country in gdpr_equivalent:
            factors.append({
                "factor": "Data protection legislation",
                "risk_level": "low",
                "description": "Adequate data protection legislation in place",
            })
        else:
            factors.append({
                "factor": "Data protection legislation",
                "risk_level": "high",
                "description": "No GDPR-equivalent legislation identified",
            })

        return factors

    def _recommend_supplementary_measures(
        self,
        sensitivity: DataSensitivity,
        risk_factors: list[dict],
    ) -> list[str]:
        measures = [
            "End-to-end encryption using AES-256-GCM for data in transit",
            "Encryption at rest with jurisdiction-separated key management",
        ]

        high_risks = [f for f in risk_factors if f["risk_level"] == "high"]
        if high_risks:
            measures.extend([
                "Pseudonymization of all personal identifiers before transfer",
                "Contractual clause prohibiting disclosure to government without "
                "data exporter's prior consent",
                "Warrant canary or transparency reporting obligations",
                "Right to suspend transfers if legal framework changes",
            ])

        if sensitivity in (DataSensitivity.HIGH, DataSensitivity.CRITICAL):
            measures.extend([
                "Multi-party computation or secure enclaves for processing",
                "Audit logging of all access to transferred data",
                "Quarterly review of transfer necessity and proportionality",
            ])

        return measures

    def _calculate_overall_risk(
        self,
        risk_factors: list[dict],
        sensitivity: DataSensitivity,
    ) -> str:
        high_count = sum(1 for f in risk_factors if f["risk_level"] == "high")
        medium_count = sum(1 for f in risk_factors if f["risk_level"] == "medium")

        if sensitivity == DataSensitivity.CRITICAL and high_count >= 2:
            return "unacceptable"
        if high_count >= 2:
            return "high"
        if high_count >= 1 or medium_count >= 2:
            return "medium"
        return "low"

    def _audit(
        self,
        source: str,
        target: str,
        data_type: str,
        result: dict,
    ):
        self.audit_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_jurisdiction": source,
            "target_country": target,
            "data_type": data_type,
            "decision": result["decision"],
            "mechanism": result.get("mechanism", "n/a"),
        })


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
def run_demo():
    engine = CrossBorderComplianceEngine()

    transfers = [
        ("UK", "MT", "player_pii"),
        ("UK", "US", "financial"),
        ("MT", "DE", "gaming_activity"),
        ("MT", "US", "kyc"),
        ("DE", "GB", "player_pii"),
        ("DE", "CN", "financial"),
        ("ON", "US", "player_pii"),
        ("ON", "GB", "analytics"),
    ]

    print("=" * 80)
    print("CROSS-BORDER DATA TRANSFER COMPLIANCE CHECK")
    print("=" * 80)

    for source, target, dt in transfers:
        result = engine.check_transfer(source, target, dt)
        print(f"\n--- {source} -> {target} ({dt}) ---")
        print(f"  Decision:   {result['decision']}")
        print(f"  Mechanism:  {result.get('mechanism', 'n/a')}")
        print(f"  Reason:     {result['reason']}")
        if result.get("conditions"):
            print(f"  Conditions:")
            for c in result["conditions"]:
                print(f"    - {c}")

    # Generate a TIA
    print("\n" + "=" * 80)
    print("TRANSFER IMPACT ASSESSMENT (DE -> US)")
    print("=" * 80)
    tia = engine.generate_tia("DE", "US", ["financial", "player_pii"])
    print(json.dumps(asdict(tia), indent=2, default=str))

    # Create SCC
    print("\n" + "=" * 80)
    print("STANDARD CONTRACTUAL CLAUSES RECORD (MT -> US)")
    print("=" * 80)
    scc = engine.create_scc_record("MT", "US", ["analytics", "marketing"])
    print(json.dumps(asdict(scc), indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Cross-Border Data Transfer Compliance Engine"
    )
    parser.add_argument("--demo", action="store_true")
    parser.add_argument(
        "--check-transfer",
        nargs=3,
        metavar=("SOURCE_JUR", "TARGET_COUNTRY", "DATA_TYPE"),
    )
    parser.add_argument(
        "--list-adequacy",
        metavar="JURISDICTION",
        help="List adequacy countries for a jurisdiction",
    )
    parser.add_argument(
        "--generate-tia",
        nargs=3,
        metavar=("SOURCE_JUR", "TARGET_COUNTRY", "DATA_TYPE"),
    )

    args = parser.parse_args()
    engine = CrossBorderComplianceEngine()

    if args.demo:
        run_demo()
    elif args.check_transfer:
        result = engine.check_transfer(*args.check_transfer)
        print(json.dumps(result, indent=2))
    elif args.list_adequacy:
        jur = args.list_adequacy.upper()
        rules = engine.TRANSFER_RULES.get(jur)
        if rules:
            print(f"Adequacy countries for {jur} ({rules['framework']}):")
            for c in sorted(rules["adequacy_list"]):
                print(f"  {c}")
        else:
            print(f"Unknown jurisdiction: {jur}")
    elif args.generate_tia:
        src, tgt, dt = args.generate_tia
        tia = engine.generate_tia(src.upper(), tgt.upper(), [dt])
        print(json.dumps(asdict(tia), indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
