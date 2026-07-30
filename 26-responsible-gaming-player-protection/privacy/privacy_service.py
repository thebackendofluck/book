# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# REGULATORY REQUIREMENT: GDPR + UK GDPR + LGPD + PIPEDA + CCPA
# Regulation:  GDPR (EU) 2016/679 Arts. 15-21 (full suite of data subject rights)
#              UK GDPR + Data Protection Act 2018
#              LGPD (Brazil) Lei No. 13.709/2018 Art. 18
#              PIPEDA S.C. 2000 c.5 Schedule 1 Principle 9
#              CCPA Cal. Civ. Code §§1798.100-1798.120
# Purpose:     Comprehensive privacy rights service covering all GDPR data subject
#              rights: access, erasure, portability, rectification, restriction,
#              and objection. Implements the AML/RG retention exceptions correctly:
#              - Pseudonymisation (not deletion) for AML-retained transaction records
#              - Self-exclusion records retained indefinitely per UKGC/MGA requirement
#              - RG profiling treated as non-objectable (compelling legitimate interest)
#              - AML monitoring treated as non-objectable (legal obligation Art.6(1)(c))
# Key design:  ERASURE uses pseudonymisation, NOT deletion, to comply with BOTH
#              GDPR Art. 17 (right to be forgotten) AND 4AMLD/5AMLD Art. 40
#              (5-year retention of transaction records). GDPR Art. 17(3)(b)
#              explicitly exempts processing required for legal obligations.
#              NOTE: AML retention reference "AMLD6 Art.40" in the code is
#              actually the 4AMLD/5AMLD Art. 40 — the 6AMLD enters into force
#              only on 10 July 2027. Update code references when 6AMLD applies.
# Penalty:     GDPR Art. 83(5): up to €20M or 4% global annual turnover
# Jurisdictions: All EU/EEA, UK, Brazil, Canada
#
# References:
#   GDPR Full Text: https://gdpr-info.eu/
#   Art. 15 (Right of Access): https://gdpr-info.eu/art-15-gdpr/
#   Art. 17 (Right to Erasure): https://gdpr-info.eu/art-17-gdpr/
#   Art. 20 (Data Portability): https://gdpr-info.eu/art-20-gdpr/
#   Art. 83 (Penalties): https://gdpr-info.eu/art-83-gdpr/
#   UK GDPR: https://www.legislation.gov.uk/uksi/2019/419/contents
#   LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/L13709.htm
#   5AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L0843
#   6AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L1673
# =============================================================================
"""
Player Privacy Rights Service — GDPR/UK GDPR/LGPD/PIPEDA/CCPA

Handles Subject Access Requests, erasure, portability, rectification, and
restriction. Each method documents the specific legal provision that creates
the right being exercised.

Legal basis reference:
  GDPR Art.15  — Right of access by the data subject
  GDPR Art.16  — Right to rectification
  GDPR Art.17  — Right to erasure ("right to be forgotten")
  GDPR Art.18  — Right to restriction of processing
  GDPR Art.20  — Right to data portability
  GDPR Art.21  — Right to object
  UK GDPR      — Mirrors GDPR post-Brexit (Data Protection Act 2018)
  LGPD Art.18  — Player rights (Brazilian data subjects)
  PIPEDA Pr.9  — Accuracy and access (Canadian data subjects)
  CCPA §1798   — California Consumer Privacy Act rights
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Jurisdiction definitions
# ---------------------------------------------------------------------------


class Jurisdiction(str, Enum):
    """
    Jurisdictions for which this service implements privacy rights.
    The value is the two- or three-letter ISO territory code used throughout
    the platform.
    """

    GDPR = "GDPR"       # EU/EEA — Malta MGA, Germany GGL, Sweden, Netherlands
    UK_GDPR = "UK_GDPR" # United Kingdom — UKGC
    LGPD = "LGPD"       # Brazil — Secretaria de Prêmios e Apostas
    PIPEDA = "PIPEDA"   # Canada — AGCO (Ontario)
    CCPA = "CCPA"       # California, USA


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


@dataclass
class PrivacyRightResponse:
    """Standard envelope for all privacy right responses."""

    request_id: str
    player_id: str
    right_exercised: str
    legal_basis: str
    jurisdiction: str
    status: str  # "completed" | "partial" | "rejected" | "pending"
    completed_at: str
    payload: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class AccessRequestPayload:
    """
    GDPR Art.15 — The data subject shall have the right to obtain
    confirmation of whether or not personal data are being processed,
    and where that is the case, access to the personal data.
    """

    categories_processed: list[str]
    purposes: list[dict[str, str]]
    legal_bases: list[str]
    retention_periods: dict[str, str]
    recipients: list[str]
    third_country_transfers: list[str]
    data_snapshot: dict[str, Any]
    rights_available: list[str]
    supervisory_authority: str


# ---------------------------------------------------------------------------
# Privacy service
# ---------------------------------------------------------------------------


class PrivacyService:
    """
    Implements all data subject rights across supported jurisdictions.

    WHY THIS CLASS EXISTS:
    GDPR Art.12 requires that operators respond to rights requests without
    undue delay and in any event within one month. This service provides the
    single entry point for all privacy rights requests, routing to the
    appropriate handler based on jurisdiction and right type.

    The service is intentionally stateless — it receives a player_id, calls
    the relevant repositories, and returns a structured response. All database
    writes are handled by specialised handlers (ErasureHandler, ConsentManager).
    """

    def __init__(
        self,
        player_repo: Any,
        transaction_repo: Any,
        rg_repo: Any,
        erasure_handler: Any,
        consent_manager: Any,
        audit_log: Any,
    ) -> None:
        self._players = player_repo
        self._transactions = transaction_repo
        self._rg = rg_repo
        self._erasure = erasure_handler
        self._consent = consent_manager
        self._audit = audit_log

    # ------------------------------------------------------------------
    # Right to Access — GDPR Art.15 / LGPD Art.18(I) / PIPEDA Pr.9
    # WHY: Citizens cannot exercise any other right without first knowing
    # what data an organisation holds about them. This is the gateway right.
    # ------------------------------------------------------------------

    async def handle_access_request(
        self,
        player_id: str,
        jurisdiction: str,
    ) -> PrivacyRightResponse:
        """
        Return all personal data held about the player.

        Legal provisions:
          GDPR Art.15 — 30-day response window; copy must be in intelligible form
          UK GDPR Art.15 — identical; ICO guidance requires structured format
          LGPD Art.18(I/II) — 15-day response under ANPD guidance
          PIPEDA Principle 9 — "reasonable timeframe" (interpreted as 30 days)
          CCPA §1798.100 — 45-day response; specific disclosure categories required

        WHY LGPD requires less time than GDPR: Brazil's ANPD (established 2020)
        set a stricter 15-day window to accelerate rights enforcement in a market
        where data subject awareness was low.
        """
        request_id = str(uuid.uuid4())
        logger.info(
            "SAR received",
            extra={
                "request_id": request_id,
                "player_id": player_id,
                "jurisdiction": jurisdiction,
            },
        )

        player = await self._players.get_by_id(player_id)
        if not player:
            return PrivacyRightResponse(
                request_id=request_id,
                player_id=player_id,
                right_exercised="access",
                legal_basis=_legal_basis_for_access(jurisdiction),
                jurisdiction=jurisdiction,
                status="rejected",
                completed_at=_utcnow(),
                notes=["Player not found — no data held."],
            )

        transactions = await self._transactions.get_all_for_player(player_id)
        rg_history = await self._rg.get_full_history(player_id)
        consent_records = await self._consent.get_all_records(player_id)

        rights = _rights_available_for_jurisdiction(jurisdiction)

        payload = AccessRequestPayload(
            categories_processed=[
                "identity_data",
                "contact_data",
                "financial_data",
                "behavioural_data",
                "health_adjacent_data",  # PGSI scores, RG flags
                "technical_data",        # IP addresses, device identifiers
            ],
            purposes=[
                {
                    "purpose": "Contract performance — account management",
                    "basis": "GDPR Art.6(1)(b)",
                },
                {
                    "purpose": "Legal obligation — AML/KYC compliance",
                    "basis": "GDPR Art.6(1)(c)",
                },
                {
                    "purpose": "Player protection — responsible gaming profiling",
                    "basis": "GDPR Art.6(1)(f) — legitimate interest",
                },
                {
                    "purpose": "Marketing — only where consent given",
                    "basis": "GDPR Art.6(1)(a)",
                },
            ],
            legal_bases=[
                "Contract (Art.6(1)(b))",
                "Legal obligation (Art.6(1)(c))",
                "Legitimate interest (Art.6(1)(f))",
                "Consent (Art.6(1)(a)) — marketing only",
            ],
            retention_periods={
                "identity_and_kyc": "5 years after account closure (AMLD6 Art.40)",
                "transaction_records": "5 years after account closure (AMLD6 Art.40)",
                "responsible_gaming": "5 years after account closure (UKGC LCCP)",
                "self_exclusion": "Duration of exclusion + 5 years",
                "marketing_data": "Until consent withdrawn",
                "technical_logs": "13 months (ICO guidance)",
            },
            recipients=[
                "Payment processors (PCI DSS compliant)",
                "KYC verification providers",
                "National self-exclusion registries (GAMSTOP, Spelpaus, ROFUS)",
                "Regulatory authorities (on lawful request)",
            ],
            third_country_transfers=[
                "Transfer to non-EEA processors covered by Standard Contractual Clauses (SCCs)"
            ],
            data_snapshot={
                "player_profile": _sanitise_profile(player),
                "transactions": transactions,
                "responsible_gaming": rg_history,
                "consent_records": consent_records,
            },
            rights_available=rights,
            supervisory_authority=_supervisory_authority(jurisdiction),
        )

        await self._audit.log(
            event_type="privacy_right_exercised",
            player_id=player_id,
            details={"right": "access", "request_id": request_id, "jurisdiction": jurisdiction},
        )

        return PrivacyRightResponse(
            request_id=request_id,
            player_id=player_id,
            right_exercised="access",
            legal_basis=_legal_basis_for_access(jurisdiction),
            jurisdiction=jurisdiction,
            status="completed",
            completed_at=_utcnow(),
            payload={"access_report": payload.__dict__},
        )

    # ------------------------------------------------------------------
    # Right to Erasure — GDPR Art.17 / LGPD Art.18(VI) / CCPA §1798.105
    # WHY: Individuals should not be permanently defined by their past.
    # The right is NOT absolute — it yields to AML retention obligations.
    # Implementation uses pseudonymisation, NOT deletion.
    # ------------------------------------------------------------------

    async def handle_erasure_request(
        self,
        player_id: str,
        jurisdiction: str,
    ) -> PrivacyRightResponse:
        """
        Pseudonymise PII while retaining AML/regulatory records.

        WHY PSEUDONYMISATION RATHER THAN DELETION:
        GDPR Art.17(3)(b) exempts processing necessary for compliance with a
        legal obligation. AMLD6 Art.40 and UK MLR 2017 Reg.40 impose a 5-year
        retention obligation on transaction records and KYC documents. Full
        deletion would be a criminal AML breach.

        GDPR Recital 26 makes clear that pseudonymised data — where the
        re-identification key has been destroyed — is outside the scope of
        the GDPR. Once PII fields are replaced with one-way hashes and the
        mapping table destroyed, the remaining records cease to be personal
        data and are therefore exempt from the erasure obligation.

        SELF-EXCLUSION EXCEPTION:
        Self-exclusion records are not erased even after pseudonymisation.
        UKGC LCCP and MGA Directive require these records to remain active to
        prevent re-registration. The self-exclusion itself represents the
        player's earlier explicit instruction not to be permitted to gamble —
        erasing it would contradict those instructions.
        """
        request_id = str(uuid.uuid4())
        logger.info(
            "Erasure request received",
            extra={"request_id": request_id, "player_id": player_id, "jurisdiction": jurisdiction},
        )

        result = await self._erasure.pseudonymise(player_id)

        notes = [
            "PII fields replaced with one-way SHA-256 hashes; mapping key destroyed.",
            "Transaction records retained per AMLD6 Art.40 (5-year obligation).",
            "Self-exclusion status retained per UKGC LCCP / MGA Directive.",
            "Responsible gaming flags retained per regulatory audit requirements.",
        ]
        if jurisdiction == Jurisdiction.CCPA:
            notes.append(
                "California statutory exemption applied: CCPA §1798.105(d)(2) "
                "permits retention where required for legal compliance."
            )

        await self._audit.log(
            event_type="privacy_right_exercised",
            player_id=player_id,
            details={
                "right": "erasure",
                "request_id": request_id,
                "jurisdiction": jurisdiction,
                "fields_pseudonymised": result.fields_pseudonymised,
                "fields_retained": result.fields_retained,
            },
        )

        return PrivacyRightResponse(
            request_id=request_id,
            player_id=player_id,
            right_exercised="erasure",
            legal_basis=f"GDPR Art.17 — pseudonymisation; Art.17(3)(b) exception for legal obligation ({jurisdiction})",
            jurisdiction=jurisdiction,
            status="completed",
            completed_at=_utcnow(),
            payload={
                "fields_pseudonymised": result.fields_pseudonymised,
                "fields_retained": result.fields_retained,
                "erasure_certificate": result.certificate,
            },
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Right to Portability — GDPR Art.20 / LGPD Art.18(V)
    # WHY: Enables competition by allowing players to take their history
    # (including RG history) to another operator.
    # ------------------------------------------------------------------

    async def handle_portability_request(
        self,
        player_id: str,
    ) -> bytes:
        """
        Export all player data in machine-readable JSON format.

        GDPR Art.20 applies where:
          (a) processing is based on consent or contract, AND
          (b) processing is carried out by automated means.

        Note: portability does NOT apply to data processed on the basis of
        legal obligation (e.g., AML transaction records). The export covers
        profile data, preference data, and data actively provided by the player.

        WHY JSON: Art.20(1) requires a "structured, commonly used and
        machine-readable format". JSON satisfies this. The LGPD Art.18(V)
        uses the same language, so a single export format serves both.
        """
        from .sar_export import SARExporter  # type: ignore[import]

        exporter = SARExporter(self._players, self._transactions, self._rg, self._consent)
        data = await exporter.build_export(player_id)

        await self._audit.log(
            event_type="privacy_right_exercised",
            player_id=player_id,
            details={"right": "portability"},
        )

        return json.dumps(data, indent=2, default=str).encode("utf-8")

    # ------------------------------------------------------------------
    # Right to Rectification — GDPR Art.16 / LGPD Art.18(III)
    # WHY: Inaccurate data causes concrete harm — incorrect KYC can lock
    # players out of their accounts; wrong problem gambling flags can
    # trigger unjustified restrictions.
    # ------------------------------------------------------------------

    async def handle_rectification(
        self,
        player_id: str,
        field: str,
        new_value: str,
    ) -> PrivacyRightResponse:
        """
        Correct inaccurate personal data.

        GDPR Art.16 requires incomplete data to be completed, taking into
        account the purposes of the processing. This is subject to identity
        verification — the operator must confirm the requester is the player
        before modifying data.

        COMPLIANCE NOTE: Changes to KYC fields (name, date of birth, address)
        must trigger a re-verification cycle. Simply accepting self-reported
        corrections for KYC-material fields would undermine AML controls.
        """
        request_id = str(uuid.uuid4())

        _IMMUTABLE_FIELDS = {"player_id", "registration_date", "kyc_verification_date"}
        _KYC_FIELDS = {"name", "date_of_birth", "nationality", "address"}

        if field in _IMMUTABLE_FIELDS:
            return PrivacyRightResponse(
                request_id=request_id,
                player_id=player_id,
                right_exercised="rectification",
                legal_basis="GDPR Art.16",
                jurisdiction="GDPR",
                status="rejected",
                completed_at=_utcnow(),
                notes=[f"Field '{field}' is an immutable audit field and cannot be modified."],
            )

        requires_reverification = field in _KYC_FIELDS

        await self._players.update_field(player_id, field, new_value)

        await self._audit.log(
            event_type="privacy_right_exercised",
            player_id=player_id,
            details={
                "right": "rectification",
                "request_id": request_id,
                "field": field,
                "requires_reverification": requires_reverification,
            },
        )

        notes = [f"Field '{field}' updated successfully."]
        if requires_reverification:
            notes.append(
                "KYC field modified — re-verification required before "
                "account limits can be increased."
            )

        return PrivacyRightResponse(
            request_id=request_id,
            player_id=player_id,
            right_exercised="rectification",
            legal_basis="GDPR Art.16",
            jurisdiction="GDPR",
            status="completed",
            completed_at=_utcnow(),
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Right to Restriction — GDPR Art.18
    # WHY: Provides a procedural pause during disputes without requiring
    # full deletion. The player's account data is frozen in place while
    # the dispute is resolved.
    # ------------------------------------------------------------------

    async def handle_restrict(
        self,
        player_id: str,
        scope: str,
    ) -> PrivacyRightResponse:
        """
        Restrict processing during a dispute or contested accuracy claim.

        GDPR Art.18 applies when:
          (a) the accuracy of data is contested (pending verification period)
          (b) processing is unlawful but the data subject opposes erasure
          (c) the controller no longer needs the data but the data subject
              requires it for legal claims
          (d) the data subject has objected (Art.21) pending verification
              of the controller's legitimate grounds

        WHY THIS IS DIFFERENT FROM ERASURE:
        Restriction freezes the data in place — it is still held but may
        only be processed for specific purposes (storage, legal claims,
        protecting rights of another person). This is the correct remedy
        when the dispute is about whether data is accurate, not whether
        it should exist at all.

        scope: "all" | "marketing" | "profiling" | "analytics"
        """
        request_id = str(uuid.uuid4())

        await self._players.set_processing_restriction(player_id, scope)

        await self._audit.log(
            event_type="privacy_right_exercised",
            player_id=player_id,
            details={"right": "restriction", "request_id": request_id, "scope": scope},
        )

        return PrivacyRightResponse(
            request_id=request_id,
            player_id=player_id,
            right_exercised="restriction",
            legal_basis="GDPR Art.18",
            jurisdiction="GDPR",
            status="completed",
            completed_at=_utcnow(),
            notes=[
                f"Processing restricted for scope: '{scope}'.",
                "Data is retained but processing is suspended pending resolution.",
                "Core account functions (withdrawals) are unaffected.",
            ],
        )

    # ------------------------------------------------------------------
    # Right to Object — GDPR Art.21 / LGPD Art.18(IV) / CCPA §1798.120
    # ------------------------------------------------------------------

    async def handle_objection(
        self,
        player_id: str,
        processing_purpose: str,
    ) -> PrivacyRightResponse:
        """
        Process an objection to specific processing activities.

        GDPR Art.21 grants an absolute right to object to direct marketing.
        For other legitimate-interest processing, the controller may override
        the objection if compelling legitimate grounds are demonstrated.

        WHY RESPONSIBLE GAMING PROFILING IS NOT OBJECTABLE IN ABSOLUTE TERMS:
        Art.21(1) permits the controller to demonstrate "compelling legitimate
        grounds for the processing which override the interests, rights and
        freedoms of the data subject". Preventing gambling harm is such a
        ground. This argument is documented in the platform's legitimate
        interest assessment.
        """
        request_id = str(uuid.uuid4())

        _ABSOLUTE_OBJECTION_PURPOSES = {"marketing", "profiling_for_commercial"}
        _NON_OBJECTABLE_PURPOSES = {
            "responsible_gaming_profiling": (
                "Legitimate interest for player harm prevention overrides "
                "objection right. UKGC LCCP and MGA Directive require this "
                "processing. See Legitimate Interest Assessment v1.2."
            ),
            "aml_monitoring": (
                "Processing is required by legal obligation (AMLD6). "
                "Art.21 does not apply to Art.6(1)(c) processing."
            ),
        }

        if processing_purpose in _NON_OBJECTABLE_PURPOSES:
            reason = _NON_OBJECTABLE_PURPOSES[processing_purpose]
            await self._audit.log(
                event_type="privacy_right_exercised",
                player_id=player_id,
                details={
                    "right": "objection",
                    "request_id": request_id,
                    "purpose": processing_purpose,
                    "outcome": "overridden",
                },
            )
            return PrivacyRightResponse(
                request_id=request_id,
                player_id=player_id,
                right_exercised="objection",
                legal_basis="GDPR Art.21(1) — overridden by compelling legitimate grounds",
                jurisdiction="GDPR",
                status="rejected",
                completed_at=_utcnow(),
                notes=[reason],
            )

        if processing_purpose in _ABSOLUTE_OBJECTION_PURPOSES:
            await self._consent.revoke_for_purpose(player_id, processing_purpose)
            await self._audit.log(
                event_type="privacy_right_exercised",
                player_id=player_id,
                details={
                    "right": "objection",
                    "request_id": request_id,
                    "purpose": processing_purpose,
                    "outcome": "granted",
                },
            )
            return PrivacyRightResponse(
                request_id=request_id,
                player_id=player_id,
                right_exercised="objection",
                legal_basis="GDPR Art.21(2) — absolute right to object to direct marketing",
                jurisdiction="GDPR",
                status="completed",
                completed_at=_utcnow(),
                notes=[f"Processing for '{processing_purpose}' stopped immediately."],
            )

        # Default: investigate and respond within 30 days
        return PrivacyRightResponse(
            request_id=request_id,
            player_id=player_id,
            right_exercised="objection",
            legal_basis="GDPR Art.21(1)",
            jurisdiction="GDPR",
            status="pending",
            completed_at=_utcnow(),
            notes=[
                f"Objection to '{processing_purpose}' received. "
                "Under investigation — response within 30 days."
            ],
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _legal_basis_for_access(jurisdiction: str) -> str:
    mapping = {
        Jurisdiction.GDPR: "GDPR Art.15",
        Jurisdiction.UK_GDPR: "UK GDPR Art.15 (Data Protection Act 2018 s.45)",
        Jurisdiction.LGPD: "LGPD Art.18(I) and Art.18(II)",
        Jurisdiction.PIPEDA: "PIPEDA Schedule 1, Principle 9",
        Jurisdiction.CCPA: "CCPA §1798.100",
    }
    return mapping.get(jurisdiction, "GDPR Art.15")  # type: ignore[call-overload]


def _supervisory_authority(jurisdiction: str) -> str:
    mapping = {
        Jurisdiction.GDPR: "Relevant EU supervisory authority (e.g., IDPC Malta, EDPB)",
        Jurisdiction.UK_GDPR: "Information Commissioner's Office (ICO) — ico.org.uk",
        Jurisdiction.LGPD: "Autoridade Nacional de Proteção de Dados (ANPD) — gov.br/anpd",
        Jurisdiction.PIPEDA: "Office of the Privacy Commissioner of Canada — priv.gc.ca",
        Jurisdiction.CCPA: "California Privacy Protection Agency (CPPA) — cppa.ca.gov",
    }
    return mapping.get(jurisdiction, "Relevant supervisory authority")  # type: ignore[call-overload]


def _rights_available_for_jurisdiction(jurisdiction: str) -> list[str]:
    base = ["access", "rectification", "withdrawal_of_consent"]
    gdpr_full = base + ["erasure", "portability", "restriction", "objection", "no_automated_profiling"]

    mapping: dict[str, list[str]] = {
        Jurisdiction.GDPR: gdpr_full,
        Jurisdiction.UK_GDPR: gdpr_full,
        Jurisdiction.LGPD: base + ["erasure", "portability", "objection", "no_automated_profiling"],
        Jurisdiction.PIPEDA: base + ["accuracy_correction"],
        Jurisdiction.CCPA: base + ["erasure", "opt_out_of_sale", "no_discrimination"],
    }
    return mapping.get(jurisdiction, base)  # type: ignore[call-overload]


def _sanitise_profile(player: Any) -> dict[str, Any]:
    """Return player profile dict, excluding internal system fields."""
    if hasattr(player, "__dict__"):
        data = player.__dict__.copy()
    elif isinstance(player, dict):
        data = player.copy()
    else:
        return {}
    # Remove internal hashes and system keys not meaningful to the player
    for key in ("_hash", "_version", "_row_hash"):
        data.pop(key, None)
    return data
