#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Multi-Jurisdiction Licensing Application Tracker

Tracks licensing applications across multiple gambling jurisdictions with
timeline estimation, document checklists, milestone tracking, and deadline
alerts.

Usage:
    python3 licensing-tracker.py --add uk
    python3 licensing-tracker.py --status
    python3 licensing-tracker.py --update uk --milestone "Personal Management License submitted"
    python3 licensing-tracker.py --export timeline.json
"""

import argparse
import json
import os
import sys
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jurisdiction licensing requirements database
# ---------------------------------------------------------------------------

JURISDICTION_DB: dict[str, dict[str, Any]] = {
    "uk": {
        "name": "United Kingdom",
        "regulator": "UK Gambling Commission",
        "license_types": [
            {"type": "Remote Casino Operating License", "fee": 16142, "currency": "GBP"},
            {"type": "Personal Management License", "fee": 545, "currency": "GBP"},
        ],
        "estimated_timeline_weeks": 16,
        "required_documents": [
            "Certificate of Incorporation",
            "Articles of Association",
            "Business Plan (3-year projections)",
            "AML/CFT Policy Document",
            "Social Responsibility Policy",
            "Responsible Gambling Strategy",
            "Technical Standards Compliance Report",
            "Financial Statements (audited, 2 years)",
            "Key Personnel DBS Checks",
            "Player Protection Fund Evidence",
            "IT Security Assessment (ISO 27001 or equivalent)",
            "Data Protection Impact Assessment",
            "Source of Funds Documentation",
            "Organizational Structure Chart",
            "Gaming Software Testing Certificate (eCOGRA/GLI/BMM)",
        ],
        "milestones": [
            {"name": "Application Submitted", "week": 0},
            {"name": "Completeness Check Passed", "week": 2},
            {"name": "Personal License Applications Filed", "week": 3},
            {"name": "DBS Checks Completed", "week": 6},
            {"name": "Technical Standards Assessment", "week": 8},
            {"name": "Compliance Interview", "week": 10},
            {"name": "Financial Assessment Complete", "week": 12},
            {"name": "License Decision", "week": 16},
        ],
        "renewal_period_years": 1,
        "notes": "UKGC requires a UK-based Designated Safeguarding Officer. "
                 "Personal Management Licenses required for key positions.",
    },
    "malta": {
        "name": "Malta",
        "regulator": "Malta Gaming Authority",
        "license_types": [
            {"type": "B2C Type 1 (Casino)", "fee": 25000, "currency": "EUR"},
            {"type": "B2C Type 2 (Fixed Odds)", "fee": 25000, "currency": "EUR"},
        ],
        "estimated_timeline_weeks": 12,
        "required_documents": [
            "Application Form (Schedule 1-4)",
            "Certificate of Registration in Malta",
            "Memorandum and Articles of Association",
            "Business Plan",
            "System Architecture Document",
            "Game and Betting Rules",
            "Terms and Conditions",
            "AML Procedures",
            "Player Protection Policy",
            "Responsible Gaming Implementation Plan",
            "IT Security Audit Report",
            "Certified Financial Statements",
            "Police Conduct Certificates (all directors/shareholders)",
            "Declaration of Beneficial Ownership",
            "Server Location Confirmation",
        ],
        "milestones": [
            {"name": "Application Submitted", "week": 0},
            {"name": "Preliminary Review Complete", "week": 3},
            {"name": "Fit and Proper Assessment Start", "week": 4},
            {"name": "System Review", "week": 6},
            {"name": "Game Certification", "week": 8},
            {"name": "Compliance Audit", "week": 10},
            {"name": "License Issued", "week": 12},
        ],
        "renewal_period_years": 5,
        "notes": "Requires company registration in Malta. Minimum share capital EUR 100,000. "
                 "Key function holders must be EU residents.",
    },
    "ontario": {
        "name": "Ontario, Canada",
        "regulator": "AGCO / iGaming Ontario",
        "license_types": [
            {"type": "iGaming Operator Registration", "fee": 100000, "currency": "CAD"},
        ],
        "estimated_timeline_weeks": 24,
        "required_documents": [
            "AGCO Registration Application",
            "iGO Operating Agreement Application",
            "Corporate Ownership Structure",
            "Financial Statements (3 years)",
            "Criminal Record Checks (all principals)",
            "Responsible Gambling Plan",
            "AML/ATF Compliance Program",
            "Player Dispute Resolution Process",
            "Technical Architecture Documentation",
            "Ontario Server Requirements Compliance",
            "Know Your Client Procedures",
            "Marketing and Advertising Standards Compliance",
            "Problem Gambling Support Integration Plan",
            "Data Residency Compliance (Ontario)",
        ],
        "milestones": [
            {"name": "Pre-Application Meeting", "week": 0},
            {"name": "Application Submitted", "week": 2},
            {"name": "Background Investigations Start", "week": 4},
            {"name": "Technical Platform Review", "week": 8},
            {"name": "Responsible Gambling Assessment", "week": 12},
            {"name": "AGCO Registration Decision", "week": 16},
            {"name": "iGO Agreement Negotiation", "week": 18},
            {"name": "Soft Launch Approval", "week": 22},
            {"name": "Full Launch Authorization", "week": 24},
        ],
        "renewal_period_years": 2,
        "notes": "Revenue share model with iGaming Ontario. Must use AGCO-approved "
                 "gaming suppliers. Ontario data residency requirements.",
    },
    "new_jersey": {
        "name": "New Jersey, USA",
        "regulator": "NJ Division of Gaming Enforcement",
        "license_types": [
            {"type": "Casino Service Industry Enterprise License", "fee": 400000, "currency": "USD"},
            {"type": "Transactional Waiver", "fee": 20000, "currency": "USD"},
        ],
        "estimated_timeline_weeks": 40,
        "required_documents": [
            "Multi-Jurisdictional Application",
            "Corporate Structure Documentation",
            "SEC Filings (if publicly traded)",
            "Complete Financial Disclosures",
            "FBI Background Checks (all key persons)",
            "Technical Infrastructure Documentation",
            "Geolocation Provider Agreement",
            "Land-based Casino Partnership Agreement",
            "Responsible Gaming Program",
            "Internal Controls Submission (IC)",
            "Platform/Software Testing Reports",
            "Server Location Compliance (NJ or approved)",
            "Player Protection and Self-Exclusion Integration",
            "NJ Self-Exclusion List Integration Plan",
        ],
        "milestones": [
            {"name": "Casino Partner Agreement Signed", "week": 0},
            {"name": "Application Filed with DGE", "week": 2},
            {"name": "Background Investigation Phase 1", "week": 4},
            {"name": "Financial Investigation", "week": 12},
            {"name": "Technical Review Start", "week": 16},
            {"name": "Platform Testing & Certification", "week": 24},
            {"name": "Internal Controls Approval", "week": 30},
            {"name": "Soft Launch (limited)", "week": 36},
            {"name": "Full Authorization", "week": 40},
        ],
        "renewal_period_years": 1,
        "notes": "Requires partnership with a land-based NJ casino licensee. "
                 "Geolocation verification mandatory. State-specific server requirements.",
    },
    "sweden": {
        "name": "Sweden",
        "regulator": "Spelinspektionen",
        "license_types": [
            {"type": "Online Casino License", "fee": 400000, "currency": "SEK"},
        ],
        "estimated_timeline_weeks": 16,
        "required_documents": [
            "License Application Form",
            "Company Registration Certificate",
            "Ownership Structure (UBO declaration)",
            "Business Plan",
            "AML Risk Assessment",
            "Responsible Gambling Policy (Spelpaus integration)",
            "Player Bonus Restriction Compliance",
            "Marketing Compliance Plan (moderate gambling advertising rules)",
            "Technical Platform Documentation",
            "Game Provider Certifications",
            "Financial Projections",
            "Criminal Record Extracts",
            "GDPR Compliance Documentation",
        ],
        "milestones": [
            {"name": "Application Submitted", "week": 0},
            {"name": "Administrative Review", "week": 2},
            {"name": "Ownership Investigation", "week": 4},
            {"name": "Technical Assessment", "week": 8},
            {"name": "AML Review", "week": 10},
            {"name": "Responsible Gambling Verification", "week": 12},
            {"name": "License Decision", "week": 16},
        ],
        "renewal_period_years": 5,
        "notes": "Mandatory Spelpaus (national self-exclusion) integration. "
                 "3-second deposit limit cooldown. No welcome bonuses (only one bonus ever).",
    },
    "brazil": {
        "name": "Brazil",
        "regulator": "Secretaria de Premios e Apostas (SPA)",
        "license_types": [
            {"type": "Online Betting and Gaming License", "fee": 30000000, "currency": "BRL"},
        ],
        "estimated_timeline_weeks": 32,
        "required_documents": [
            "SPA License Application",
            "CNPJ Registration (Brazilian entity)",
            "Corporate Structure and Beneficial Ownership",
            "Financial Capability Proof (minimum capital)",
            "AML/CFT Program (COAF compliance)",
            "Responsible Gambling Policy",
            "Technical Platform Documentation",
            "SIGAP Integration Plan",
            "Data Localization Compliance (LGPD)",
            "Game Provider Certifications",
            "Player Verification (CPF-based KYC)",
            "Payment Method Documentation (PIX integration)",
            "Marketing Compliance Plan",
            "Criminal Background Checks",
        ],
        "milestones": [
            {"name": "Brazilian Entity Established", "week": 0},
            {"name": "Application Filed with SPA", "week": 4},
            {"name": "Document Completeness Review", "week": 8},
            {"name": "Background Investigations", "week": 12},
            {"name": "Technical Platform Assessment", "week": 18},
            {"name": "SIGAP Integration Testing", "week": 22},
            {"name": "AML Compliance Verification", "week": 26},
            {"name": "License Decision", "week": 32},
        ],
        "renewal_period_years": 5,
        "notes": "Requires Brazilian corporate entity. R$30M license fee (approx USD 6M). "
                 "SIGAP mandatory reporting system. PIX payment integration essential.",
    },
}

TRACKER_FILE = "licensing_tracker_data.json"


@dataclass
class LicenseApplication:
    """Tracks a single jurisdiction's license application."""
    jurisdiction: str
    status: str = "NOT_STARTED"  # NOT_STARTED, IN_PROGRESS, SUBMITTED, UNDER_REVIEW, APPROVED, DENIED
    start_date: Optional[str] = None
    estimated_completion: Optional[str] = None
    documents_submitted: list = field(default_factory=list)
    milestones_completed: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    total_cost: float = 0.0
    last_updated: str = ""


