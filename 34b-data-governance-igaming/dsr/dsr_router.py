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
DSR Router — Jurisdiction-Aware SLA Routing
============================================
Maps each pending DSR to its jurisdictional deadline, calculates remaining SLA
time, assigns priority, and returns a sorted processing queue.

Priority logic:
  1. Requests closest to their SLA deadline (ascending remaining days)
  2. Brazil (LGPD) requests bumped up when tied — 15 business-day window is shortest
  3. Requests already past their SLA deadline are flagged as BREACHED and go first

Regulatory context:
  - GDPR Art. 12(3): Controller shall provide info without undue delay, within 1 month
  - GDPR Art. 12(5): Repeated or manifestly unfounded requests may be refused
  - LGPD Art. 18 §3: Response within 15 business days
  - CCPA § 1798.105(b): Respond within 45 days (extendable by 45 more)
  - MGA Data Protection Policy: aligned with GDPR 30-day rule
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dsr_router")


class Jurisdiction(str, Enum):
    GDPR_EU = "GDPR_EU"
    LGPD_BR = "LGPD_BR"
    CCPA_US = "CCPA_US"
    MGA_MT  = "MGA_MT"


class DSRType(str, Enum):
    ACCESS      = "ACCESS"
    ERASURE     = "ERASURE"
    PORTABILITY = "PORTABILITY"
    RECTIFY     = "RECTIFY"
    RESTRICT    = "RESTRICT"


class SLAStatus(str, Enum):
    ON_TRACK = "ON_TRACK"
    AT_RISK  = "AT_RISK"    # < 5 business days remaining
    BREACHED = "BREACHED"   # Past deadline — immediate escalation required


# ---------------------------------------------------------------------------
# SLA configuration by jurisdiction (calendar days from submission)
# ---------------------------------------------------------------------------
@dataclass
class JurisdictionConfig:
    name: str
    sla_calendar_days: int
    extension_allowed_days: int     # One-time extension if complexity warrants
    legal_basis: str


JURISDICTION_MAP: dict[Jurisdiction, JurisdictionConfig] = {
    Jurisdiction.GDPR_EU: JurisdictionConfig(
        name="European Union (GDPR)",
        sla_calendar_days=30,
        extension_allowed_days=60,
        legal_basis="GDPR Art. 12(3)",
    ),
    Jurisdiction.LGPD_BR: JurisdictionConfig(
        name="Brazil (LGPD)",
        sla_calendar_days=21,       # 15 business days ≈ 21 calendar days
        extension_allowed_days=0,
        legal_basis="LGPD Art. 18 §3",
    ),
    Jurisdiction.CCPA_US: JurisdictionConfig(
        name="California (CCPA)",
        sla_calendar_days=45,
        extension_allowed_days=45,
        legal_basis="CCPA § 1798.105(b)",
    ),
    Jurisdiction.MGA_MT: JurisdictionConfig(
        name="Malta (MGA / GDPR aligned)",
        sla_calendar_days=30,
        extension_allowed_days=60,
        legal_basis="GDPR Art. 12(3) / MGA DPP",
    ),
}


@dataclass
class PendingDSR:
    dsr_id: str
    player_id: str
    request_type: DSRType
    jurisdiction: Jurisdiction
    submitted_at: date
    extended: bool = False


@dataclass
class RoutedDSR:
    dsr_id: str
    player_id: str
    request_type: str
    jurisdiction: str
    submitted_at: str
    deadline: str
    remaining_calendar_days: int
    sla_status: SLAStatus
    priority_score: int             # Lower = higher priority
    legal_basis: str
    notes: str


def _compute_deadline(submitted: date, jurisdiction: Jurisdiction, extended: bool) -> date:
    """Return the SLA deadline date for a DSR."""
    config = JURISDICTION_MAP[jurisdiction]
    days = config.sla_calendar_days
    if extended:
        days += config.extension_allowed_days
    return submitted + timedelta(days=days)


def _compute_priority(remaining_days: int, jurisdiction: Jurisdiction) -> int:
    """
    Compute priority score (lower = higher urgency).

    Breached requests get score 0. LGPD requests get a -1 bonus because
    the 21-day window leaves the least headroom operationally.
    """
    if remaining_days < 0:
        return 0
    score = remaining_days
    if jurisdiction == Jurisdiction.LGPD_BR:
        score = max(0, score - 1)   # LGPD priority bump
    return score


