#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 06, Licensing Guide.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Testing Lab Coordination and Scheduling Tool
===============================================

Manages coordination with iGaming testing laboratories for game certification,
RNG testing, platform audits, and regulatory compliance testing.

Supported labs: GLI, BMM Testlabs, eCOGRA, iTech Labs, NMi, QUINEL, SIQ, SBC Labs.

Usage:
    python lab_coordinator.py --labs
    python lab_coordinator.py --schedule --lab GLI --test-type rng --jurisdiction MGA
    python lab_coordinator.py --status
    python lab_coordinator.py --estimate --lab BMM --games 50
"""

import json
import logging
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class TestType(Enum):
    RNG_CERTIFICATION = "rng_certification"
    GAME_MATHEMATICS = "game_mathematics"
    PLATFORM_AUDIT = "platform_audit"
    INFORMATION_SECURITY = "information_security"
    PENETRATION_TEST = "penetration_test"
    RESPONSIBLE_GAMING = "responsible_gaming"
    LIVE_DEALER_ASSESSMENT = "live_dealer_assessment"
    SPORTS_BETTING_ASSESSMENT = "sports_betting_assessment"
    GEOLOCATION_TESTING = "geolocation_testing"
    PROGRESSIVE_JACKPOT = "progressive_jackpot"
    CHANGE_MANAGEMENT = "change_management"


class TestStatus(Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    TESTING = "testing"
    REMEDIATION = "remediation"  # issues found, fixes needed
    RETESTING = "retesting"
    PASSED = "passed"
    FAILED = "failed"
    CERTIFICATE_ISSUED = "certificate_issued"


@dataclass
class TestingLab:
    """A certified testing laboratory profile."""
    code: str
    name: str
    full_name: str
    headquarters: str
    website: str
    jurisdictions_supported: list = field(default_factory=list)
    test_types: list = field(default_factory=list)
    typical_turnaround_weeks: dict = field(default_factory=dict)  # test_type -> weeks
    base_cost_usd: dict = field(default_factory=dict)            # test_type -> cost
    per_game_cost_usd: float = 0      # additional per-game cost
    offices: list = field(default_factory=list)
    contact_email: str = ""
    notes: str = ""


@dataclass
class TestEngagement:
    """A testing engagement with a lab."""
    id: str
    lab_code: str
    test_type: TestType
    jurisdiction: str
    entity_name: str

    # Scope
    game_count: int = 0
    game_types: list = field(default_factory=list)  # slots, table_games, live, etc.
    platform_components: list = field(default_factory=list)

    # Timeline
    submission_date: Optional[str] = None
    estimated_completion: Optional[str] = None
    actual_completion: Optional[str] = None

    # Costs
    quoted_cost_usd: float = 0
    actual_cost_usd: float = 0

    # Status
    status: TestStatus = TestStatus.DRAFT
    findings: list = field(default_factory=list)    # issues found during testing
    remediation_items: list = field(default_factory=list)
    certificate_number: Optional[str] = None
    certificate_expiry: Optional[str] = None

    # Documents
    test_plan_submitted: bool = False
    source_code_provided: bool = False
    math_docs_provided: bool = False
    api_access_provided: bool = False

    notes: str = ""


# ---------------------------------------------------------------------------
# Lab database
# ---------------------------------------------------------------------------

TESTING_LABS = [
    TestingLab(
        code="GLI",
        name="GLI",
        full_name="Gaming Laboratories International",
        headquarters="Lakewood, NJ, USA",
        website="https://gaminglabs.com",
        jurisdictions_supported=[
            "MGA", "UKGC", "Gibraltar", "IOM", "Alderney", "Sweden", "Denmark",
            "Italy", "Spain", "Portugal", "US (all states)", "Canada (all provinces)",
            "Australia", "New Zealand", "Brazil", "Colombia", "Argentina",
            "South Africa", "Philippines", "Macau", "Singapore",
        ],
        test_types=[TestType.RNG_CERTIFICATION, TestType.GAME_MATHEMATICS,
                     TestType.PLATFORM_AUDIT, TestType.INFORMATION_SECURITY,
                     TestType.SPORTS_BETTING_ASSESSMENT, TestType.GEOLOCATION_TESTING,
                     TestType.PROGRESSIVE_JACKPOT, TestType.LIVE_DEALER_ASSESSMENT],
        typical_turnaround_weeks={
            TestType.RNG_CERTIFICATION.value: 6,
            TestType.GAME_MATHEMATICS.value: 4,
            TestType.PLATFORM_AUDIT.value: 8,
            TestType.INFORMATION_SECURITY.value: 4,
            TestType.SPORTS_BETTING_ASSESSMENT.value: 8,
            TestType.GEOLOCATION_TESTING.value: 3,
        },
        base_cost_usd={
            TestType.RNG_CERTIFICATION.value: 15000,
            TestType.GAME_MATHEMATICS.value: 5000,
            TestType.PLATFORM_AUDIT.value: 40000,
            TestType.INFORMATION_SECURITY.value: 20000,
            TestType.SPORTS_BETTING_ASSESSMENT.value: 35000,
        },
        per_game_cost_usd=2500,
        offices=["USA", "UK", "Netherlands", "Australia", "Macau", "South Africa"],
        contact_email="info@gaminglabs.com",
        notes="Largest testing lab globally. Accepted by virtually all jurisdictions.",
    ),
    TestingLab(
        code="BMM",
        name="BMM Testlabs",
        full_name="BMM Testlabs International",
        headquarters="Las Vegas, NV, USA",
        website="https://bmm.com",
        jurisdictions_supported=[
            "MGA", "UKGC", "Gibraltar", "US (most states)", "Canada",
            "Australia", "Macau", "Philippines", "Argentina", "Brazil",
            "Colombia", "Peru", "South Africa",
        ],
        test_types=[TestType.RNG_CERTIFICATION, TestType.GAME_MATHEMATICS,
                     TestType.PLATFORM_AUDIT, TestType.INFORMATION_SECURITY,
                     TestType.SPORTS_BETTING_ASSESSMENT, TestType.GEOLOCATION_TESTING],
        typical_turnaround_weeks={
            TestType.RNG_CERTIFICATION.value: 5,
            TestType.GAME_MATHEMATICS.value: 3,
            TestType.PLATFORM_AUDIT.value: 6,
            TestType.INFORMATION_SECURITY.value: 4,
        },
        base_cost_usd={
            TestType.RNG_CERTIFICATION.value: 12000,
            TestType.GAME_MATHEMATICS.value: 4000,
            TestType.PLATFORM_AUDIT.value: 35000,
            TestType.INFORMATION_SECURITY.value: 18000,
        },
        per_game_cost_usd=2000,
        offices=["USA", "Canada", "UK", "Spain", "Macau", "Australia", "Argentina"],
        contact_email="sales@bmm.com",
        notes="Strong presence in Americas and Asia-Pacific. Competitive pricing.",
    ),
    TestingLab(
        code="ECOGRA",
        name="eCOGRA",
        full_name="eCommerce Online Gaming Regulation and Assurance",
        headquarters="London, United Kingdom",
        website="https://ecogra.org",
        jurisdictions_supported=[
            "MGA", "UKGC", "Gibraltar", "IOM", "Alderney", "Denmark", "Sweden",
            "Italy", "Romania", "Belgium",
        ],
        test_types=[TestType.RNG_CERTIFICATION, TestType.GAME_MATHEMATICS,
                     TestType.RESPONSIBLE_GAMING, TestType.PLATFORM_AUDIT],
        typical_turnaround_weeks={
            TestType.RNG_CERTIFICATION.value: 4,
            TestType.GAME_MATHEMATICS.value: 3,
            TestType.RESPONSIBLE_GAMING.value: 3,
            TestType.PLATFORM_AUDIT.value: 6,
        },
        base_cost_usd={
            TestType.RNG_CERTIFICATION.value: 10000,
            TestType.GAME_MATHEMATICS.value: 3500,
            TestType.RESPONSIBLE_GAMING.value: 8000,
            TestType.PLATFORM_AUDIT.value: 25000,
        },
        per_game_cost_usd=1800,
        offices=["UK", "Malta"],
        contact_email="info@ecogra.org",
        notes="Strong focus on player protection and fair gaming. "
              "Also provides Safe and Fair seals for operators.",
    ),
    TestingLab(
        code="ITECH",
        name="iTech Labs",
        full_name="iTech Labs Pty Ltd",
        headquarters="Melbourne, Australia",
        website="https://itechlabs.com",
        jurisdictions_supported=[
            "MGA", "UKGC", "IOM", "Alderney", "Australia", "New Zealand",
            "Philippines", "Curaçao", "South Africa",
        ],
        test_types=[TestType.RNG_CERTIFICATION, TestType.GAME_MATHEMATICS,
                     TestType.PLATFORM_AUDIT, TestType.INFORMATION_SECURITY],
        typical_turnaround_weeks={
            TestType.RNG_CERTIFICATION.value: 3,
            TestType.GAME_MATHEMATICS.value: 2,
            TestType.PLATFORM_AUDIT.value: 5,
            TestType.INFORMATION_SECURITY.value: 3,
        },
        base_cost_usd={
            TestType.RNG_CERTIFICATION.value: 8000,
            TestType.GAME_MATHEMATICS.value: 2500,
            TestType.PLATFORM_AUDIT.value: 20000,
            TestType.INFORMATION_SECURITY.value: 12000,
        },
        per_game_cost_usd=1500,
        offices=["Australia", "UK"],
        contact_email="info@itechlabs.com",
        notes="Fastest turnaround times. Cost-effective option. "
              "Popular with startups and mid-tier operators.",
    ),
    TestingLab(
        code="NMI",
        name="NMi Gaming",
        full_name="NMi Gaming B.V.",
        headquarters="Delft, Netherlands",
        website="https://nmi.nl",
        jurisdictions_supported=[
            "MGA", "UKGC", "Netherlands", "Belgium", "Denmark", "Sweden",
            "Germany", "Austria", "Switzerland",
        ],
        test_types=[TestType.RNG_CERTIFICATION, TestType.GAME_MATHEMATICS,
                     TestType.PLATFORM_AUDIT, TestType.RESPONSIBLE_GAMING],
        typical_turnaround_weeks={
            TestType.RNG_CERTIFICATION.value: 4,
            TestType.GAME_MATHEMATICS.value: 3,
            TestType.PLATFORM_AUDIT.value: 6,
        },
        base_cost_usd={
            TestType.RNG_CERTIFICATION.value: 11000,
            TestType.GAME_MATHEMATICS.value: 3500,
            TestType.PLATFORM_AUDIT.value: 28000,
        },
        per_game_cost_usd=2000,
        offices=["Netherlands", "UK", "Malta"],
        contact_email="gaming@nmi.nl",
        notes="Strong European presence. Specialist in Dutch and German markets.",
    ),
]


# ---------------------------------------------------------------------------
# Lab coordinator engine
# ---------------------------------------------------------------------------

class LabCoordinator:
    """Coordinate testing lab engagements for iGaming certification."""

    def __init__(self):
        self.labs: dict[str, TestingLab] = {lab.code: lab for lab in TESTING_LABS}
        self.engagements: dict[str, TestEngagement] = {}
        self._engagement_counter = 0

    def list_labs(self, jurisdiction: Optional[str] = None,
                  test_type: Optional[str] = None) -> list[dict]:
        """List available labs, optionally filtered."""
        results = []
        for lab in self.labs.values():
            if jurisdiction and jurisdiction.upper() not in [j.upper() for j in lab.jurisdictions_supported]:
                continue
            if test_type:
                try:
                    tt = TestType(test_type)
                    if tt not in lab.test_types:
                        continue
                except ValueError:
                    pass

            results.append({
                "code": lab.code,
                "name": lab.name,
                "full_name": lab.full_name,
                "headquarters": lab.headquarters,
                "jurisdictions": len(lab.jurisdictions_supported),
                "test_types": [t.value for t in lab.test_types],
                "offices": lab.offices,
            })
        return results

    def estimate_cost(self, lab_code: str, test_type: str,
                      game_count: int = 0) -> dict:
        """Estimate testing cost and timeline."""
        lab = self.labs.get(lab_code.upper())
        if not lab:
            return {"error": f"Lab '{lab_code}' not found"}

        base = lab.base_cost_usd.get(test_type, 0)
        per_game = lab.per_game_cost_usd * game_count if game_count > 0 else 0
        total = base + per_game
        weeks = lab.typical_turnaround_weeks.get(test_type, 6)

        # Add buffer for remediation
        remediation_buffer_weeks = 2
        total_weeks = weeks + remediation_buffer_weeks

        estimated_completion = datetime.now() + timedelta(weeks=total_weeks)

        return {
            "lab": lab.name,
            "test_type": test_type,
            "game_count": game_count,
            "base_cost_usd": base,
            "per_game_cost_usd": per_game,
            "total_estimated_usd": total,
            "testing_weeks": weeks,
            "remediation_buffer_weeks": remediation_buffer_weeks,
            "total_timeline_weeks": total_weeks,
            "estimated_completion": estimated_completion.strftime("%Y-%m-%d"),
            "notes": "Estimate only. Final cost depends on scope and complexity. "
                     "Remediation may extend timeline.",
        }

    def compare_labs(self, test_type: str, jurisdiction: str,
                     game_count: int = 0) -> dict:
        """Compare labs for a specific test type and jurisdiction."""
        estimates = []
        for lab in self.labs.values():
            if jurisdiction.upper() not in [j.upper() for j in lab.jurisdictions_supported]:
                continue
            try:
                tt = TestType(test_type)
                if tt not in lab.test_types:
                    continue
            except ValueError:
                continue

            est = self.estimate_cost(lab.code, test_type, game_count)
            if "error" not in est:
                est["offices_in_region"] = lab.offices
                estimates.append(est)

        estimates.sort(key=lambda x: x["total_estimated_usd"])

        return {
            "test_type": test_type,
            "jurisdiction": jurisdiction,
            "game_count": game_count,
            "labs_available": len(estimates),
            "estimates": estimates,
            "cheapest": estimates[0]["lab"] if estimates else None,
            "fastest": min(estimates, key=lambda x: x["testing_weeks"])["lab"] if estimates else None,
        }

    def create_engagement(self, lab_code: str, test_type: str,
                          jurisdiction: str, entity_name: str,
                          game_count: int = 0) -> dict:
        """Create a new testing engagement."""
        self._engagement_counter += 1
        eng_id = f"ENG-{self._engagement_counter:04d}"

        estimate = self.estimate_cost(lab_code, test_type, game_count)
        if "error" in estimate:
            return estimate

        engagement = TestEngagement(
            id=eng_id,
            lab_code=lab_code.upper(),
            test_type=TestType(test_type),
            jurisdiction=jurisdiction,
            entity_name=entity_name,
            game_count=game_count,
            submission_date=datetime.now().strftime("%Y-%m-%d"),
            estimated_completion=estimate["estimated_completion"],
            quoted_cost_usd=estimate["total_estimated_usd"],
            status=TestStatus.DRAFT,
        )

        self.engagements[eng_id] = engagement
        logger.info("Created engagement %s with %s for %s", eng_id, lab_code, test_type)

        # Generate preparation checklist
        checklist = self._generate_preparation_checklist(engagement)

        return {
            "engagement_id": eng_id,
            "lab": estimate["lab"],
            "test_type": test_type,
            "estimated_cost_usd": estimate["total_estimated_usd"],
            "estimated_completion": estimate["estimated_completion"],
            "preparation_checklist": checklist,
        }

    def _generate_preparation_checklist(self, eng: TestEngagement) -> list[dict]:
        """Generate lab-specific preparation checklist."""
        checklist = [
            {"step": 1, "task": "Sign NDA with testing lab",
             "responsible": "Legal", "timeline": "Week 1"},
            {"step": 2, "task": "Submit test plan and scope document",
             "responsible": "Technical Lead", "timeline": "Week 1"},
            {"step": 3, "task": "Provide API access / test environment credentials",
             "responsible": "DevOps", "timeline": "Week 1"},
        ]

        if eng.test_type == TestType.RNG_CERTIFICATION:
            checklist.extend([
                {"step": 4, "task": "Provide RNG source code and documentation",
                 "responsible": "Engineering", "timeline": "Week 1"},
                {"step": 5, "task": "Submit RNG statistical output samples (10M+ iterations)",
                 "responsible": "Engineering", "timeline": "Week 1"},
                {"step": 6, "task": "Provide seeding and entropy documentation",
                 "responsible": "Engineering", "timeline": "Week 1"},
            ])
        elif eng.test_type == TestType.GAME_MATHEMATICS:
            checklist.extend([
                {"step": 4, "task": "Submit PAR sheets for each game",
                 "responsible": "Game Math", "timeline": "Week 1"},
                {"step": 5, "task": "Provide game rules documentation",
                 "responsible": "Game Design", "timeline": "Week 1"},
                {"step": 6, "task": f"Provide access to {eng.game_count} games in test environment",
                 "responsible": "QA", "timeline": "Week 1"},
                {"step": 7, "task": "Submit bonus/feature mechanics documentation",
                 "responsible": "Game Design", "timeline": "Week 2"},
            ])
        elif eng.test_type == TestType.PLATFORM_AUDIT:
            checklist.extend([
                {"step": 4, "task": "Submit system architecture documentation",
                 "responsible": "Architecture", "timeline": "Week 1"},
                {"step": 5, "task": "Provide player account management documentation",
                 "responsible": "Product", "timeline": "Week 1"},
                {"step": 6, "task": "Submit financial transaction flow documentation",
                 "responsible": "Payments", "timeline": "Week 2"},
                {"step": 7, "task": "Provide reporting and audit trail samples",
                 "responsible": "BI", "timeline": "Week 2"},
                {"step": 8, "task": "Submit responsible gaming feature documentation",
                 "responsible": "Compliance", "timeline": "Week 2"},
            ])
        elif eng.test_type == TestType.INFORMATION_SECURITY:
            checklist.extend([
                {"step": 4, "task": "Provide network architecture diagrams",
                 "responsible": "Infrastructure", "timeline": "Week 1"},
                {"step": 5, "task": "Submit security policies and procedures",
                 "responsible": "Security", "timeline": "Week 1"},
                {"step": 6, "task": "Provide access for vulnerability scanning",
                 "responsible": "Security", "timeline": "Week 2"},
                {"step": 7, "task": "Submit incident response procedures",
                 "responsible": "Security", "timeline": "Week 2"},
            ])

        checklist.append({"step": len(checklist) + 1, "task": "Confirm engagement kickoff call with lab",
                           "responsible": "Project Manager", "timeline": "Week 2"})

        return checklist

    def get_engagement_status(self) -> list[dict]:
        """Get status of all engagements."""
        return [asdict(eng) for eng in self.engagements.values()]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="iGaming Testing Lab Coordinator")
    parser.add_argument("--labs", action="store_true", help="List available testing labs")
    parser.add_argument("--lab", type=str, help="Specific lab code")
    parser.add_argument("--test-type", type=str, help="Test type")
    parser.add_argument("--jurisdiction", type=str, help="Target jurisdiction")
    parser.add_argument("--games", type=int, default=0, help="Number of games to test")
    parser.add_argument("--estimate", action="store_true", help="Cost/time estimate")
    parser.add_argument("--compare", action="store_true", help="Compare labs")
    parser.add_argument("--schedule", action="store_true", help="Create engagement")
    parser.add_argument("--entity", type=str, default="AcmetoCasino Ltd", help="Entity name")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    coordinator = LabCoordinator()

    if args.labs:
        labs = coordinator.list_labs(args.jurisdiction, args.test_type)
        if args.format == "json":
            print(json.dumps(labs, indent=2))
        else:
            print(f"\n=== Available Testing Labs ===\n")
            for lab in labs:
                print(f"  {lab['code']:<8} {lab['full_name']}")
                print(f"           HQ: {lab['headquarters']}")
                print(f"           Jurisdictions: {lab['jurisdictions']}")
                print(f"           Offices: {', '.join(lab['offices'])}")
                print()
        return

    if args.compare and args.test_type and args.jurisdiction:
        result = coordinator.compare_labs(args.test_type, args.jurisdiction, args.games)
        print(json.dumps(result, indent=2, default=str))
        return

    if args.estimate and args.lab and args.test_type:
        result = coordinator.estimate_cost(args.lab, args.test_type, args.games)
        print(json.dumps(result, indent=2, default=str))
        return

    if args.schedule and args.lab and args.test_type and args.jurisdiction:
        result = coordinator.create_engagement(
            args.lab, args.test_type, args.jurisdiction, args.entity, args.games)
        print(json.dumps(result, indent=2, default=str))
        return

    # Default: show test types
    print("Available test types:")
    for tt in TestType:
        print(f"  {tt.value}")
    print("\nUsage examples:")
    print("  python lab_coordinator.py --labs")
    print("  python lab_coordinator.py --compare --test-type rng_certification --jurisdiction MGA --games 20")
    print("  python lab_coordinator.py --estimate --lab GLI --test-type platform_audit")


if __name__ == "__main__":
    main()