def load_tracker(filepath: str) -> dict:
    """Load tracker data from JSON file."""
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return json.load(f)
    return {"applications": {}, "created": datetime.now(timezone.utc).isoformat()}


def save_tracker(data: dict, filepath: str):
    """Save tracker data to JSON file."""
    data["last_modified"] = datetime.now(timezone.utc).isoformat()
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"Tracker saved to {filepath}")


def add_jurisdiction(tracker: dict, jurisdiction: str) -> dict:
    """Add a new jurisdiction to track."""
    if jurisdiction not in JURISDICTION_DB:
        logger.error(f"Unknown jurisdiction: {jurisdiction}")
        logger.info(f"Available: {', '.join(JURISDICTION_DB.keys())}")
        return tracker

    if jurisdiction in tracker["applications"]:
        logger.warning(f"{jurisdiction} already being tracked")
        return tracker

    jur = JURISDICTION_DB[jurisdiction]
    start = datetime.now(timezone.utc)
    est_completion = start + timedelta(weeks=jur["estimated_timeline_weeks"])  # ty:ignore[invalid-argument-type]
    total_cost = sum(lt["fee"] for lt in jur["license_types"])  # ty:ignore[invalid-argument-type, not-iterable]

    app = LicenseApplication(
        jurisdiction=jurisdiction,
        status="NOT_STARTED",
        start_date=start.isoformat(),
        estimated_completion=est_completion.isoformat(),
        total_cost=total_cost,
        last_updated=start.isoformat(),
    )

    tracker["applications"][jurisdiction] = asdict(app)
    logger.info(f"Added {jur['name']} to tracker")
    logger.info(f"  Estimated timeline: {jur['estimated_timeline_weeks']} weeks")
    logger.info(f"  Estimated completion: {est_completion.strftime('%Y-%m-%d')}")
    logger.info(f"  License fees: {jur['license_types'][0]['currency']} {total_cost:,.0f}")  # ty:ignore[invalid-argument-type, non-subscriptable]

    return tracker


