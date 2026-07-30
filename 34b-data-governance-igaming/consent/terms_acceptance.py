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
Terms Acceptance — Version-Controlled Legal Document Lifecycle.

Regulatory References:
  - GDPR (EU) 2016/679, Art. 7 (conditions for consent), Art. 13/14 (notice)
  - ePrivacy Directive 2002/58/EC, Art. 5(3) (cookie consent record-keeping)
  - UKGC Licence Conditions (LCCP) 7.1.1 — T&C acceptance at registration
  - MGA (Malta) Player Protection Directive 2018, Art. 14 — acceptance audit
  - LGPD (Lei 13.709/2018), Arts. 8, 9 — informed and specific consent
  - Responsible Gambling (RG) Policy: GamCare, BeGambleAware standards

SQL Schema (PostgreSQL)
-----------------------
The following DDL creates the canonical persistence table::

    CREATE SCHEMA IF NOT EXISTS governance;

    CREATE TABLE governance.terms_acceptance_log (
        id              BIGSERIAL PRIMARY KEY,
        player_id       UUID        NOT NULL,
        doc_type        TEXT        NOT NULL,          -- e.g. 'tc', 'privacy', 'cookie', 'rg'
        version         TEXT        NOT NULL,          -- semantic version e.g. '3.1.0'
        content_hash    CHAR(64)    NOT NULL,          -- SHA-256 hex of document body
        accepted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ip_address      INET        NOT NULL,
        user_agent      TEXT,
        method          TEXT        NOT NULL,          -- 'explicit_checkbox', 'api_call', 'migration'
        withdrawn_at    TIMESTAMPTZ,
        CONSTRAINT uq_player_doc_version UNIQUE (player_id, doc_type, version)
    );

    CREATE INDEX idx_tal_player ON governance.terms_acceptance_log (player_id);
    CREATE INDEX idx_tal_doc_type ON governance.terms_acceptance_log (doc_type, version);
"""

from __future__ import annotations

import argparse
import hashlib
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


class DocType(str, Enum):
    TC = "tc"                          # Terms & Conditions
    PRIVACY = "privacy"                # Privacy Policy
    COOKIE = "cookie"                  # Cookie Policy
    RG = "rg"                          # Responsible Gambling Policy
    BONUS = "bonus"                    # Bonus Terms
    AMLKYC = "aml_kyc"                 # AML / KYC Policy


class AcceptanceMethod(str, Enum):
    EXPLICIT_CHECKBOX = "explicit_checkbox"    # Player ticked a checkbox
    API_CALL = "api_call"                      # Programmatic acceptance (mobile SDK)
    MIGRATION = "migration"                    # Bulk migration of legacy records
    RE_ACCEPT_BANNER = "re_accept_banner"      # Re-acceptance banner acknowledged


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LegalDocument:
    """Represents a versioned legal document."""

    doc_type: DocType
    version: str                     # Semantic version, e.g. "3.1.0"
    effective_date: datetime
    content_hash: str                # SHA-256 hex digest of canonical document body
    supersedes_version: Optional[str] = None
    requires_reaccept: bool = True   # If True, all players must re-accept on upgrade


@dataclass
class PlayerAcceptance:
    """Single acceptance event for a player and document version."""

    acceptance_id: str
    player_id: str
    doc_type: DocType
    version: str
    content_hash: str
    accepted_at: datetime
    ip_address: str
    user_agent: str
    method: AcceptanceMethod
    withdrawn_at: Optional[datetime] = None


@dataclass
class AcceptanceCheckResult:
    """Result of checking whether a player must re-accept a document."""

    player_id: str
    doc_type: DocType
    current_version: str
    player_version: Optional[str]
    reaccept_required: bool
    reason: str


# ---------------------------------------------------------------------------
# In-memory registry (replace with DB layer in production)
# ---------------------------------------------------------------------------

# doc_type -> LegalDocument (latest live version)
_DOCUMENT_REGISTRY: dict[DocType, LegalDocument] = {}

# player_id -> list[PlayerAcceptance]
_ACCEPTANCE_STORE: dict[str, list[PlayerAcceptance]] = {}


def register_document(doc: LegalDocument) -> None:
    """Register or replace a legal document in the in-memory registry."""
    _DOCUMENT_REGISTRY[doc.doc_type] = doc
    logger.info(
        "Registered document: %s v%s (hash=%s)",
        doc.doc_type.value,
        doc.version,
        doc.content_hash[:12],
    )


def sha256_content(content: str) -> str:
    """Return hex SHA-256 digest of a document's canonical string body."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def check_reaccept_required(player_id: str, doc_type: DocType) -> AcceptanceCheckResult:
    """Check whether a player must re-accept the current version of a document.

    Args:
        player_id: Unique player identifier (UUID string).
        doc_type: The :class:`DocType` to check.

    Returns:
        :class:`AcceptanceCheckResult` describing whether re-acceptance is needed.

    Raises:
        KeyError: If the document type is not registered.
    """
    current_doc = _DOCUMENT_REGISTRY[doc_type]
    acceptances = _ACCEPTANCE_STORE.get(player_id, [])

    # Find the most recent active acceptance for this doc type
    active = [
        a for a in acceptances
        if a.doc_type == doc_type and a.withdrawn_at is None
    ]
    active_sorted = sorted(active, key=lambda a: a.accepted_at, reverse=True)

    if not active_sorted:
        return AcceptanceCheckResult(
            player_id=player_id,
            doc_type=doc_type,
            current_version=current_doc.version,
            player_version=None,
            reaccept_required=True,
            reason="No acceptance record found",
        )

    latest = active_sorted[0]
    if latest.version != current_doc.version:
        return AcceptanceCheckResult(
            player_id=player_id,
            doc_type=doc_type,
            current_version=current_doc.version,
            player_version=latest.version,
            reaccept_required=current_doc.requires_reaccept,
            reason=(
                f"Player accepted v{latest.version}, current is v{current_doc.version}"
            ),
        )

    return AcceptanceCheckResult(
        player_id=player_id,
        doc_type=doc_type,
        current_version=current_doc.version,
        player_version=latest.version,
        reaccept_required=False,
        reason="Player has accepted current version",
    )


