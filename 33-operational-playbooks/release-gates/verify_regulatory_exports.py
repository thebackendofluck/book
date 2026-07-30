# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Release gate: validate regulatory report format and completeness.

Checks that generated regulatory exports (SAR reports, player activity
reports, financial summaries) meet the required format for each
jurisdiction before submission.

Returns exit code 1 if any validation failure is found.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Required fields per report type and jurisdiction
# ---------------------------------------------------------------------------

_REPORT_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "SAR_REPORT": {
        "required_fields": [
            "report_id", "jurisdiction", "reporting_entity", "subject_name",
            "subject_id", "date_of_birth", "nationality", "account_number",
            "suspicious_activity_description", "transaction_details",
            "reporter_name", "reporter_role", "report_date",
        ],
        "UKGC": [
            "nca_reference", "nominated_officer_name", "nominated_officer_role",
            "consent_requested",
        ],
        "MGA": [
            "fiau_reference", "mlro_name", "mlro_licence_number",
        ],
        "BRAZIL": [
            "coaf_reference", "cpf_number", "responsible_officer",
        ],
    },
    "PLAYER_ACTIVITY_REPORT": {
        "required_fields": [
            "report_id", "jurisdiction", "reporting_period_start",
            "reporting_period_end", "total_active_players",
            "total_deposits", "total_withdrawals", "total_ggr",
            "self_exclusion_count", "complaint_count",
        ],
        "UKGC": [
            "problem_gambling_interactions", "reality_check_count",
            "deposit_limit_changes", "self_exclusion_referrals_gamstop",
        ],
        "SWEDEN": [
            "spelpaus_referrals", "moderate_risk_player_count",
            "high_risk_player_count",
        ],
        "BRAZIL": [
            "sigap_protocol_number", "total_bets_brl",
            "tax_withheld_brl", "excluded_players_count",
        ],
    },
    "FINANCIAL_SUMMARY": {
        "required_fields": [
            "report_id", "jurisdiction", "reporting_period",
            "total_revenue", "total_payouts", "ggr", "ngr",
            "tax_payable", "currency",
        ],
        "UKGC": [
            "player_funds_held", "segregated_account_balance",
            "rtp_percentage",
        ],
        "MGA": [
            "player_protection_fund_contribution",
            "licence_fee_due",
        ],
        "BRAZIL": [
            "ggt_gross_gaming_tax", "outorga_contribution",
            "total_bets_brl", "total_prizes_brl",
        ],
    },
}


# ---------------------------------------------------------------------------
# Sample reports for validation
# ---------------------------------------------------------------------------