def update_milestone(tracker: dict, jurisdiction: str, milestone: str) -> dict:
    """Mark a milestone as completed."""
    if jurisdiction not in tracker["applications"]:
        logger.error(f"{jurisdiction} not in tracker. Add it first with --add")
        return tracker

    app = tracker["applications"][jurisdiction]
    now = datetime.now(timezone.utc).isoformat()

    app["milestones_completed"].append({
        "milestone": milestone,
        "completed_at": now,
    })
    app["status"] = "IN_PROGRESS"
    app["last_updated"] = now

    # Check if all milestones are done
    jur = JURISDICTION_DB[jurisdiction]
    all_milestones = [m["name"] for m in jur["milestones"]]  # ty:ignore[invalid-argument-type, not-iterable]
    completed_names = [m["milestone"] for m in app["milestones_completed"]]

    if jur["milestones"][-1]["name"] in completed_names:  # ty:ignore[invalid-argument-type, non-subscriptable]
        app["status"] = "APPROVED"
        logger.info(f"{jur['name']} license APPROVED!")

    logger.info(f"Milestone recorded: '{milestone}' for {jur['name']}")
    return tracker


def submit_document(tracker: dict, jurisdiction: str, document: str) -> dict:
    """Mark a document as submitted."""
    if jurisdiction not in tracker["applications"]:
        logger.error(f"{jurisdiction} not in tracker")
        return tracker

    app = tracker["applications"][jurisdiction]
    now = datetime.now(timezone.utc).isoformat()

    app["documents_submitted"].append({
        "document": document,
        "submitted_at": now,
    })
    app["last_updated"] = now

    jur = JURISDICTION_DB[jurisdiction]
    total_docs = len(jur["required_documents"])  # ty:ignore[invalid-argument-type]
    submitted = len(app["documents_submitted"])

    logger.info(f"Document submitted: '{document}' ({submitted}/{total_docs})")
    return tracker


