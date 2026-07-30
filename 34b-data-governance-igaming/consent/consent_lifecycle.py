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
Consent Lifecycle State Machine — Unified Cookie, Terms & Marketing Consent.

Regulatory References:
  - GDPR (EU) 2016/679, Arts. 4(11), 7, 17 — consent, conditions, right to withdraw
  - ePrivacy Directive 2002/58/EC, Art. 5(3) — cookie consent withdrawal
  - LGPD (Lei 13.709/2018), Art. 8 §5 — consent withdrawal at any time, cost-free
  - ICO PECR (SI 2003/2426), Regulation 6 — cookie consent requirements (UK)
  - EDPB Guidelines 05/2020 on consent — granularity, freely given, unambiguous

State Machine:
  NOT_GIVEN ──grant──► ACTIVE ──withdraw──► WITHDRAWN
  ACTIVE ──trigger_reaccept──► PENDING_REACCEPT ──grant──► ACTIVE

All events are appended to an immutable audit log; no records are updated in-place.
The current state is derived from the log at query time (event sourcing pattern).

Purpose taxonomy:
  - cookie:<category>      e.g. cookie:analytics, cookie:marketing
  - terms:<doc_type>       e.g. terms:tc, terms:privacy
  - marketing:<channel>    e.g. marketing:email, marketing:sms, marketing:push
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConsentState(str, Enum):
    NOT_GIVEN = "not_given"
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
    PENDING_REACCEPT = "pending_reaccept"