def _sla_status(remaining_days: int) -> SLAStatus:
    if remaining_days < 0:
        return SLAStatus.BREACHED
    if remaining_days <= 5:
        return SLAStatus.AT_RISK
    return SLAStatus.ON_TRACK


def route_queue(pending: list[PendingDSR], reference_date: Optional[date] = None) -> list[RoutedDSR]:
    """
    Process a list of pending DSRs and return a priority-sorted queue.

    Parameters
    ----------
    pending:        list of PendingDSR objects
    reference_date: date to compute remaining days against (default: today)

    Returns
    -------
    List of RoutedDSR sorted by priority_score ascending (most urgent first).
    """
    today = reference_date or date.today()
    routed: list[RoutedDSR] = []

    for dsr in pending:
        config = JURISDICTION_MAP[dsr.jurisdiction]
        deadline = _compute_deadline(dsr.submitted_at, dsr.jurisdiction, dsr.extended)
        remaining = (deadline - today).days
        status = _sla_status(remaining)
        priority = _compute_priority(remaining, dsr.jurisdiction)

        notes_parts: list[str] = []
        if status == SLAStatus.BREACHED:
            notes_parts.append("SLA BREACHED — escalate immediately")
        elif status == SLAStatus.AT_RISK:
            notes_parts.append(f"AT RISK — {remaining} days remaining")
        if dsr.extended:
            notes_parts.append(f"Extension applied (+{config.extension_allowed_days} days)")

        routed.append(RoutedDSR(
            dsr_id=dsr.dsr_id,
            player_id=dsr.player_id,
            request_type=dsr.request_type.value,
            jurisdiction=dsr.jurisdiction.value,
            submitted_at=dsr.submitted_at.isoformat(),
            deadline=deadline.isoformat(),
            remaining_calendar_days=remaining,
            sla_status=status,
            priority_score=priority,
            legal_basis=config.legal_basis,
            notes="; ".join(notes_parts) if notes_parts else "Within SLA",
        ))

    routed.sort(key=lambda r: (r.priority_score, r.remaining_calendar_days))
    breached = sum(1 for r in routed if r.sla_status == SLAStatus.BREACHED)
    at_risk   = sum(1 for r in routed if r.sla_status == SLAStatus.AT_RISK)
    logger.info(
        "Routed %d DSRs — %d BREACHED, %d AT_RISK, %d ON_TRACK",
        len(routed), breached, at_risk, len(routed) - breached - at_risk,
    )
    return routed


# ---------------------------------------------------------------------------
# Sample queue for CLI demo
# ---------------------------------------------------------------------------
def _sample_queue() -> list[PendingDSR]:
    today = date.today()
    return [
        PendingDSR("dsr-001", "player-A", DSRType.ERASURE,     Jurisdiction.GDPR_EU, today - timedelta(days=28)),
        PendingDSR("dsr-002", "player-B", DSRType.ACCESS,      Jurisdiction.LGPD_BR, today - timedelta(days=18)),
        PendingDSR("dsr-003", "player-C", DSRType.PORTABILITY, Jurisdiction.CCPA_US, today - timedelta(days=5)),
        PendingDSR("dsr-004", "player-D", DSRType.RECTIFY,     Jurisdiction.MGA_MT,  today - timedelta(days=35)),
        PendingDSR("dsr-005", "player-E", DSRType.ERASURE,     Jurisdiction.LGPD_BR, today - timedelta(days=10)),
        PendingDSR("dsr-006", "player-F", DSRType.ACCESS,      Jurisdiction.GDPR_EU, today - timedelta(days=1)),
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Route pending DSR queue by jurisdictional SLA priority.")
    parser.add_argument("--output", default="-", help="Output JSON file (default: stdout)")
    args = parser.parse_args()

    queue = _sample_queue()
    result = route_queue(queue)
    payload = json.dumps([asdict(r) for r in result], indent=2, default=str)

    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        with open(args.output, "w") as fh:
            fh.write(payload + "\n")
        logger.info("Routed queue written to %s", args.output)
