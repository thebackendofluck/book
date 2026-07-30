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
Data Classification Engine for iGaming Platforms
=================================================
Classifies data into tiers (public, internal, confidential, restricted)
based on jurisdiction-specific rules and data type analysis.

Covers: UK (UKGC), Malta (MGA), Germany (GluStV), Ontario (AGCO/iGO).

Usage:
    python data_classifier.py --scan-database --jurisdiction UK
    python data_classifier.py --classify-table players --jurisdiction MT
    python data_classifier.py --generate-report --output classification_report.json
"""

import json
import hashlib
import logging
import argparse
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("data-classifier")


# ---------------------------------------------------------------------------
# Classification levels
# ---------------------------------------------------------------------------
class Classification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class DataCategory(str, Enum):
    PLAYER_PII = "player_pii"
    FINANCIAL = "financial"
    GAMING_ACTIVITY = "gaming_activity"
    KYC_DOCUMENTS = "kyc_documents"
    MARKETING = "marketing"
    OPERATIONAL = "operational"
    SYSTEM_LOGS = "system_logs"
    AGGREGATED_ANALYTICS = "aggregated_analytics"


# ---------------------------------------------------------------------------
# Jurisdiction rules
# ---------------------------------------------------------------------------
@dataclass
class JurisdictionRule:
    jurisdiction: str
    display_name: str
    regulator: str
    retention_years: dict  # category -> years
    residency_regions: list
    cross_border_allowed: bool
    encryption_required: bool
    classification_overrides: dict = field(default_factory=dict)
    notes: str = ""


JURISDICTION_RULES: dict[str, JurisdictionRule] = {
    "UK": JurisdictionRule(
        jurisdiction="UK",
        display_name="United Kingdom",
        regulator="UK Gambling Commission (UKGC)",
        retention_years={
            DataCategory.PLAYER_PII: 5,
            DataCategory.FINANCIAL: 7,
            DataCategory.GAMING_ACTIVITY: 5,
            DataCategory.KYC_DOCUMENTS: 5,
            DataCategory.MARKETING: 3,
            DataCategory.OPERATIONAL: 3,
            DataCategory.SYSTEM_LOGS: 2,
            DataCategory.AGGREGATED_ANALYTICS: 0,  # no retention requirement
        },
        residency_regions=["eu-west-2", "uk-dc-1", "uk-dc-2"],
        cross_border_allowed=True,  # with UK adequacy / UK-IDTA
        encryption_required=True,
        classification_overrides={
            # UKGC requires all player-linked gambling data as restricted
            DataCategory.GAMING_ACTIVITY: Classification.RESTRICTED,
        },
        notes="Post-Brexit: UK GDPR + Data Protection Act 2018. "
              "International Data Transfer Agreement (IDTA) for transfers.",
    ),
    "MT": JurisdictionRule(
        jurisdiction="MT",
        display_name="Malta",
        regulator="Malta Gaming Authority (MGA)",
        retention_years={
            DataCategory.PLAYER_PII: 5,
            DataCategory.FINANCIAL: 10,
            DataCategory.GAMING_ACTIVITY: 5,
            DataCategory.KYC_DOCUMENTS: 5,
            DataCategory.MARKETING: 3,
            DataCategory.OPERATIONAL: 5,
            DataCategory.SYSTEM_LOGS: 2,
            DataCategory.AGGREGATED_ANALYTICS: 0,
        },
        residency_regions=[
            "eu-central-1", "eu-west-1", "eu-south-1", "eu-north-1"
        ],
        cross_border_allowed=False,  # EU/EEA only without SCCs
        encryption_required=True,
        classification_overrides={
            DataCategory.FINANCIAL: Classification.RESTRICTED,
        },
        notes="GDPR applies. MGA requires 10-year financial retention. "
              "All player funds data classified as restricted.",
    ),
    "DE": JurisdictionRule(
        jurisdiction="DE",
        display_name="Germany",
        regulator="Gemeinsame Gluecksspielbehorde der Laender (GGL)",
        retention_years={
            DataCategory.PLAYER_PII: 10,
            DataCategory.FINANCIAL: 10,
            DataCategory.GAMING_ACTIVITY: 10,
            DataCategory.KYC_DOCUMENTS: 10,
            DataCategory.MARKETING: 2,
            DataCategory.OPERATIONAL: 5,
            DataCategory.SYSTEM_LOGS: 3,
            DataCategory.AGGREGATED_ANALYTICS: 0,
        },
        residency_regions=["eu-central-1", "de-dc-1", "de-dc-2"],
        cross_border_allowed=False,  # Strict: primary must be in EU
        encryption_required=True,
        classification_overrides={
            # GluStV mandates all player data as restricted
            DataCategory.PLAYER_PII: Classification.RESTRICTED,
            DataCategory.GAMING_ACTIVITY: Classification.RESTRICTED,
            DataCategory.KYC_DOCUMENTS: Classification.RESTRICTED,
        },
        notes="GluStV (Gluecksspielstaatsvertrag) requires strict data "
              "separation per player. Monthly deposit limit of EUR 1,000 "
              "tracked centrally -- OASIS system integration required.",
    ),
    "ON": JurisdictionRule(
        jurisdiction="ON",
        display_name="Ontario, Canada",
        regulator="Alcohol and Gaming Commission of Ontario (AGCO) / iGO",
        retention_years={
            DataCategory.PLAYER_PII: 7,
            DataCategory.FINANCIAL: 7,
            DataCategory.GAMING_ACTIVITY: 7,
            DataCategory.KYC_DOCUMENTS: 7,
            DataCategory.MARKETING: 3,
            DataCategory.OPERATIONAL: 5,
            DataCategory.SYSTEM_LOGS: 3,
            DataCategory.AGGREGATED_ANALYTICS: 0,
        },
        residency_regions=["ca-central-1", "ca-dc-toronto", "ca-dc-montreal"],
        cross_border_allowed=True,  # PIPEDA allows with safeguards
        encryption_required=True,
        classification_overrides={},
        notes="PIPEDA (federal) + Ontario privacy rules. AGCO Standards "
              "require player data to be accessible from Canadian soil.",
    ),
}


# ---------------------------------------------------------------------------
# Default classification matrix
# ---------------------------------------------------------------------------
DEFAULT_CLASSIFICATION: dict[DataCategory, Classification] = {
    DataCategory.PLAYER_PII: Classification.CONFIDENTIAL,
    DataCategory.FINANCIAL: Classification.RESTRICTED,
    DataCategory.GAMING_ACTIVITY: Classification.CONFIDENTIAL,
    DataCategory.KYC_DOCUMENTS: Classification.RESTRICTED,
    DataCategory.MARKETING: Classification.INTERNAL,
    DataCategory.OPERATIONAL: Classification.INTERNAL,
    DataCategory.SYSTEM_LOGS: Classification.INTERNAL,
    DataCategory.AGGREGATED_ANALYTICS: Classification.PUBLIC,
}


# ---------------------------------------------------------------------------
# PII detection patterns
# ---------------------------------------------------------------------------
PII_PATTERNS: dict[str, list[str]] = {
    "email": [r"email", r"e_mail", r"email_address", r"player_email"],
    "phone": [r"phone", r"mobile", r"telephone", r"cell_number"],
    "name": [r"first_name", r"last_name", r"full_name", r"player_name"],
    "address": [r"address", r"street", r"city", r"postal", r"zip_code"],
    "dob": [r"date_of_birth", r"dob", r"birth_date", r"birthday"],
    "national_id": [
        r"ssn", r"social_security", r"national_id", r"passport",
        r"id_number", r"tax_id", r"nin",
    ],
    "ip_address": [r"ip_addr", r"ip_address", r"client_ip", r"source_ip"],
    "financial": [
        r"card_number", r"iban", r"account_number", r"sort_code",
        r"routing_number", r"wallet_address", r"bank_account",
    ],
}

FINANCIAL_PATTERNS: list[str] = [
    r"balance", r"deposit", r"withdrawal", r"transaction",
    r"payment", r"payout", r"wager", r"stake", r"win_amount",
    r"bonus_amount", r"revenue", r"ggr", r"ngr",
]

GAMING_PATTERNS: list[str] = [
    r"bet_id", r"game_id", r"round_id", r"spin", r"hand",
    r"game_result", r"rng_seed", r"game_session", r"bet_history",
    r"play_duration", r"responsible_gambling",
]


# ---------------------------------------------------------------------------
# Data classification result
# ---------------------------------------------------------------------------
@dataclass
class ClassificationResult:
    table_name: str
    column_name: str
    detected_category: DataCategory
    classification: Classification
    jurisdiction: str
    retention_years: int
    encryption_required: bool
    residency_regions: list
    pii_type: Optional[str] = None
    confidence: float = 1.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Classifier engine
# ---------------------------------------------------------------------------
class DataClassifier:
    """
    Classifies database columns/fields by matching column names against
    known PII, financial, and gaming patterns, then applies jurisdiction-
    specific overrides.
    """

    def __init__(self, jurisdiction: str):
        if jurisdiction not in JURISDICTION_RULES:
            raise ValueError(
                f"Unknown jurisdiction '{jurisdiction}'. "
                f"Supported: {list(JURISDICTION_RULES.keys())}"
            )
        self.jurisdiction = jurisdiction
        self.rules = JURISDICTION_RULES[jurisdiction]
        self._build_patterns()

    def _build_patterns(self):
        """Compile regex patterns for efficient matching."""
        self._pii_compiled: dict[str, re.Pattern] = {}
        for pii_type, patterns in PII_PATTERNS.items():
            combined = "|".join(patterns)
            self._pii_compiled[pii_type] = re.compile(combined, re.IGNORECASE)

        self._financial_compiled = re.compile(
            "|".join(FINANCIAL_PATTERNS), re.IGNORECASE
        )
        self._gaming_compiled = re.compile(
            "|".join(GAMING_PATTERNS), re.IGNORECASE
        )

    def classify_column(
        self, table_name: str, column_name: str
    ) -> ClassificationResult:
        """Classify a single database column."""
        category, pii_type, confidence = self._detect_category(column_name)
        classification = self._get_classification(category)
        retention = self.rules.retention_years.get(category, 0)

        return ClassificationResult(
            table_name=table_name,
            column_name=column_name,
            detected_category=category,
            classification=classification,
            jurisdiction=self.jurisdiction,
            retention_years=retention,
            encryption_required=self.rules.encryption_required,
            residency_regions=self.rules.residency_regions,
            pii_type=pii_type,
            confidence=confidence,
        )

    def classify_table(
        self, table_name: str, columns: list[str]
    ) -> list[ClassificationResult]:
        """Classify all columns in a table."""
        results = []
        for col in columns:
            result = self.classify_column(table_name, col)
            results.append(result)
        return results

    def classify_schema(
        self, schema: dict[str, list[str]]
    ) -> dict[str, list[ClassificationResult]]:
        """
        Classify an entire database schema.

        Args:
            schema: dict mapping table_name -> list of column names
        """
        all_results: dict[str, list[ClassificationResult]] = {}
        for table_name, columns in schema.items():
            all_results[table_name] = self.classify_table(table_name, columns)
            table_max = max(
                r.classification.value for r in all_results[table_name]
            )
            logger.info(
                "Table %-30s: %d columns classified (highest: %s)",
                table_name,
                len(columns),
                table_max,
            )
        return all_results

    def _detect_category(
        self, column_name: str
    ) -> tuple[DataCategory, Optional[str], float]:
        """Detect data category from column name pattern matching."""
        # Check PII patterns first (highest priority)
        for pii_type, pattern in self._pii_compiled.items():
            if pattern.search(column_name):
                if pii_type == "financial":
                    return DataCategory.FINANCIAL, pii_type, 0.95
                return DataCategory.PLAYER_PII, pii_type, 0.9

        # Check financial patterns
        if self._financial_compiled.search(column_name):
            return DataCategory.FINANCIAL, None, 0.85

        # Check gaming patterns
        if self._gaming_compiled.search(column_name):
            return DataCategory.GAMING_ACTIVITY, None, 0.85

        # Check KYC-related
        if re.search(r"kyc|verification|document|selfie|proof", column_name, re.I):
            return DataCategory.KYC_DOCUMENTS, None, 0.8

        # Check marketing
        if re.search(
            r"campaign|promo|newsletter|consent|opt_in|marketing", column_name, re.I
        ):
            return DataCategory.MARKETING, None, 0.75

        # System/operational columns
        if re.search(
            r"created_at|updated_at|version|status|is_active|log_level",
            column_name,
            re.I,
        ):
            return DataCategory.OPERATIONAL, None, 0.7

        # Default to internal for unrecognized columns
        return DataCategory.OPERATIONAL, None, 0.5

    def _get_classification(self, category: DataCategory) -> Classification:
        """Get classification level, applying jurisdiction overrides."""
        # Check jurisdiction-specific overrides first
        if category in self.rules.classification_overrides:
            return self.rules.classification_overrides[category]
        return DEFAULT_CLASSIFICATION.get(category, Classification.INTERNAL)


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------
class ClassificationReport:
    """Generates compliance reports from classification results."""

    def __init__(self, jurisdiction: str):
        self.jurisdiction = jurisdiction
        self.rules = JURISDICTION_RULES[jurisdiction]

    def generate_summary(
        self, results: dict[str, list[ClassificationResult]]
    ) -> dict:
        """Generate a summary report suitable for compliance review."""
        total_columns = 0
        by_classification: dict[str, int] = {}
        by_category: dict[str, int] = {}
        restricted_columns: list[dict] = []
        low_confidence: list[dict] = []

        for table_name, table_results in results.items():
            for r in table_results:
                total_columns += 1
                cls_key = r.classification.value
                by_classification[cls_key] = by_classification.get(cls_key, 0) + 1

                cat_key = r.detected_category.value
                by_category[cat_key] = by_category.get(cat_key, 0) + 1

                if r.classification == Classification.RESTRICTED:
                    restricted_columns.append(
                        {
                            "table": r.table_name,
                            "column": r.column_name,
                            "category": r.detected_category.value,
                            "retention_years": r.retention_years,
                        }
                    )

                if r.confidence < 0.7:
                    low_confidence.append(
                        {
                            "table": r.table_name,
                            "column": r.column_name,
                            "detected_as": r.detected_category.value,
                            "confidence": r.confidence,
                        }
                    )

        report = {
            "report_metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "jurisdiction": self.jurisdiction,
                "regulator": self.rules.regulator,
                "total_columns_scanned": total_columns,
            },
            "classification_summary": by_classification,
            "category_summary": by_category,
            "restricted_columns": restricted_columns,
            "low_confidence_classifications": low_confidence,
            "jurisdiction_rules": {
                "residency_regions": self.rules.residency_regions,
                "cross_border_allowed": self.rules.cross_border_allowed,
                "encryption_required": self.rules.encryption_required,
                "retention_years": {
                    k.value: v for k, v in self.rules.retention_years.items()
                },
            },
            "recommendations": self._generate_recommendations(
                restricted_columns, low_confidence
            ),
        }
        return report

    def _generate_recommendations(
        self,
        restricted: list[dict],
        low_confidence: list[dict],
    ) -> list[str]:
        recommendations = []
        if restricted:
            recommendations.append(
                f"{len(restricted)} columns classified as RESTRICTED. "
                "Ensure column-level encryption and access controls are applied."
            )
        if low_confidence:
            recommendations.append(
                f"{len(low_confidence)} columns have low classification confidence. "
                "Manual review recommended before going to production."
            )
        if self.rules.jurisdiction == "DE":
            recommendations.append(
                "Germany (GluStV): Verify OASIS integration for cross-operator "
                "deposit limit enforcement. All player data must be strictly "
                "segregated per-player."
            )
        if self.rules.jurisdiction == "ON":
            recommendations.append(
                "Ontario (AGCO): Ensure PlaySmart integration for responsible "
                "gambling data. Verify Canadian data accessibility requirements."
            )
        if not self.rules.cross_border_allowed:
            recommendations.append(
                f"Cross-border transfers are NOT allowed for {self.jurisdiction} "
                "without Standard Contractual Clauses or adequacy decisions. "
                "Verify all data processors are within approved regions."
            )
        return recommendations


# ---------------------------------------------------------------------------
# Example iGaming schema for demonstration
# ---------------------------------------------------------------------------
EXAMPLE_IGAMING_SCHEMA: dict[str, list[str]] = {
    "players": [
        "player_id", "email", "first_name", "last_name", "date_of_birth",
        "phone", "address", "city", "country", "postal_code",
        "registration_date", "is_active", "ip_address", "status",
    ],
    "player_wallets": [
        "wallet_id", "player_id", "balance", "currency", "last_deposit",
        "last_withdrawal", "bonus_amount", "wagering_requirement",
        "created_at", "updated_at",
    ],
    "transactions": [
        "transaction_id", "player_id", "transaction_type", "amount",
        "currency", "payment_method", "card_number", "iban",
        "status", "created_at", "completed_at",
    ],
    "game_sessions": [
        "session_id", "player_id", "game_id", "bet_id", "stake",
        "win_amount", "game_result", "rng_seed", "play_duration",
        "started_at", "ended_at",
    ],
    "kyc_documents": [
        "document_id", "player_id", "document_type", "national_id",
        "passport", "selfie_url", "verification_status", "verified_by",
        "uploaded_at", "expires_at",
    ],
    "responsible_gambling": [
        "player_id", "deposit_limit_daily", "deposit_limit_weekly",
        "deposit_limit_monthly", "self_exclusion_end", "reality_check_interval",
        "play_duration_limit", "loss_limit", "updated_at",
    ],
    "marketing_campaigns": [
        "campaign_id", "campaign_name", "player_id", "opt_in",
        "consent_date", "newsletter_subscribed", "promo_code",
        "created_at",
    ],
    "audit_logs": [
        "log_id", "event_type", "player_id", "ip_address",
        "action", "details", "log_level", "created_at",
    ],
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="iGaming Data Classification Engine"
    )
    parser.add_argument(
        "--jurisdiction", "-j",
        choices=list(JURISDICTION_RULES.keys()),
        default="UK",
        help="Target jurisdiction (default: UK)",
    )
    parser.add_argument(
        "--scan-database",
        action="store_true",
        help="Scan example schema (demo mode)",
    )
    parser.add_argument(
        "--classify-table", "-t",
        help="Classify a specific table from example schema",
    )
    parser.add_argument(
        "--generate-report",
        action="store_true",
        help="Generate full classification report",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--all-jurisdictions",
        action="store_true",
        help="Run classification across all supported jurisdictions",
    )

    args = parser.parse_args()

    if args.all_jurisdictions:
        all_reports = {}
        for jur in JURISDICTION_RULES:
            classifier = DataClassifier(jur)
            results = classifier.classify_schema(EXAMPLE_IGAMING_SCHEMA)
            reporter = ClassificationReport(jur)
            all_reports[jur] = reporter.generate_summary(results)
        output = json.dumps(all_reports, indent=2, default=str)
    elif args.scan_database:
        classifier = DataClassifier(args.jurisdiction)
        results = classifier.classify_schema(EXAMPLE_IGAMING_SCHEMA)
        if args.generate_report:
            reporter = ClassificationReport(args.jurisdiction)
            report = reporter.generate_summary(results)
            output = json.dumps(report, indent=2, default=str)
        else:
            flat = []
            for table_results in results.values():
                for r in table_results:
                    flat.append(asdict(r))
            output = json.dumps(flat, indent=2, default=str)
    elif args.classify_table:
        if args.classify_table not in EXAMPLE_IGAMING_SCHEMA:
            logger.error(
                "Table '%s' not in example schema. Available: %s",
                args.classify_table,
                list(EXAMPLE_IGAMING_SCHEMA.keys()),
            )
            return
        classifier = DataClassifier(args.jurisdiction)
        results = classifier.classify_table(
            args.classify_table,
            EXAMPLE_IGAMING_SCHEMA[args.classify_table],
        )
        output = json.dumps([asdict(r) for r in results], indent=2, default=str)
    else:
        parser.print_help()
        return

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        logger.info("Report written to %s", args.output)
    else:
        print(output)


if __name__ == "__main__":
    main()
