# Companion code for "The Backend of Luck" - Chapter 34b, Data Governance for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
PII Data Map Scanner — iGaming Platform DSR Module
===================================================
Scans database tables and cross-references with Presidio NLP findings to produce
a structured JSON report of all PII fields with classification, retention rules,
and erasability status.

Regulatory context:
  - GDPR Art. 4(1): definition of personal data
  - GDPR Art. 30: records of processing activities (RoPA)
  - GDPR Art. 35: data protection impact assessment (DPIA)
  - LGPD Art. 5(I): definition of personal data
  - MGA Data Protection Directive (2018)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pii_data_map")


class PIICategory(str, Enum):
    DIRECT = "Direct PII"           # GDPR Art. 4(1) — name, email, national ID
    INDIRECT = "Indirect PII"       # GDPR Recital 26 — IP, device fingerprint
    SENSITIVE = "Sensitive PII"     # GDPR Art. 9 — health, religion, sexuality
    BIOMETRIC = "Biometric"         # GDPR Art. 9(1) — face scan, fingerprint
    FINANCIAL = "Financial"         # PCI-DSS, AMLD — card data, bank account
    BEHAVIORAL = "Behavioral"       # GDPR Recital 30 — betting patterns, session logs


class ErasabilityStatus(str, Enum):
    ERASABLE = "ERASABLE"
    PSEUDONYMIZABLE = "PSEUDONYMIZABLE"
    RETAINED = "RETAINED"           # Legal hold — cannot be deleted
    DEFERRED = "DEFERRED"           # Retention window not yet elapsed


@dataclass
class RetentionRule:
    basis: str          # e.g. "AML 5yr", "GDPR Art. 17(3)(b)"
    retention_years: int
    authority: str      # e.g. "FATF", "EBA", "MGA"


@dataclass
class PIIField:
    table: str
    column: str
    category: PIICategory
    erasability: ErasabilityStatus
    presidio_score: float           # 0.0–1.0 confidence from Presidio NLP
    retention_rules: list[RetentionRule]
    legal_basis_to_retain: list[str]
    sample_values_redacted: bool = True
    notes: str = ""


@dataclass
class PIIReport:
    generated_at: str
    database: str
    total_tables_scanned: int
    total_pii_fields: int
    fields: list[PIIField]
    summary_by_category: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Simulated schema catalogue — in production this is fetched via SQLAlchemy
# information_schema or a data-catalogue API (e.g. DataHub, Amundsen).
# ---------------------------------------------------------------------------
SCHEMA_CATALOGUE: list[dict[str, Any]] = [
    {"table": "players", "column": "email", "sample": "user@example.com"},
    {"table": "players", "column": "full_name", "sample": "Jane Doe"},
    {"table": "players", "column": "date_of_birth", "sample": "1988-07-14"},
    {"table": "players", "column": "national_id", "sample": "***-**-****"},
    {"table": "players", "column": "ip_address", "sample": "192.168.1.1"},
    {"table": "players", "column": "device_fingerprint", "sample": "a3f9..."},
    {"table": "kyc_documents", "column": "face_image_hash", "sample": "sha256:..."},
    {"table": "kyc_documents", "column": "liveness_score", "sample": "0.97"},
    {"table": "payment_methods", "column": "card_last4", "sample": "4242"},
    {"table": "payment_methods", "column": "iban", "sample": "GB29NWBK..."},
    {"table": "transactions", "column": "amount_eur", "sample": "150.00"},
    {"table": "transactions", "column": "merchant_ref", "sample": "TXN-9821"},
    {"table": "game_sessions", "column": "session_duration_s", "sample": "3612"},
    {"table": "game_sessions", "column": "bet_pattern_vector", "sample": "[0.1,...]"},
    {"table": "responsible_gaming", "column": "self_exclusion_reason", "sample": "..."},
    {"table": "aml_flags", "column": "flag_reason", "sample": "PEP match"},
]


# ---------------------------------------------------------------------------
# Presidio NLP stub — real integration calls presidio-analyzer REST API.
# POST http://presidio-analyzer:3000/analyze  { text, language, entities }
# ---------------------------------------------------------------------------
PRESIDIO_SCORES: dict[str, float] = {
    "email": 0.99, "full_name": 0.95, "date_of_birth": 0.92,
    "national_id": 0.98, "ip_address": 0.85, "device_fingerprint": 0.70,
    "face_image_hash": 0.88, "liveness_score": 0.80,
    "card_last4": 0.90, "iban": 0.96,
    "amount_eur": 0.60, "merchant_ref": 0.45,
    "session_duration_s": 0.30, "bet_pattern_vector": 0.65,
    "self_exclusion_reason": 0.78, "flag_reason": 0.55,
}

