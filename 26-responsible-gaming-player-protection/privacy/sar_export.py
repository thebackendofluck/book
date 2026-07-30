# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Subject Access Request (SAR) Export — GDPR Art.20 Portability

WHY THIS MODULE EXISTS:
GDPR Art.20 grants the right to receive personal data in a structured,
commonly used, and machine-readable format. Unlike the access right (Art.15),
portability applies specifically to data that:
  (a) was provided by the data subject, AND
  (b) is processed on the basis of consent or contract, AND
  (c) is processed by automated means.

This means AML transaction records (processed on legal obligation basis) are
NOT included in portability exports. They ARE included in access (Art.15)
exports but with clear labelling of the legal retention basis.

WHY JSON:
The ICO and EDPB have both confirmed that JSON satisfies "structured, commonly
used, and machine-readable" format. The alternative (CSV) is less suitable for
nested data structures such as transaction histories and responsible gaming
records.

LGPD Art.18(V) uses identical language to GDPR Art.20 — a single export format
serves both EU/UK and Brazilian data subjects.

PORTABILITY TO ANOTHER OPERATOR:
The RG history section is intentionally included in portability exports to
enable a player to share their responsible gaming history (including any
self-exclusion periods) with a new operator. This supports harm prevention
even after a player leaves the platform.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any