_SAMPLE_REPORTS: list[dict[str, Any]] = [
    {
        "report_type": "SAR_REPORT",
        "jurisdiction": "UKGC",
        "data": {
            "report_id": "SAR-2024-00123",
            "jurisdiction": "UKGC",
            "reporting_entity": "AcmeToCasino Ltd",
            "subject_name": "John Smith",
            "subject_id": "player_100",
            "date_of_birth": "1985-03-15",
            "nationality": "GB",
            "account_number": "ACC-100",
            "suspicious_activity_description": "Multiple rapid deposits from different cards",
            "transaction_details": [
                {"txn_id": "TXN-001", "amount": 5000, "date": "2024-01-15"},
            ],
            "reporter_name": "Jane Doe",
            "reporter_role": "AML Analyst",
            "report_date": "2024-01-16",
            "nca_reference": "NCA-REF-001",
            "nominated_officer_name": "Bob MLRO",
            "nominated_officer_role": "MLRO",
            "consent_requested": True,
        },
    },
    {
        "report_type": "PLAYER_ACTIVITY_REPORT",
        "jurisdiction": "BRAZIL",
        "data": {
            "report_id": "PAR-2024-Q1",
            "jurisdiction": "BRAZIL",
            "reporting_period_start": "2024-01-01",
            "reporting_period_end": "2024-03-31",
            "total_active_players": 50000,
            "total_deposits": 25000000.0,
            "total_withdrawals": 20000000.0,
            "total_ggr": 5000000.0,
            "self_exclusion_count": 120,
            "complaint_count": 45,
            # Missing: sigap_protocol_number, total_bets_brl, tax_withheld_brl, excluded_players_count
        },
    },
    {
        "report_type": "FINANCIAL_SUMMARY",
        "jurisdiction": "MGA",
        "data": {
            "report_id": "FIN-2024-01",
            "jurisdiction": "MGA",
            "reporting_period": "2024-01",
            "total_revenue": 8000000.0,
            "total_payouts": 6500000.0,
            "ggr": 1500000.0,
            "ngr": 1200000.0,
            "tax_payable": 52500.0,
            "currency": "EUR",
            "player_protection_fund_contribution": 15000.0,
            "licence_fee_due": 25000.0,
        },
    },
]


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    """
    Validate a single regulatory report against its schema.

    Returns a validation result with pass/fail and details.
    """
    report_type = report.get("report_type", "")
    jurisdiction = report.get("jurisdiction", "")
    data = report.get("data", {})

    schema = _REPORT_SCHEMAS.get(report_type)
    if schema is None:
        return {
            "report_type": report_type,
            "jurisdiction": jurisdiction,
            "valid": False,
            "errors": [f"Unknown report type: {report_type}"],
            "warnings": [],
        }

    errors: list[str] = []
    warnings: list[str] = []

    # Check base required fields
    for field in schema.get("required_fields", []):
        if field not in data or data[field] is None or data[field] == "":
            errors.append(f"Missing required field: {field}")

    # Check jurisdiction-specific fields
    jurisdiction_fields = schema.get(jurisdiction, [])
    for field in jurisdiction_fields:
        if field not in data or data[field] is None or data[field] == "":
            errors.append(f"Missing {jurisdiction}-required field: {field}")

    # Validate report_id format
    report_id = data.get("report_id", "")
    if report_id and not any(report_id.startswith(p) for p in ("SAR-", "PAR-", "FIN-", "RPT-")):
        warnings.append(f"Non-standard report_id format: {report_id}")

    # Validate numeric fields are positive
    numeric_fields = [
        "total_deposits", "total_withdrawals", "total_ggr", "total_revenue",
        "total_payouts", "ggr", "ngr", "tax_payable",
    ]
    for field in numeric_fields:
        val = data.get(field)
        if val is not None and isinstance(val, (int, float)) and val < 0:
            errors.append(f"Negative value for {field}: {val}")

    # Validate dates
    date_fields = [
        "report_date", "reporting_period_start", "reporting_period_end",
        "date_of_birth",
    ]
    for field in date_fields:
        val = data.get(field)
        if val is not None:
            try:
                datetime.fromisoformat(str(val))
            except (ValueError, TypeError):
                # Try simple date format
                try:
                    datetime.strptime(str(val), "%Y-%m-%d")
                except ValueError:
                    warnings.append(f"Non-ISO date format for {field}: {val}")

    return {
        "report_type": report_type,
        "report_id": data.get("report_id", ""),
        "jurisdiction": jurisdiction,
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "fields_checked": len(schema.get("required_fields", [])) + len(jurisdiction_fields),
    }


def validate_all_reports(reports: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Validate all pending regulatory reports."""
    if reports is None:
        reports = _SAMPLE_REPORTS

    results = [validate_report(r) for r in reports]
    passed = [r for r in results if r["valid"]]
    failed = [r for r in results if not r["valid"]]
    all_warnings = [w for r in results for w in r.get("warnings", [])]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_reports": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "warnings": len(all_warnings),
        "results": results,
        "pass": len(failed) == 0,
    }


def main() -> int:
    """Entry point for CI/CD and cron execution."""
    report = validate_all_reports()

    if report["pass"]:
        print(f"OK: All {report['total_reports']} regulatory reports valid.")
        if report["warnings"] > 0:
            print(f"  Warnings: {report['warnings']}")
            for r in report["results"]:
                for w in r.get("warnings", []):
                    print(f"    {r['report_id']}: {w}")
        return 0

    print(f"FAIL: {report['failed']}/{report['total_reports']} reports failed validation\n")
    for r in report["results"]:
        if not r["valid"]:
            print(f"  INVALID: {r.get('report_id', 'unknown')} ({r['report_type']}/{r['jurisdiction']})")
            for err in r["errors"]:
                print(f"    - {err}")

    print(f"\n{json.dumps(report, indent=2, default=str)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