FIELD_CLASSIFICATION: dict[str, tuple[PIICategory, ErasabilityStatus, list[RetentionRule], list[str]]] = {
    "email":                (PIICategory.DIRECT,    ErasabilityStatus.PSEUDONYMIZABLE,
                             [RetentionRule("GDPR Art. 5(1)(e)", 3, "GDPR")], ["GDPR Art. 6(1)(b)"]),
    "full_name":            (PIICategory.DIRECT,    ErasabilityStatus.PSEUDONYMIZABLE,
                             [RetentionRule("GDPR Art. 5(1)(e)", 3, "GDPR")], ["GDPR Art. 6(1)(b)"]),
    "date_of_birth":        (PIICategory.DIRECT,    ErasabilityStatus.RETAINED,
                             [RetentionRule("AML 5yr", 5, "FATF"), RetentionRule("KYC", 5, "MGA")], ["AMLD Art. 40"]),
    "national_id":          (PIICategory.DIRECT,    ErasabilityStatus.RETAINED,
                             [RetentionRule("AML 5yr", 5, "FATF")], ["AMLD Art. 40"]),
    "ip_address":           (PIICategory.INDIRECT,  ErasabilityStatus.ERASABLE,
                             [RetentionRule("Fraud prevention 1yr", 1, "Internal")], ["GDPR Art. 6(1)(f)"]),
    "device_fingerprint":   (PIICategory.INDIRECT,  ErasabilityStatus.ERASABLE,
                             [RetentionRule("Fraud prevention 1yr", 1, "Internal")], ["GDPR Art. 6(1)(f)"]),
    "face_image_hash":      (PIICategory.BIOMETRIC, ErasabilityStatus.RETAINED,
                             [RetentionRule("KYC 5yr", 5, "MGA")], ["GDPR Art. 9(2)(a)"]),
    "liveness_score":       (PIICategory.BIOMETRIC, ErasabilityStatus.ERASABLE,
                             [], []),
    "card_last4":           (PIICategory.FINANCIAL, ErasabilityStatus.RETAINED,
                             [RetentionRule("PCI-DSS 1yr", 1, "PCI SSC")], ["PCI-DSS Req. 3.4"]),
    "iban":                 (PIICategory.FINANCIAL, ErasabilityStatus.RETAINED,
                             [RetentionRule("Tax 7yr", 7, "HMRC/Receita Federal")], ["GDPR Art. 17(3)(b)"]),
    "amount_eur":           (PIICategory.FINANCIAL, ErasabilityStatus.RETAINED,
                             [RetentionRule("Tax 7yr", 7, "HMRC")], ["GDPR Art. 17(3)(b)"]),
    "merchant_ref":         (PIICategory.FINANCIAL, ErasabilityStatus.DEFERRED,
                             [RetentionRule("Tax 7yr", 7, "HMRC")], ["GDPR Art. 17(3)(b)"]),
    "session_duration_s":   (PIICategory.BEHAVIORAL, ErasabilityStatus.ERASABLE,
                             [], []),
    "bet_pattern_vector":   (PIICategory.BEHAVIORAL, ErasabilityStatus.ERASABLE,
                             [RetentionRule("RG monitoring 2yr", 2, "MGA")], ["GDPR Art. 6(1)(c)"]),
    "self_exclusion_reason": (PIICategory.SENSITIVE, ErasabilityStatus.RETAINED,
                              [RetentionRule("RG mandatory 5yr", 5, "MGA")], ["GDPR Art. 9(2)(a)"]),
    "flag_reason":          (PIICategory.SENSITIVE, ErasabilityStatus.RETAINED,
                             [RetentionRule("AML 5yr", 5, "FATF")], ["AMLD Art. 40"]),
}


def classify_field(column: str) -> tuple[PIICategory, ErasabilityStatus, list[RetentionRule], list[str]]:
    """Return classification tuple; defaults to INDIRECT/ERASABLE if unknown."""
    return FIELD_CLASSIFICATION.get(
        column,
        (PIICategory.INDIRECT, ErasabilityStatus.ERASABLE, [], []),
    )


def scan_schema(database: str) -> PIIReport:
    """Scan the schema catalogue and return a PIIReport."""
    logger.info("Starting PII scan on database '%s'", database)
    tables_seen: set[str] = set()
    pii_fields: list[PIIField] = []

    for entry in SCHEMA_CATALOGUE:
        table = entry["table"]
        column = entry["column"]
        tables_seen.add(table)

        presidio_score = PRESIDIO_SCORES.get(column, 0.0)
        # GDPR Art. 4(1) threshold: treat ≥ 0.45 confidence as personal data
        if presidio_score < 0.45:
            logger.debug("Skipping %s.%s (Presidio score %.2f below threshold)", table, column, presidio_score)
            continue

        category, erasability, retention_rules, legal_basis = classify_field(column)
        pii_fields.append(PIIField(
            table=table,
            column=column,
            category=category,
            erasability=erasability,
            presidio_score=presidio_score,
            retention_rules=retention_rules,
            legal_basis_to_retain=legal_basis,
        ))
        logger.info("  [%s] %s.%s  score=%.2f  erasability=%s",
                    category.value, table, column, presidio_score, erasability.value)

    summary: dict[str, int] = {}
    for f in pii_fields:
        summary[f.category.value] = summary.get(f.category.value, 0) + 1

    report = PIIReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        database=database,
        total_tables_scanned=len(tables_seen),
        total_pii_fields=len(pii_fields),
        fields=pii_fields,
        summary_by_category=summary,
    )
    logger.info("Scan complete: %d PII fields across %d tables", len(pii_fields), len(tables_seen))
    return report


def report_to_dict(report: PIIReport) -> dict[str, Any]:
    """Serialize PIIReport to a JSON-compatible dict."""
    d = asdict(report)
    # Convert enum values to strings for serialization
    for f in d["fields"]:
        f["category"] = f["category"]
        f["erasability"] = f["erasability"]
    return d


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan database schema for PII fields and produce a DSR data map.")
    parser.add_argument("--database", default="igaming_prod", help="Logical database name for the report")
    parser.add_argument("--output", default="-", help="Output file path (default: stdout)")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    report = scan_schema(args.database)
    payload = json.dumps(report_to_dict(report), indent=2, default=str)

    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        with open(args.output, "w") as fh:
            fh.write(payload + "\n")
        logger.info("Report written to %s", args.output)
