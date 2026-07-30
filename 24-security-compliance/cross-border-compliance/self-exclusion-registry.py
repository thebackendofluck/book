#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
self-exclusion-registry.py

Cross-registry self-exclusion check simulation for iGaming operators.

This tool simulates the real-time multi-registry lookup that a compliant
operator must perform at player login and session renewal. It queries
simulated registry states for:

  - GAMSTOP (UK)
  - Spelpaus (Sweden)
  - ROFUS (Denmark)
  - OASIS / LUGAS (Germany)
  - Cruks (Netherlands)
  - EPIS (Belgium)
  - RGIAJ (Spain)
  - AUMS (France)
  - ReSPONSe (Malta)

In a real deployment each lookup would be an authenticated API call to the
respective registry. This simulator uses in-memory test fixtures to
demonstrate the decision logic and response handling an operator backend
must implement.

Usage:
    python self-exclusion-registry.py --player-id P001 --registries GAMSTOP Spelpaus ROFUS
    python self-exclusion-registry.py --player-id P001 --all-registries
    python self-exclusion-registry.py --list-players
    python self-exclusion-registry.py --demo

Dependencies: none (stdlib only)
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional


class ExclusionStatus(str, Enum):
    EXCLUDED = "excluded"        # Player is actively excluded
    NOT_EXCLUDED = "not_excluded"  # Player is not in this registry
    EXPIRED = "expired"          # Exclusion period has ended (registry dependent)
    SUSPENDED = "suspended"      # Exclusion temporarily suspended (rare; jurisdiction specific)
    ERROR = "error"              # Registry lookup failed


class ExclusionDuration(str, Enum):
    SIX_MONTHS = "6_months"
    ONE_YEAR = "1_year"
    THREE_YEARS = "3_years"
    FIVE_YEARS = "5_years"
    PERMANENT = "permanent"


@dataclass
class RegistryDefinition:
    name: str
    jurisdiction: str
    jurisdiction_code: str
    url: str
    lookup_identifier: str  # What identifier is used (e.g. "national_id", "email+dob", "player_id")
    operator_registration_required: bool
    revocable: bool          # Can the player lift their own exclusion?
    min_duration: str
    cooling_off_period: Optional[str]  # Time before exclusion takes effect after registration
    description: str