class SARExporter:
    """
    Builds a complete Subject Access Request / portability export for a player.

    The export is structured into five sections:
      1. account_profile    — identity and contact data (Art.15 + Art.20)
      2. financial_data     — transaction history (Art.15 only — legal obligation basis)
      3. gaming_activity    — session and wagering history (Art.15 + Art.20)
      4. responsible_gaming — RG interventions, limits, PGSI scores (Art.15 + Art.20)
      5. consent_records    — marketing and analytics consent history (Art.15 + Art.20)

    Sections marked "Art.20 eligible" can be transmitted directly to another
    operator at the data subject's request (GDPR Art.20(2)).
    """

    _EXPORT_VERSION = "2.0"

    def __init__(
        self,
        player_repo: Any,
        transaction_repo: Any,
        rg_repo: Any,
        consent_manager: Any,
    ) -> None:
        self._players = player_repo
        self._transactions = transaction_repo
        self._rg = rg_repo
        self._consent = consent_manager

    async def build_export(self, player_id: str) -> dict[str, Any]:
        """
        Assemble the complete data export package.

        Returns a dict that can be serialised to JSON and delivered to the
        player as a downloadable file, or transmitted to another controller
        under Art.20(2).
        """
        player = await self._players.get_by_id(player_id)
        if not player:
            raise ValueError(f"No data found for player {player_id}")

        profile = player if isinstance(player, dict) else player.__dict__
        transactions = await self._transactions.get_all_for_player(player_id)
        rg_history = await self._rg.get_full_history(player_id)
        consent_records = await self._consent.get_all_records(player_id)

        return {
            "export_metadata": self._build_metadata(player_id),
            "account_profile": self._build_profile_section(profile),
            "financial_data": self._build_financial_section(transactions),
            "gaming_activity": self._build_gaming_section(profile),
            "responsible_gaming": self._build_rg_section(rg_history),
            "consent_records": self._build_consent_section(consent_records),
            "legal_notice": self._build_legal_notice(),
        }

    def _build_metadata(self, player_id: str) -> dict[str, Any]:
        return {
            "export_id": str(uuid.uuid4()),
            "generated_at": datetime.now(UTC).isoformat(),
            "export_version": self._EXPORT_VERSION,
            "player_id": player_id,
            "format": "JSON",
            "legal_basis_access": "GDPR Art.15 — Right of access",
            "legal_basis_portability": "GDPR Art.20 — Right to data portability",
            "controller": "AcmeToCasino Ltd",
            "dpo_contact": "dpo@acmetocasino.com",
        }

    def _build_profile_section(self, profile: dict[str, Any]) -> dict[str, Any]:
        """
        Art.20 eligible: this data was provided by the player (registration form).
        Legal basis: contract (Art.6(1)(b)).
        """
        _PROFILE_FIELDS = [
            "player_id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "date_of_birth",
            "address_line_1",
            "address_line_2",
            "city",
            "postcode",
            "country",
            "nationality",
            "language_preference",
            "currency",
            "registration_date",
            "account_status",
        ]
        return {
            "art20_eligible": True,
            "legal_basis": "Contract — GDPR Art.6(1)(b)",
            "data": {k: profile.get(k) for k in _PROFILE_FIELDS if k in profile},
        }

    def _build_financial_section(self, transactions: list[Any]) -> dict[str, Any]:
        """
        Art.20 NOT eligible: transaction records are processed on legal obligation
        basis (AMLD6) and are therefore outside the scope of portability.

        They ARE included in the access export (Art.15) with this explanation.
        """
        tx_list = []
        for tx in transactions:
            tx_dict = tx if isinstance(tx, dict) else tx.__dict__
            tx_list.append(
                {
                    "transaction_id": tx_dict.get("transaction_id"),
                    "type": tx_dict.get("type"),
                    "amount": str(tx_dict.get("amount", "")),
                    "currency": tx_dict.get("currency"),
                    "status": tx_dict.get("status"),
                    "created_at": str(tx_dict.get("created_at", "")),
                    "payment_method": tx_dict.get("payment_method"),
                }
            )

        return {
            "art20_eligible": False,
            "reason_not_portable": (
                "Transaction records are processed on the basis of legal obligation "
                "(AMLD6 Art.40 / UK MLR 2017 Reg.40). GDPR Art.20 applies only to "
                "data processed on the basis of consent or contract."
            ),
            "access_basis": "GDPR Art.15 — Right of access",
            "retention_basis": "AMLD6 Art.40 — 5 years after account closure",
            "data": tx_list,
        }

    def _build_gaming_section(self, profile: dict[str, Any]) -> dict[str, Any]:
        """
        Art.20 eligible: wagering history was generated by the player's activity.
        Legal basis: contract (Art.6(1)(b)).
        """
        return {
            "art20_eligible": True,
            "legal_basis": "Contract — GDPR Art.6(1)(b)",
            "data": {
                "total_sessions": profile.get("total_sessions"),
                "total_wagered": str(profile.get("total_wagered", "")),
                "total_won": str(profile.get("total_won", "")),
                "preferred_games": profile.get("preferred_games"),
                "last_session_at": str(profile.get("last_session_at", "")),
                "average_session_duration_minutes": profile.get("average_session_duration_minutes"),
            },
        }

    def _build_rg_section(self, rg_history: Any) -> dict[str, Any]:
        """
        Art.20 eligible for limits and self-exclusion (player-provided instructions).
        PGSI scores are Art.15 only (health-adjacent data, special category).

        WHY RG HISTORY IS INCLUDED IN PORTABILITY:
        A player who has set deposit limits or self-excluded at one operator should
        be able to provide that history to a new operator to maintain their protection
        level. Including this in portability exports supports harm prevention
        across the industry.

        WHY PGSI SCORES ARE ART.15 ONLY:
        PGSI scores are health-adjacent data processed under Art.9 (special
        categories). Portability of Art.9 data requires explicit consent under
        Art.9(2)(a). The default export assumes no such consent is in place.
        """
        rg_dict = rg_history if isinstance(rg_history, dict) else (
            rg_history.__dict__ if hasattr(rg_history, "__dict__") else {}
        )

        return {
            "art20_eligible": True,
            "legal_basis": "Contract and legitimate interest — GDPR Art.6(1)(b) and Art.6(1)(f)",
            "special_category_note": (
                "PGSI/SOGS scores are health-adjacent data under GDPR Art.9. "
                "These are included in access (Art.15) exports only. Portability "
                "of PGSI data requires explicit consent under Art.9(2)(a)."
            ),
            "data": {
                "self_exclusion_history": rg_dict.get("self_exclusion_history", []),
                "deposit_limit_history": rg_dict.get("deposit_limit_history", []),
                "session_limit_history": rg_dict.get("session_limit_history", []),
                "reality_check_preference_minutes": rg_dict.get("reality_check_preference_minutes"),
                "risk_level_history": rg_dict.get("risk_level_history", []),
                "interventions": rg_dict.get("interventions", []),
                # PGSI excluded from portability — access (Art.15) only
                "pgsi_scores_note": (
                    "PGSI score history excluded from portability export. "
                    "Request a full access (Art.15) report to receive this data."
                ),
            },
        }

    def _build_consent_section(self, consent_records: Any) -> dict[str, Any]:
        """
        Art.20 eligible: consent records represent player-provided instructions.
        """
        records_list = consent_records if isinstance(consent_records, list) else []
        return {
            "art20_eligible": True,
            "legal_basis": "Consent — GDPR Art.6(1)(a)",
            "data": {
                "records": [
                    r if isinstance(r, dict) else r.__dict__
                    for r in records_list
                ]
            },
        }

    def _build_legal_notice(self) -> dict[str, str]:
        return {
            "right_to_lodge_complaint": (
                "You have the right to lodge a complaint with your local supervisory "
                "authority. In the EU: your national DPA. In the UK: Information "
                "Commissioner's Office (ico.org.uk). In Brazil: ANPD (gov.br/anpd). "
                "In Canada: OPC (priv.gc.ca). In California: CPPA (cppa.ca.gov)."
            ),
            "right_to_erasure": (
                "You may request erasure of your personal data. Erasure is implemented "
                "via pseudonymisation rather than deletion because AMLD6 Art.40 requires "
                "retention of transaction records for five years. All PII fields will be "
                "replaced with cryptographic hashes and the mapping key destroyed."
            ),
            "third_party_transmission": (
                "Under GDPR Art.20(2), you may request that we transmit the Art.20-eligible "
                "sections of this export directly to another controller. Contact "
                "privacy@acmetocasino.com to initiate this."
            ),
        }