def print_status(tracker: dict):
    """Print comprehensive status of all tracked applications."""
    print("\n" + "=" * 80)
    print("  MULTI-JURISDICTION LICENSING TRACKER")
    print("=" * 80)

    if not tracker["applications"]:
        print("\n  No applications being tracked.")
        print(f"  Add one with: python3 licensing-tracker.py --add <jurisdiction>")
        print(f"  Available: {', '.join(JURISDICTION_DB.keys())}")
        print()
        return

    for jur_key, app in tracker["applications"].items():
        jur = JURISDICTION_DB.get(jur_key, {})
        name = jur.get("name", jur_key)
        regulator = jur.get("regulator", "Unknown")

        status_icon = {
            "NOT_STARTED": "[ ]",
            "IN_PROGRESS": "[~]",
            "SUBMITTED": "[>]",
            "UNDER_REVIEW": "[?]",
            "APPROVED": "[+]",
            "DENIED": "[X]",
        }.get(app["status"], "[?]")

        print(f"\n  {status_icon} {name} ({regulator})")
        print(f"  {'=' * 60}")
        print(f"  Status:             {app['status']}")
        print(f"  Started:            {app.get('start_date', 'N/A')[:10]}")
        print(f"  Est. Completion:    {app.get('estimated_completion', 'N/A')[:10]}")

        # Calculate days remaining
        if app.get("estimated_completion"):
            try:
                est = datetime.fromisoformat(app["estimated_completion"])
                remaining = (est - datetime.now(timezone.utc)).days
                if remaining > 0:
                    print(f"  Days Remaining:     {remaining}")
                else:
                    print(f"  Days Overdue:       {abs(remaining)}")
            except (ValueError, TypeError):
                pass

        # License costs
        if jur.get("license_types"):
            currency = jur["license_types"][0]["currency"]  # ty:ignore[invalid-argument-type, non-subscriptable]
            print(f"  Total License Cost: {currency} {app['total_cost']:,.0f}")

        # Document progress
        total_docs = len(jur.get("required_documents", []))
        submitted_docs = len(app.get("documents_submitted", []))
        pct = (submitted_docs / total_docs * 100) if total_docs > 0 else 0
        bar = "#" * int(pct / 5) + "-" * (20 - int(pct / 5))
        print(f"  Documents:          [{bar}] {submitted_docs}/{total_docs} ({pct:.0f}%)")

        # Outstanding documents
        if total_docs > submitted_docs:
            submitted_names = [d["document"] for d in app.get("documents_submitted", [])]
            outstanding = [d for d in jur.get("required_documents", []) if d not in submitted_names]
            print(f"  Outstanding:")
            for doc in outstanding[:5]:
                print(f"    - {doc}")
            if len(outstanding) > 5:
                print(f"    ... and {len(outstanding) - 5} more")

        # Milestone progress
        total_ms = len(jur.get("milestones", []))
        completed_ms = len(app.get("milestones_completed", []))
        print(f"\n  Milestones:         {completed_ms}/{total_ms}")
        for ms in jur.get("milestones", []):
            completed = any(
                m["milestone"] == ms["name"]
                for m in app.get("milestones_completed", [])
            )
            icon = "[+]" if completed else "[ ]"
            print(f"    {icon} Week {ms['week']:>2}: {ms['name']}")

        # Notes
        if jur.get("notes"):
            print(f"\n  Regulatory Notes:")
            print(f"    {jur['notes']}")

    # Timeline overview
    print(f"\n\n  TIMELINE OVERVIEW")
    print(f"  {'-' * 60}")
    for jur_key, app in sorted(
        tracker["applications"].items(),
        key=lambda x: x[1].get("estimated_completion", "9999"),
    ):
        name = JURISDICTION_DB.get(jur_key, {}).get("name", jur_key)
        est = app.get("estimated_completion", "N/A")[:10]
        print(f"  {name:<30} -> {est}  [{app['status']}]")

    print("\n" + "=" * 80)