REGISTRY_DEFINITIONS: dict[str, RegistryDefinition] = {
    "GAMSTOP": RegistryDefinition(
        name="GAMSTOP",
        jurisdiction="United Kingdom",
        jurisdiction_code="GB",
        url="https://www.gamstop.co.uk",
        lookup_identifier="email+date_of_birth",
        operator_registration_required=True,
        revocable=True,
        min_duration="6 months",
        cooling_off_period="24 hours",
        description=(
            "UK national self-exclusion scheme. Mandatory for all UKGC licensees. "
            "Players register with email + DOB. Minimum 6-month exclusion. "
            "Operators must check GAMSTOP at every account registration and login."
        ),
    ),
    "Spelpaus": RegistryDefinition(
        name="Spelpaus",
        jurisdiction="Sweden",
        jurisdiction_code="SE",
        url="https://www.spelpaus.se",
        lookup_identifier="swedish_personnummer",
        operator_registration_required=True,
        revocable=True,
        min_duration="1 month",
        cooling_off_period="24 hours",
        description=(
            "Swedish national self-exclusion register operated by Spelinspektionen. "
            "Mandatory for all SE licensees. Uses Swedish personal identity number "
            "(personnummer). Minimum 1-month exclusion. Player can revoke after minimum period."
        ),
    ),
    "ROFUS": RegistryDefinition(
        name="ROFUS",
        jurisdiction="Denmark",
        jurisdiction_code="DK",
        url="https://www.rofus.nu",
        lookup_identifier="danish_cpr",
        operator_registration_required=True,
        revocable=True,
        min_duration="1 day",
        cooling_off_period=None,
        description=(
            "Danish Register of Voluntarily Excluded Players (ROFUS). "
            "Mandatory for all Spillemyndigheden licensees. Uses Danish CPR (civil registration) number. "
            "Exclusion durations from 1 day to permanent. Revocable after minimum period."
        ),
    ),
    "OASIS": RegistryDefinition(
        name="OASIS",
        jurisdiction="Germany",
        jurisdiction_code="DE",
        url="https://www.gluecksspiel.de/oasis",
        lookup_identifier="german_id_document",
        operator_registration_required=True,
        revocable=False,  # Minimum 1 year; reinstatement via formal process
        min_duration="1 year",
        cooling_off_period="24 hours",
        description=(
            "German national self-exclusion system (Übergreifendes Sperrsystem). "
            "Mandatory for all GGL licensees. Uses government-issued ID. "
            "Exclusions apply across ALL licensed German operators. "
            "Cannot be self-revoked; requires formal application after minimum 1 year. "
            "Also integrates with LUGAS (cross-operator spending limit tracker)."
        ),
    ),
    "Cruks": RegistryDefinition(
        name="Cruks",
        jurisdiction="Netherlands",
        jurisdiction_code="NL",
        url="https://cruks.nl",
        lookup_identifier="dutch_bsn",
        operator_registration_required=True,
        revocable=True,
        min_duration="6 months",
        cooling_off_period="24 hours",
        description=(
            "Dutch Central Register for Exclusion from Games of Chance (Cruks). "
            "Mandatory for all KSA licensees. Uses Dutch BSN (Burger Service Nummer). "
            "Minimum 6-month exclusion. 24-hour cooling-off after registration. "
            "Revocable after minimum period with 24-hour cooling-off."
        ),
    ),
    "EPIS": RegistryDefinition(
        name="EPIS",
        jurisdiction="Belgium",
        jurisdiction_code="BE",
        url="https://www.gamingcommission.be",
        lookup_identifier="belgian_national_id",
        operator_registration_required=True,
        revocable=True,
        min_duration="3 months",
        cooling_off_period="72 hours",
        description=(
            "Belgian Excluded Persons Information System. "
            "Mandatory for all BGC licensees. Uses Belgian national register number. "
            "Minimum 3-month exclusion. 72-hour cooling-off before exclusion takes effect."
        ),
    ),
    "RGIAJ": RegistryDefinition(
        name="RGIAJ",
        jurisdiction="Spain",
        jurisdiction_code="ES",
        url="https://www.ordenacionjuego.es/rgiaj",
        lookup_identifier="spanish_dni_nie",
        operator_registration_required=True,
        revocable=True,
        min_duration="6 months",
        cooling_off_period=None,
        description=(
            "Spanish General Register of Gambling Access Prohibitions (RGIAJ). "
            "Mandatory for all DGOJ licensees. Uses Spanish DNI or NIE (foreigner number). "
            "Minimum 6-month exclusion."
        ),
    ),
    "AUMS": RegistryDefinition(
        name="AUMS",
        jurisdiction="France",
        jurisdiction_code="FR",
        url="https://www.anj.fr",
        lookup_identifier="french_national_id",
        operator_registration_required=True,
        revocable=True,
        min_duration="3 years",
        cooling_off_period=None,
        description=(
            "French self-exclusion register managed by ANJ. "
            "Mandatory for all ANJ licensees. Minimum 3-year exclusion — one of the longest in Europe. "
            "Players must apply in person or online to the ANJ."
        ),
    ),
    "ReSPONSe": RegistryDefinition(
        name="ReSPONSe",
        jurisdiction="Malta",
        jurisdiction_code="MT",
        url="https://www.mga.org.mt",
        lookup_identifier="government_id",
        operator_registration_required=True,
        revocable=True,
        min_duration="3 months",
        cooling_off_period="24 hours",
        description=(
            "Malta national self-exclusion register managed by the MGA. "
            "Mandatory for all MGA licensees serving Maltese players. "
            "Minimum 3-month exclusion."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Test fixture data — simulated player exclusion states
# ---------------------------------------------------------------------------

@dataclass
class ExclusionRecord:
    player_id: str
    registry: str
    status: ExclusionStatus
    excluded_since: Optional[date] = None
    excluded_until: Optional[date] = None
    duration: Optional[ExclusionDuration] = None
    reason: str = "voluntary"
    identifier_used: str = "test_fixture"


# Simulated test players — in production these would be resolved via secure API lookups
TEST_EXCLUSION_DATABASE: dict[tuple[str, str], ExclusionRecord] = {
    # Player P001: Excluded on GAMSTOP and Spelpaus, clean elsewhere
    ("P001", "GAMSTOP"): ExclusionRecord(
        player_id="P001",
        registry="GAMSTOP",
        status=ExclusionStatus.EXCLUDED,
        excluded_since=date(2024, 3, 1),
        excluded_until=date(2025, 3, 1),
        duration=ExclusionDuration.ONE_YEAR,
        reason="voluntary",
    ),
    ("P001", "Spelpaus"): ExclusionRecord(
        player_id="P001",
        registry="Spelpaus",
        status=ExclusionStatus.EXCLUDED,
        excluded_since=date(2024, 3, 1),
        excluded_until=None,  # Permanent
        duration=ExclusionDuration.PERMANENT,
        reason="voluntary",
    ),
    # Player P002: Excluded on OASIS (Germany) — cannot be reversed
    ("P002", "OASIS"): ExclusionRecord(
        player_id="P002",
        registry="OASIS",
        status=ExclusionStatus.EXCLUDED,
        excluded_since=date(2023, 6, 15),
        excluded_until=date(2024, 6, 15),
        duration=ExclusionDuration.ONE_YEAR,
        reason="voluntary",
    ),
    # Player P003: Excluded on ROFUS with expired exclusion
    ("P003", "ROFUS"): ExclusionRecord(
        player_id="P003",
        registry="ROFUS",
        status=ExclusionStatus.EXPIRED,
        excluded_since=date(2022, 1, 1),
        excluded_until=date(2023, 1, 1),
        duration=ExclusionDuration.ONE_YEAR,
        reason="voluntary",
    ),
    # Player P004: Clean on all registries (no records = not excluded)
}


def lookup_registry(
    player_id: str,
    registry_name: str,
) -> dict:
    """
    Simulate a registry lookup for a player.

    In production this would be:
        response = registry_api_client.check(player_identifier=hashed_identifier)
    """
    registry_def = REGISTRY_DEFINITIONS.get(registry_name)
    if not registry_def:
        return {
            "registry": registry_name,
            "status": ExclusionStatus.ERROR,
            "error": f"Unknown registry: {registry_name}",
            "action_required": "BLOCK",
        }

    record = TEST_EXCLUSION_DATABASE.get((player_id, registry_name))

    if record is None:
        # No record in registry = not excluded
        return {
            "registry": registry_name,
            "jurisdiction": registry_def.jurisdiction,
            "jurisdiction_code": registry_def.jurisdiction_code,
            "player_id": player_id,
            "status": ExclusionStatus.NOT_EXCLUDED,
            "action_required": "ALLOW",
            "message": f"Player not found in {registry_name} — access permitted for {registry_def.jurisdiction}.",
        }

    if record.status == ExclusionStatus.EXCLUDED:
        until_str = str(record.excluded_until) if record.excluded_until else "permanent"
        return {
            "registry": registry_name,
            "jurisdiction": registry_def.jurisdiction,
            "jurisdiction_code": registry_def.jurisdiction_code,
            "player_id": player_id,
            "status": ExclusionStatus.EXCLUDED,
            "excluded_since": str(record.excluded_since),
            "excluded_until": until_str,
            "duration": record.duration,
            "action_required": "BLOCK",
            "message": (
                f"PLAYER EXCLUDED: {player_id} is registered in {registry_name} "
                f"since {record.excluded_since} until {until_str}. "
                f"Access to {registry_def.jurisdiction}-licensed services MUST be denied."
            ),
        }

    if record.status == ExclusionStatus.EXPIRED:
        return {
            "registry": registry_name,
            "jurisdiction": registry_def.jurisdiction,
            "jurisdiction_code": registry_def.jurisdiction_code,
            "player_id": player_id,
            "status": ExclusionStatus.EXPIRED,
            "excluded_until": str(record.excluded_until),
            "action_required": "ALLOW_WITH_FLAG",
            "message": (
                f"Exclusion EXPIRED: {player_id} had a {registry_name} exclusion that expired "
                f"on {record.excluded_until}. Player is permitted to access the platform. "
                "Consider responsible gambling review and enhanced monitoring."
            ),
        }

    return {
        "registry": registry_name,
        "jurisdiction": registry_def.jurisdiction,
        "jurisdiction_code": registry_def.jurisdiction_code,
        "player_id": player_id,
        "status": record.status,
        "action_required": "REVIEW",
        "message": f"Unexpected registry state for {player_id}: {record.status}",
    }


def check_all_registries(
    player_id: str,
    registry_names: list[str],
) -> dict:
    """Run lookups across multiple registries and produce a consolidated decision."""
    results = []
    for reg in registry_names:
        result = lookup_registry(player_id, reg)
        results.append(result)

    # Consolidated decision: any EXCLUDED result = global block
    blocked_in = [r for r in results if r.get("action_required") == "BLOCK"]
    flagged_in = [r for r in results if r.get("action_required") == "ALLOW_WITH_FLAG"]
    allowed_in = [r for r in results if r.get("action_required") == "ALLOW"]
    errors = [r for r in results if r.get("status") == ExclusionStatus.ERROR]

    if blocked_in:
        final_action = "BLOCK"
        summary = (
            f"Player {player_id} is EXCLUDED in {len(blocked_in)} registry/registries: "
            + ", ".join(r["registry"] for r in blocked_in)
            + ". Session MUST be terminated / account access denied."
        )
    elif errors:
        final_action = "BLOCK_PENDING_RETRY"
        summary = (
            f"Registry lookup errors for {len(errors)} registries. "
            "Per responsible gambling policy, access must be denied until all registries confirm "
            "player is not excluded. Retry required."
        )
    elif flagged_in:
        final_action = "ALLOW_WITH_ENHANCED_MONITORING"
        summary = (
            f"Player {player_id} has expired exclusions in "
            + ", ".join(r["registry"] for r in flagged_in)
            + ". Access permitted; apply enhanced responsible gambling monitoring."
        )
    else:
        final_action = "ALLOW"
        summary = (
            f"Player {player_id} is not excluded in any checked registry. Access permitted."
        )

    return {
        "player_id": player_id,
        "check_timestamp": datetime.now(timezone.utc).isoformat(),
        "registries_checked": len(results),
        "final_action": final_action,
        "summary": summary,
        "registry_results": results,
        "blocked_count": len(blocked_in),
        "flagged_count": len(flagged_in),
        "allowed_count": len(allowed_in),
        "error_count": len(errors),
    }


def run_demo() -> None:
    """Run a demo showing all test scenarios."""
    demo_scenarios = [
        ("P001", list(REGISTRY_DEFINITIONS.keys()), "Player with GAMSTOP + Spelpaus exclusions"),
        ("P002", list(REGISTRY_DEFINITIONS.keys()), "Player with OASIS (Germany) exclusion"),
        ("P003", list(REGISTRY_DEFINITIONS.keys()), "Player with expired ROFUS (Denmark) exclusion"),
        ("P004", list(REGISTRY_DEFINITIONS.keys()), "Clean player — no exclusions"),
    ]

    for player_id, registries, scenario_desc in demo_scenarios:
        print(f"\n{'=' * 70}")
        print(f"SCENARIO: {scenario_desc}")
        print(f"{'=' * 70}")
        result = check_all_registries(player_id, registries)
        print(f"Player ID:        {result['player_id']}")
        print(f"Check Timestamp:  {result['check_timestamp']}")
        print(f"Registries:       {result['registries_checked']}")
        print(f"FINAL ACTION:     {result['final_action']}")
        print(f"Summary:          {result['summary']}")

        if result["blocked_count"] > 0:
            print("\nBlocked registries:")
            for r in result["registry_results"]:
                if r.get("action_required") == "BLOCK":
                    print(f"  - {r['registry']} ({r['jurisdiction']}): {r['message']}")

        if result["flagged_count"] > 0:
            print("\nFlagged (expired exclusion):")
            for r in result["registry_results"]:
                if r.get("action_required") == "ALLOW_WITH_FLAG":
                    print(f"  - {r['registry']} ({r['jurisdiction']}): {r['message']}")

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cross-registry self-exclusion check simulation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--player-id",
        metavar="ID",
        help="Player identifier to check (use P001–P004 for test fixtures)",
    )
    p.add_argument(
        "--registries",
        nargs="+",
        metavar="REG",
        help="Registry names to check (e.g. GAMSTOP Spelpaus ROFUS). Use --list-registries to see options.",
    )
    p.add_argument(
        "--all-registries",
        action="store_true",
        help="Check all known registries",
    )
    p.add_argument(
        "--list-registries",
        action="store_true",
        help="List all registries in the database",
    )
    p.add_argument(
        "--list-players",
        action="store_true",
        help="List all test fixture players",
    )
    p.add_argument(
        "--demo",
        action="store_true",
        help="Run all demo scenarios",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_registries:
        print(f"\nKnown self-exclusion registries ({len(REGISTRY_DEFINITIONS)} total):\n")
        for name, reg in REGISTRY_DEFINITIONS.items():
            print(f"  {name:12s}  [{reg.jurisdiction_code:2s}]  {reg.jurisdiction}")
            print(f"             {reg.description[:80]}...")
            print()
        return 0

    if args.list_players:
        print("\nTest fixture players:\n")
        players: dict[str, list[str]] = {}
        for (pid, reg), record in TEST_EXCLUSION_DATABASE.items():
            players.setdefault(pid, []).append(f"{reg} ({record.status.value})")
        for pid, exclusions in sorted(players.items()):
            print(f"  {pid}: {', '.join(exclusions)}")
        print("  P004: No exclusions (clean player)")
        print()
        return 0

    if args.demo:
        run_demo()
        return 0

    if not args.player_id:
        parser.print_help()
        return 1

    registries = (
        list(REGISTRY_DEFINITIONS.keys())
        if args.all_registries
        else (args.registries or [])
    )

    if not registries:
        print("ERROR: specify --registries or --all-registries", file=sys.stderr)
        return 1

    result = check_all_registries(args.player_id, registries)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\nSelf-Exclusion Check: {result['player_id']}")
        print(f"Timestamp:    {result['check_timestamp']}")
        print(f"Registries:   {result['registries_checked']} checked")
        print(f"FINAL ACTION: {result['final_action']}")
        print(f"Summary:      {result['summary']}")
        print()

        for r in result["registry_results"]:
            action = r.get("action_required", "?")
            status = r.get("status", "?")
            reg_name = r.get("registry", "?")
            jurisdiction = r.get("jurisdiction", "")
            print(f"  [{action:35s}]  {reg_name:12s}  {jurisdiction} — {status}")

        print()

    # Exit with non-zero if BLOCK required
    return 1 if result["final_action"] in ("BLOCK", "BLOCK_PENDING_RETRY") else 0


if __name__ == "__main__":
    sys.exit(main())
