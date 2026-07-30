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
DSR Decision Engine — Per-Field Erasure Decision Tree
======================================================
Given a player_id and jurisdiction, evaluates every PII field against AML holds,
retention windows, and legal exemptions to produce a per-field action plan.

Regulatory context:
  - GDPR Art. 17(1): Right to erasure ("right to be forgotten")
  - GDPR Art. 17(3)(b): Exemption — legal obligation to retain
  - GDPR Art. 17(3)(e): Exemption — establishment/exercise of legal claims
  - AMLD Art. 40: AML record-keeping obligation (5 years)
  - LGPD Art. 16: Data retention after consent withdrawal
  - MGA Player Protection Directive 2018, Art. 11
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from enum import Enum
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dsr_decision_engine")


class Action(str, Enum):
    DELETE = "DELETE"               # Immediate hard delete
    PSEUDONYMIZE = "PSEUDONYMIZE"   # Replace PII with opaque token
    RETAIN = "RETAIN"               # Cannot touch — legal hold active
    DEFER = "DEFER"                 # Retention window not elapsed; revisit later


class Jurisdiction(str, Enum):
    GDPR_EU = "GDPR_EU"
    LGPD_BR = "LGPD_BR"
    CCPA_US = "CCPA_US"
    MGA_MT = "MGA_MT"


@dataclass
class AMLHold:
    player_id: str
    reason: str                     # e.g. "STR filed", "PEP match"
    hold_until: date                # earliest date the hold can be lifted


@dataclass
class FieldDecision:
    field: str
    action: Action
    legal_basis: str                # Citation for the chosen action
    defer_until: Optional[date]     # Populated only when action == DEFER
    notes: str


@dataclass
class ActionPlan:
    player_id: str
    jurisdiction: Jurisdiction
    evaluated_at: str
    aml_hold_active: bool
    decisions: list[FieldDecision]


# ---------------------------------------------------------------------------
# Stub data sources — production implementations query PostgreSQL / Redis.
# ---------------------------------------------------------------------------

def _get_aml_hold(player_id: str) -> Optional[AMLHold]:
    """Return active AML hold if present. Stub returns a hold for demo player."""
    HOLDS: dict[str, AMLHold] = {
        "player_aml_001": AMLHold(
            player_id="player_aml_001",
            reason="STR filed — suspicious large cash equivalent deposits",
            hold_until=date.today() + timedelta(days=730),  # 2-year investigation hold
        ),
    }
    return HOLDS.get(player_id)


def _get_account_created(player_id: str) -> date:
    """Return account creation date. Stub returns a fixed date."""
    _ = player_id
    return date(2019, 3, 15)


# ---------------------------------------------------------------------------
# Retention windows per category (years) — sourced from legal matrix.
# See: FATF Rec. 11 (AML 5yr), EU VAT Directive 2006/112 Art. 244 (tax 7yr),
#      MGA Licence Conditions 2021 (RG 5yr), GDPR Art. 5(1)(e) (minimisation).
# ---------------------------------------------------------------------------
RETENTION_MATRIX: dict[str, tuple[int, str, str]] = {
    # field_name: (retention_years, legal_basis, authority)
    "email":                 (3,  "GDPR Art. 5(1)(e) — storage limitation",        "GDPR"),
    "full_name":             (3,  "GDPR Art. 5(1)(e)",                             "GDPR"),
    "date_of_birth":         (5,  "AMLD Art. 40 — KYC record-keeping",             "FATF/EBA"),
    "national_id":           (5,  "AMLD Art. 40",                                  "FATF/EBA"),
    "ip_address":            (1,  "GDPR Art. 6(1)(f) — fraud prevention 1yr",      "GDPR"),
    "device_fingerprint":    (1,  "GDPR Art. 6(1)(f)",                             "GDPR"),
    "face_image_hash":       (5,  "MGA LC 2021 Art. 11 — KYC biometrics",         "MGA"),
    "liveness_score":        (0,  "GDPR Art. 5(1)(c) — data minimisation",         "GDPR"),
    "card_last4":            (1,  "PCI-DSS Req. 3.4 — limited card data",          "PCI SSC"),
    "iban":                  (7,  "GDPR Art. 17(3)(b) — tax obligation 7yr",       "HMRC/Receita Federal"),
    "amount_eur":            (7,  "GDPR Art. 17(3)(b) — tax/accounting",           "HMRC"),
    "merchant_ref":          (7,  "GDPR Art. 17(3)(b)",                            "HMRC"),
    "session_duration_s":    (0,  "GDPR Art. 5(1)(c) — no retention basis",        "GDPR"),
    "bet_pattern_vector":    (2,  "MGA LC 2021 — RG monitoring",                   "MGA"),
    "self_exclusion_reason": (5,  "MGA LC 2021 Art. 11 — RG records mandatory",    "MGA"),
    "flag_reason":           (5,  "AMLD Art. 40 — AML records",                    "FATF"),
}