class ConsentEventType(str, Enum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"
    REACCEPT_TRIGGERED = "reaccept_triggered"
    REACCEPTED = "reaccepted"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ConsentEvent:
    """An immutable consent event appended to the audit log."""

    event_id: str
    player_id: str
    purpose: str              # e.g. "cookie:analytics", "terms:tc", "marketing:email"
    event_type: ConsentEventType
    version: str              # Document/policy version consented to
    timestamp: datetime
    ip_address: str
    user_agent: str
    jurisdiction: str         # ISO jurisdiction code at time of event


@dataclass
class ConsentStatus:
    """Current derived state for a single (player, purpose) pair."""

    player_id: str
    purpose: str
    state: ConsentState
    version: Optional[str]              # Version currently in force
    last_event_at: Optional[datetime]
    pending_version: Optional[str]      # For PENDING_REACCEPT state


@dataclass
class PlayerConsentSummary:
    """Aggregated current consent state for all purposes of a player."""

    player_id: str
    jurisdiction: str
    as_of: datetime
    purposes: dict[str, ConsentStatus] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Global immutable event log (append-only)
# ---------------------------------------------------------------------------

# player_id -> list[ConsentEvent] (ordered by timestamp)
_EVENT_LOG: dict[str, list[ConsentEvent]] = {}

# Pending reaccept registry: purpose -> new_version
_PENDING_REACCEPT: dict[str, str] = {}


def _append_event(event: ConsentEvent) -> None:
    """Append an event to the immutable log."""
    if event.player_id not in _EVENT_LOG:
        _EVENT_LOG[event.player_id] = []
    _EVENT_LOG[event.player_id].append(event)
    logger.debug(
        "Event appended: %s player=%s purpose=%s v=%s",
        event.event_type.value,
        event.player_id,
        event.purpose,
        event.version,
    )


# ---------------------------------------------------------------------------
# State derivation
# ---------------------------------------------------------------------------


def _derive_state(player_id: str, purpose: str) -> ConsentStatus:
    """Derive current consent state for (player_id, purpose) from the event log."""
    events = [
        e for e in _EVENT_LOG.get(player_id, [])
        if e.purpose == purpose
    ]
    events_sorted = sorted(events, key=lambda e: e.timestamp)

    if not events_sorted:
        return ConsentStatus(
            player_id=player_id,
            purpose=purpose,
            state=ConsentState.NOT_GIVEN,
            version=None,
            last_event_at=None,
            pending_version=None,
        )

    latest = events_sorted[-1]

    if latest.event_type == ConsentEventType.GRANTED:
        state = ConsentState.ACTIVE
        # Check if a reaccept was triggered after this grant
        pending = _PENDING_REACCEPT.get(purpose)
        if pending and pending != latest.version:
            state = ConsentState.PENDING_REACCEPT
        return ConsentStatus(
            player_id=player_id,
            purpose=purpose,
            state=state,
            version=latest.version,
            last_event_at=latest.timestamp,
            pending_version=pending if state == ConsentState.PENDING_REACCEPT else None,
        )

    if latest.event_type == ConsentEventType.REACCEPTED:
        return ConsentStatus(
            player_id=player_id,
            purpose=purpose,
            state=ConsentState.ACTIVE,
            version=latest.version,
            last_event_at=latest.timestamp,
            pending_version=None,
        )

    if latest.event_type == ConsentEventType.REACCEPT_TRIGGERED:
        return ConsentStatus(
            player_id=player_id,
            purpose=purpose,
            state=ConsentState.PENDING_REACCEPT,
            version=latest.version,
            last_event_at=latest.timestamp,
            pending_version=_PENDING_REACCEPT.get(purpose),
        )

    # WITHDRAWN
    return ConsentStatus(
        player_id=player_id,
        purpose=purpose,
        state=ConsentState.WITHDRAWN,
        version=latest.version,
        last_event_at=latest.timestamp,
        pending_version=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def grant_consent(
    player_id: str,
    purpose: str,
    version: str,
    ip: str = "0.0.0.0",
    user_agent: str = "",
    jurisdiction: str = "EU",
) -> ConsentEvent:
    """Grant consent for a given purpose and version.

    Transitions:
      NOT_GIVEN → ACTIVE
      WITHDRAWN → ACTIVE
      PENDING_REACCEPT → ACTIVE (treated as re-acceptance)

    Args:
        player_id: UUID string.
        purpose: Consent purpose string (e.g. ``"cookie:analytics"``).
        version: Version of the policy being accepted.
        ip: Player's IP address at consent time.
        user_agent: HTTP User-Agent string.
        jurisdiction: ISO jurisdiction code.

    Returns:
        The created :class:`ConsentEvent`.

    Raises:
        ValueError: If current state is ACTIVE for the same version.
    """
    current = _derive_state(player_id, purpose)
    if current.state == ConsentState.ACTIVE and current.version == version:
        raise ValueError(
            f"Consent already ACTIVE for purpose={purpose} version={version}"
        )

    pending = _PENDING_REACCEPT.get(purpose)
    event_type = (
        ConsentEventType.REACCEPTED
        if pending and pending == version
        else ConsentEventType.GRANTED
    )

    event = ConsentEvent(
        event_id=str(uuid.uuid4()),
        player_id=player_id,
        purpose=purpose,
        event_type=event_type,
        version=version,
        timestamp=datetime.now(timezone.utc),
        ip_address=ip,
        user_agent=user_agent,
        jurisdiction=jurisdiction,
    )
    _append_event(event)
    logger.info("Consent granted: player=%s purpose=%s v=%s", player_id, purpose, version)
    return event


def withdraw_consent(
    player_id: str,
    purpose: str,
    ip: str = "0.0.0.0",
    user_agent: str = "",
    jurisdiction: str = "EU",
) -> ConsentEvent:
    """Withdraw consent for a given purpose.

    Transition: ACTIVE → WITHDRAWN (GDPR Art. 7(3), LGPD Art. 8 §5).

    Args:
        player_id: UUID string.
        purpose: Consent purpose string.
        ip: Player's IP address at withdrawal time.
        user_agent: HTTP User-Agent string.
        jurisdiction: ISO jurisdiction code.

    Returns:
        The created :class:`ConsentEvent`.

    Raises:
        ValueError: If current state is NOT_GIVEN or already WITHDRAWN.
    """
    current = _derive_state(player_id, purpose)
    if current.state in (ConsentState.NOT_GIVEN, ConsentState.WITHDRAWN):
        raise ValueError(
            f"Cannot withdraw — current state is {current.state.value} for purpose={purpose}"
        )

    event = ConsentEvent(
        event_id=str(uuid.uuid4()),
        player_id=player_id,
        purpose=purpose,
        event_type=ConsentEventType.WITHDRAWN,
        version=current.version or "unknown",
        timestamp=datetime.now(timezone.utc),
        ip_address=ip,
        user_agent=user_agent,
        jurisdiction=jurisdiction,
    )
    _append_event(event)
    logger.info("Consent withdrawn: player=%s purpose=%s", player_id, purpose)
    return event


def get_consent_state(player_id: str) -> PlayerConsentSummary:
    """Return aggregated consent state for all purposes of a player.

    Args:
        player_id: UUID string.

    Returns:
        :class:`PlayerConsentSummary` with derived state per purpose.
    """
    events = _EVENT_LOG.get(player_id, [])
    purposes_seen: set[str] = {e.purpose for e in events}

    jurisdiction = "UNKNOWN"
    if events:
        latest_event = max(events, key=lambda e: e.timestamp)
        jurisdiction = latest_event.jurisdiction

    statuses: dict[str, ConsentStatus] = {
        p: _derive_state(player_id, p) for p in purposes_seen
    }

    return PlayerConsentSummary(
        player_id=player_id,
        jurisdiction=jurisdiction,
        as_of=datetime.now(timezone.utc),
        purposes=statuses,
    )


def trigger_reaccept(doc_type: str, new_version: str) -> int:
    """Mark all active players as PENDING_REACCEPT for a purpose after a policy update.

    This does NOT write per-player events; the PENDING_REACCEPT state is derived
    lazily when :func:`get_consent_state` is called, by comparing the active
    version against the pending registry entry.

    Args:
        doc_type: Purpose prefix, e.g. ``"terms:tc"`` or ``"cookie:analytics"``.
        new_version: The new policy version that requires re-acceptance.

    Returns:
        Number of players currently ACTIVE for this purpose who will be
        transitioned to PENDING_REACCEPT on next state query.
    """
    _PENDING_REACCEPT[doc_type] = new_version
    affected = sum(
        1
        for player_id in _EVENT_LOG
        if _derive_state(player_id, doc_type).state == ConsentState.ACTIVE
    )
    logger.info(
        "Reaccept triggered: purpose=%s new_version=%s affected_players=%d",
        doc_type,
        new_version,
        affected,
    )
    return affected


def get_event_log(player_id: str) -> list[ConsentEvent]:
    """Return full immutable event log for a player, oldest first."""
    return sorted(
        _EVENT_LOG.get(player_id, []),
        key=lambda e: e.timestamp,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Demo: unified consent lifecycle state machine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--jurisdiction", default="EU",
        help="Jurisdiction code for demo events (default: EU)",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    player_id = str(uuid.uuid4())
    jur = args.jurisdiction
    ip = "198.51.100.7"
    ua = "Mozilla/5.0 (iGaming Demo)"

    print(f"Player: {player_id}")
    print(f"Jurisdiction: {jur}\n")

    # Grant cookie and terms consents
    purposes_to_grant = [
        ("cookie:necessary", "1.0.0"),
        ("cookie:analytics", "1.0.0"),
        ("terms:tc", "4.0.0"),
        ("terms:privacy", "2.3.0"),
        ("marketing:email", "1.0.0"),
    ]
    for purpose, version in purposes_to_grant:
        grant_consent(player_id, purpose, version, ip=ip, user_agent=ua, jurisdiction=jur)

    summary = get_consent_state(player_id)
    print("State after initial grants:")
    for purpose, status in sorted(summary.purposes.items()):
        print(f"  {purpose:35s} {status.state.value:20s} v={status.version}")

    # Withdraw marketing consent
    print("\nWithdrawing marketing:email...")
    withdraw_consent(player_id, "marketing:email", ip=ip, user_agent=ua, jurisdiction=jur)

    # Trigger reaccept for T&C update
    print("Triggering T&C reaccept for v4.1.0...")
    affected = trigger_reaccept("terms:tc", "4.1.0")
    print(f"  Players pending reaccept: {affected}")

    summary2 = get_consent_state(player_id)
    print("\nState after withdraw and reaccept trigger:")
    for purpose, status in sorted(summary2.purposes.items()):
        pv = f"v={status.version}"
        pend = f" pending={status.pending_version}" if status.pending_version else ""
        print(f"  {purpose:35s} {status.state.value:20s} {pv}{pend}")

    # Re-accept T&C
    print("\nRe-accepting terms:tc v4.1.0...")
    grant_consent(player_id, "terms:tc", "4.1.0", ip=ip, user_agent=ua, jurisdiction=jur)

    # Print full event log as JSON
    log_json = [
        {
            "event_id": e.event_id[:8],
            "purpose": e.purpose,
            "event_type": e.event_type.value,
            "version": e.version,
            "timestamp": e.timestamp.isoformat(),
            "jurisdiction": e.jurisdiction,
        }
        for e in get_event_log(player_id)
    ]
    print("\nFull event log:")
    print(json.dumps(log_json, indent=2))