def print_checklist(jurisdiction: str):
    """Print the full document checklist for a jurisdiction."""
    if jurisdiction not in JURISDICTION_DB:
        logger.error(f"Unknown jurisdiction: {jurisdiction}")
        return

    jur = JURISDICTION_DB[jurisdiction]
    print(f"\n  DOCUMENT CHECKLIST: {jur['name']}")
    print(f"  Regulator: {jur['regulator']}")
    print(f"  {'=' * 50}")
    for i, doc in enumerate(jur["required_documents"], 1):  # ty:ignore[invalid-argument-type]
        print(f"  {i:>2}. [ ] {doc}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Multi-Jurisdiction Licensing Tracker")
    parser.add_argument("--add", type=str, help="Add jurisdiction to track")
    parser.add_argument("--status", action="store_true", help="Show status of all applications")
    parser.add_argument("--update", type=str, help="Update jurisdiction (use with --milestone)")
    parser.add_argument("--milestone", type=str, help="Milestone to mark complete")
    parser.add_argument("--submit-doc", type=str, help="Mark document as submitted (use with --update)")
    parser.add_argument("--checklist", type=str, help="Print document checklist for jurisdiction")
    parser.add_argument("--list-jurisdictions", action="store_true", help="List available jurisdictions")
    parser.add_argument("--data-file", type=str, default=TRACKER_FILE,
                        help="Path to tracker data file")
    parser.add_argument("--export", type=str, help="Export tracker to JSON")

    args = parser.parse_args()
    tracker = load_tracker(args.data_file)

    if args.list_jurisdictions:
        print("\nAvailable Jurisdictions:")
        for key, jur in JURISDICTION_DB.items():
            fees = sum(lt["fee"] for lt in jur["license_types"])  # ty:ignore[invalid-argument-type, not-iterable]
            currency = jur["license_types"][0]["currency"]  # ty:ignore[invalid-argument-type, non-subscriptable]
            print(f"  {key:<15} {jur['name']:<30} {currency} {fees:>12,.0f}  "
                  f"({jur['estimated_timeline_weeks']} weeks)")
        print()
        return

    if args.checklist:
        print_checklist(args.checklist)
        return

    if args.add:
        tracker = add_jurisdiction(tracker, args.add)
        save_tracker(tracker, args.data_file)

    if args.update and args.milestone:
        tracker = update_milestone(tracker, args.update, args.milestone)
        save_tracker(tracker, args.data_file)

    if args.update and args.submit_doc:
        tracker = submit_document(tracker, args.update, args.submit_doc)
        save_tracker(tracker, args.data_file)

    if args.status or (not args.add and not args.update and not args.checklist
                       and not args.list_jurisdictions):
        print_status(tracker)

    if args.export:
        with open(args.export, "w") as f:
            json.dump(tracker, f, indent=2)
        logger.info(f"Exported to {args.export}")


if __name__ == "__main__":
    main()