def record_acceptance(
    player_id: str,
    doc_type: DocType,
    version: str,
    ip: str,
    user_agent: str,
    method: AcceptanceMethod,
) -> PlayerAcceptance:
    """Record a player's acceptance of a legal document version.

    Args:
        player_id: UUID string identifying the player.
        doc_type: Document category.
        version: Semantic version string the player accepted.
        ip: IPv4 or IPv6 address of the player at acceptance time.
        user_agent: HTTP User-Agent header string.
        method: How acceptance was captured.

    Returns:
        Persisted :class:`PlayerAcceptance` record.

    Raises:
        KeyError: If doc_type is not registered.
        ValueError: If version does not match the registered document.
    """
    current_doc = _DOCUMENT_REGISTRY[doc_type]
    if current_doc.version != version:
        raise ValueError(
            f"Version mismatch: requested {version}, registered is {current_doc.version}"
        )

    acceptance = PlayerAcceptance(
        acceptance_id=str(uuid.uuid4()),
        player_id=player_id,
        doc_type=doc_type,
        version=version,
        content_hash=current_doc.content_hash,
        accepted_at=datetime.now(timezone.utc),
        ip_address=ip,
        user_agent=user_agent,
        method=method,
    )

    if player_id not in _ACCEPTANCE_STORE:
        _ACCEPTANCE_STORE[player_id] = []
    _ACCEPTANCE_STORE[player_id].append(acceptance)

    logger.info(
        "Acceptance recorded: player=%s doc=%s v=%s method=%s",
        player_id,
        doc_type.value,
        version,
        method.value,
    )
    return acceptance


def get_acceptance_history(player_id: str) -> list[PlayerAcceptance]:
    """Return full acceptance history for a player, newest first.

    Args:
        player_id: UUID string identifying the player.

    Returns:
        List of :class:`PlayerAcceptance` records sorted descending by
        accepted_at.
    """
    records = _ACCEPTANCE_STORE.get(player_id, [])
    return sorted(records, key=lambda a: a.accepted_at, reverse=True)


def withdraw_acceptance(player_id: str, doc_type: DocType) -> int:
    """Mark all active acceptances for a doc_type as withdrawn.

    Args:
        player_id: UUID string.
        doc_type: The document type to withdraw.

    Returns:
        Number of records updated.
    """
    now = datetime.now(timezone.utc)
    count = 0
    for record in _ACCEPTANCE_STORE.get(player_id, []):
        if record.doc_type == doc_type and record.withdrawn_at is None:
            record.withdrawn_at = now
            count += 1
    if count:
        logger.info(
            "Withdrew %d acceptance(s) for player=%s doc=%s",
            count,
            player_id,
            doc_type.value,
        )
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _seed_demo_data() -> str:
    """Seed registry with demo documents and return a test player ID."""
    now = datetime.now(timezone.utc)
    for doc_type, version, body in [
        (DocType.TC, "4.0.0", "Terms and Conditions v4.0.0 — Acme Casino"),
        (DocType.PRIVACY, "2.3.0", "Privacy Policy v2.3.0 — Acme Casino"),
        (DocType.COOKIE, "1.5.0", "Cookie Policy v1.5.0 — Acme Casino"),
        (DocType.RG, "3.0.0", "Responsible Gambling Policy v3.0.0 — Acme Casino"),
    ]:
        register_document(
            LegalDocument(
                doc_type=doc_type,
                version=version,
                effective_date=now,
                content_hash=sha256_content(body),
                requires_reaccept=True,
            )
        )
    return str(uuid.uuid4())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Demo: terms acceptance lifecycle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    player_id = _seed_demo_data()
    print(f"Demo player: {player_id}\n")

    # Check before acceptance
    for dt in [DocType.TC, DocType.PRIVACY, DocType.COOKIE, DocType.RG]:
        result = check_reaccept_required(player_id, dt)
        print(f"  [{dt.value}] reaccept_required={result.reaccept_required} reason={result.reason!r}")

    print()

    # Record acceptance of TC and Privacy
    for dt, version in [(DocType.TC, "4.0.0"), (DocType.PRIVACY, "2.3.0")]:
        rec = record_acceptance(
            player_id, dt, version,
            ip="198.51.100.42",
            user_agent="Mozilla/5.0 (iGaming Demo)",
            method=AcceptanceMethod.EXPLICIT_CHECKBOX,
        )
        print(f"  Recorded: {dt.value} v{version} id={rec.acceptance_id[:8]}...")

    print("\nAcceptance history:")
    for entry in get_acceptance_history(player_id):
        ts = entry.accepted_at.isoformat()
        print(f"  {ts}  {entry.doc_type.value} v{entry.version}  method={entry.method.value}")

    history_json = [
        {
            "doc_type": e.doc_type.value,
            "version": e.version,
            "accepted_at": e.accepted_at.isoformat(),
            "method": e.method.value,
        }
        for e in get_acceptance_history(player_id)
    ]
    print("\nJSON export:")
    print(json.dumps(history_json, indent=2))