# Fields that must be RETAINED unconditionally under active AML investigation
AML_HOLD_FIELDS = {
    "full_name", "date_of_birth", "national_id", "iban",
    "amount_eur", "merchant_ref", "flag_reason",
}

# Fields eligible for pseudonymisation rather than hard delete
PSEUDONYMIZABLE_FIELDS = {"email", "full_name", "ip_address", "device_fingerprint"}


def _decide_field(
    field_name: str,
    account_created: date,
    jurisdiction: Jurisdiction,
    aml_hold: Optional[AMLHold],
    reference_date: date,
) -> FieldDecision:
    """Apply the decision tree for a single field."""

    # Step 1 — AML hold overrides everything (GDPR Art. 17(3)(e))
    if aml_hold is not None and field_name in AML_HOLD_FIELDS:
        return FieldDecision(
            field=field_name,
            action=Action.RETAIN,
            legal_basis="GDPR Art. 17(3)(e) — establishment/exercise of legal claims; "
                        f"AML hold until {aml_hold.hold_until} ({aml_hold.reason})",
            defer_until=aml_hold.hold_until,
            notes="AML investigation hold prevents erasure.",
        )

    # Step 2 — Check statutory retention window
    retention_years, legal_basis, _authority = RETENTION_MATRIX.get(
        field_name, (0, "GDPR Art. 5(1)(c) — data minimisation", "GDPR")
    )
    if retention_years == 0:
        # No retention basis → immediate action
        if field_name in PSEUDONYMIZABLE_FIELDS:
            return FieldDecision(
                field=field_name,
                action=Action.PSEUDONYMIZE,
                legal_basis="GDPR Art. 17(1) — right to erasure; pseudonymisation preserves analytics",
                defer_until=None,
                notes="Field pseudonymised; re-identification key destroyed.",
            )
        return FieldDecision(
            field=field_name,
            action=Action.DELETE,
            legal_basis="GDPR Art. 17(1) — right to erasure; no retention basis exists",
            defer_until=None,
            notes="Hard delete scheduled.",
        )

    # Step 3 — Retention window still active?
    window_end = date(account_created.year + retention_years, account_created.month, account_created.day)
    if reference_date < window_end:
        return FieldDecision(
            field=field_name,
            action=Action.DEFER,
            legal_basis=legal_basis,
            defer_until=window_end,
            notes=f"Retention window expires {window_end}. Erasure deferred.",
        )

    # Step 4 — Window elapsed; apply LGPD Art. 16 / GDPR Art. 17 deletion
    if field_name in PSEUDONYMIZABLE_FIELDS:
        return FieldDecision(
            field=field_name,
            action=Action.PSEUDONYMIZE,
            legal_basis="GDPR Art. 17(1) — retention window elapsed; pseudonymisation applied",
            defer_until=None,
            notes="Retention window expired. Pseudonymising.",
        )
    return FieldDecision(
        field=field_name,
        action=Action.DELETE,
        legal_basis="GDPR Art. 17(1) — retention window elapsed; hard delete",
        defer_until=None,
        notes="Retention window expired. Deleting.",
    )


def build_action_plan(
    player_id: str,
    jurisdiction: Jurisdiction,
    reference_date: Optional[date] = None,
) -> ActionPlan:
    """Build a complete per-field action plan for the given player and jurisdiction."""
    ref = reference_date or date.today()
    aml_hold = _get_aml_hold(player_id)
    account_created = _get_account_created(player_id)

    if aml_hold:
        logger.warning("Player %s has active AML hold until %s", player_id, aml_hold.hold_until)

    decisions: list[FieldDecision] = []
    for field_name in RETENTION_MATRIX:
        decision = _decide_field(field_name, account_created, jurisdiction, aml_hold, ref)
        logger.info("  %s → %s", field_name, decision.action.value)
        decisions.append(decision)

    plan = ActionPlan(
        player_id=player_id,
        jurisdiction=jurisdiction,
        evaluated_at=ref.isoformat(),
        aml_hold_active=aml_hold is not None,
        decisions=decisions,
    )
    logger.info("Action plan built: %d fields evaluated for player %s", len(decisions), player_id)
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a per-field DSR erasure action plan.")
    parser.add_argument("--player-id", required=True, help="Player UUID or identifier")
    parser.add_argument("--jurisdiction", default="GDPR_EU",
                        choices=[j.value for j in Jurisdiction],
                        help="Regulatory jurisdiction")
    parser.add_argument("--output", default="-", help="Output file path (default: stdout)")
    args = parser.parse_args()

    plan = build_action_plan(args.player_id, Jurisdiction(args.jurisdiction))
    payload = json.dumps(asdict(plan), indent=2, default=str)

    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        with open(args.output, "w") as fh:
            fh.write(payload + "\n")
        logger.info("Action plan written to %s", args.output)
