#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Identity Resolution Engine for Casino CDP
==========================================
Combines deterministic matching (email, phone, document ID) with
probabilistic matching (device fingerprint, behavioral patterns) to
create a unified player profile graph.

Regulatory Notes:
- All PII hashing uses SHA-256 with site-specific salt
- GDPR Art.6(1)(b) - processing necessary for contract performance
- Player merge requires audit trail for responsible-gambling obligations
- Identity graph must support "right to erasure" cascade deletion
"""

import hashlib
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MatchType(Enum):
    DETERMINISTIC_EMAIL = "deterministic_email"
    DETERMINISTIC_PHONE = "deterministic_phone"
    DETERMINISTIC_DOCUMENT = "deterministic_document"
    DETERMINISTIC_SSO = "deterministic_sso"
    PROBABILISTIC_DEVICE = "probabilistic_device"
    PROBABILISTIC_BEHAVIORAL = "probabilistic_behavioral"
    PROBABILISTIC_IP_PATTERN = "probabilistic_ip_pattern"


class MatchConfidence(Enum):
    CERTAIN = 1.0       # Exact deterministic match
    HIGH = 0.85         # Strong probabilistic signal
    MEDIUM = 0.65       # Multiple weak signals combined
    LOW = 0.45          # Single weak signal
    INSUFFICIENT = 0.0  # Below merge threshold


@dataclass
class PlayerIdentifier:
    """A single identifier associated with a player."""
    identifier_type: str          # email, phone, device_fp, ip, document_id
    identifier_value: str         # Raw value (stored hashed in production)
    identifier_hash: str = ""     # SHA-256 hash
    source: str = ""              # Registration, deposit, login, etc.
    first_seen: datetime = field(default_factory=datetime.utcnow)  # ty:ignore[deprecated]
    last_seen: datetime = field(default_factory=datetime.utcnow)  # ty:ignore[deprecated]
    confidence: float = 1.0

    def __post_init__(self):
        if not self.identifier_hash:
            self.identifier_hash = self._hash_value(self.identifier_value)

    @staticmethod
    def _hash_value(value: str, salt: str = "casino_cdp_salt_v1") -> str:
        return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()


@dataclass
class IdentityEdge:
    """An edge in the identity graph connecting two player records."""
    source_profile_id: str
    target_profile_id: str
    match_type: MatchType
    confidence: float
    matched_on: str              # The identifier that caused the match
    created_at: datetime = field(default_factory=datetime.utcnow)  # ty:ignore[deprecated]
    verified: bool = False       # Manual verification flag
    auto_merged: bool = False


@dataclass
class UnifiedProfile:
    """A unified player profile aggregating all matched identities."""
    unified_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_profile_ids: list = field(default_factory=list)
    identifiers: list = field(default_factory=list)
    merge_history: list = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)  # ty:ignore[deprecated]
    updated_at: datetime = field(default_factory=datetime.utcnow)  # ty:ignore[deprecated]
    is_active: bool = True

    @property
    def emails(self) -> list:
        return [i for i in self.identifiers if i.identifier_type == "email"]

    @property
    def devices(self) -> list:
        return [i for i in self.identifiers if i.identifier_type == "device_fp"]


class IdentityResolutionEngine:
    """
    Core identity resolution engine for casino CDP.

    Merge Strategy:
    1. Deterministic pass: exact match on email, phone, or document ID
    2. Probabilistic pass: device fingerprint + behavioral similarity
    3. Conflict resolution: handle contradictions (e.g., same device, different KYC docs)

    Casino-Specific Considerations:
    - Multi-accounting detection is a side effect of identity resolution
    - Self-exclusion lists must propagate across merged profiles
    - Deposit limits apply to the UNIFIED profile, not individual accounts
    - Bonus abuse (multiple welcome bonuses) detected via identity graph
    """

    # Confidence thresholds for auto-merge vs. manual review
    AUTO_MERGE_THRESHOLD = 0.85
    MANUAL_REVIEW_THRESHOLD = 0.55
    REJECTION_THRESHOLD = 0.30

    # Casino-specific: max accounts that can legitimately share a device
    # (household members on shared computer)
    MAX_DEVICE_SHARING = 3

    def __init__(self, db_client=None):
        self.db = db_client
        self.profiles: dict[str, UnifiedProfile] = {}
        self.edges: list[IdentityEdge] = []
        self._identifier_index: dict[str, list[str]] = {}  # hash -> [profile_ids]

    def resolve(self, incoming_identifiers: list[PlayerIdentifier],
                source_profile_id: str) -> dict:
        """
        Main entry point: resolve a set of identifiers against the identity graph.

        Returns:
            {
                "action": "new" | "merge" | "review" | "blocked",
                "unified_profile_id": str,
                "matched_profiles": [...],
                "confidence": float,
                "flags": [...]
            }
        """
        candidates = self._find_candidates(incoming_identifiers)

        if not candidates:
            profile = self._create_new_profile(incoming_identifiers, source_profile_id)
            return {
                "action": "new",
                "unified_profile_id": profile.unified_id,
                "matched_profiles": [],
                "confidence": 1.0,
                "flags": [],
            }

        scored = self._score_candidates(incoming_identifiers, candidates)
        best_match = scored[0]

        # Check for multi-accounting fraud signals
        fraud_flags = self._check_fraud_signals(incoming_identifiers, candidates)

        if fraud_flags:
            logger.warning(
                "Fraud signals detected for %s: %s",
                source_profile_id, fraud_flags
            )
            return {
                "action": "blocked",
                "unified_profile_id": None,
                "matched_profiles": [c[0] for c in scored],
                "confidence": best_match[1],
                "flags": fraud_flags,
            }

        if best_match[1] >= self.AUTO_MERGE_THRESHOLD:
            profile = self._merge_profiles(
                best_match[0], incoming_identifiers, source_profile_id, best_match[2]
            )
            return {
                "action": "merge",
                "unified_profile_id": profile.unified_id,
                "matched_profiles": [best_match[0]],
                "confidence": best_match[1],
                "flags": [],
            }

        if best_match[1] >= self.MANUAL_REVIEW_THRESHOLD:
            return {
                "action": "review",
                "unified_profile_id": best_match[0],
                "matched_profiles": [c[0] for c in scored if c[1] >= self.MANUAL_REVIEW_THRESHOLD],
                "confidence": best_match[1],
                "flags": ["manual_review_required"],
            }

        profile = self._create_new_profile(incoming_identifiers, source_profile_id)
        return {
            "action": "new",
            "unified_profile_id": profile.unified_id,
            "matched_profiles": [],
            "confidence": 1.0,
            "flags": [],
        }

    def _find_candidates(self, identifiers: list[PlayerIdentifier]) -> dict[str, list]:
        """Find existing profiles that share any identifier."""
        candidates: dict[str, list] = {}
        for ident in identifiers:
            h = ident.identifier_hash
            if h in self._identifier_index:
                for profile_id in self._identifier_index[h]:
                    if profile_id not in candidates:
                        candidates[profile_id] = []
                    candidates[profile_id].append((ident, h))
        return candidates

    def _score_candidates(
        self, incoming: list[PlayerIdentifier], candidates: dict[str, list]
    ) -> list[tuple]:
        """
        Score each candidate profile. Returns sorted list of
        (profile_id, confidence, match_type).

        Scoring weights (casino-tuned):
        - Email exact match:     0.95
        - Phone exact match:     0.90
        - Document ID match:     0.99
        - SSO provider match:    0.85
        - Device FP match:       0.40 (shared devices are common)
        - IP pattern match:      0.15 (NAT, VPN)
        - Behavioral similarity: 0.30
        """
        WEIGHTS = {
            "email": 0.95,
            "phone": 0.90,
            "document_id": 0.99,
            "sso_id": 0.85,
            "device_fp": 0.40,
            "ip": 0.15,
            "behavioral": 0.30,
        }

        scored = []
        for profile_id, matches in candidates.items():
            total_score = 0.0
            best_match_type = MatchType.PROBABILISTIC_IP_PATTERN

            for ident, _ in matches:
                weight = WEIGHTS.get(ident.identifier_type, 0.1)
                adjusted = weight * ident.confidence
                total_score = max(total_score, adjusted)

                if ident.identifier_type in ("email", "phone", "document_id"):
                    if ident.identifier_type == "email":
                        best_match_type = MatchType.DETERMINISTIC_EMAIL
                    elif ident.identifier_type == "phone":
                        best_match_type = MatchType.DETERMINISTIC_PHONE
                    else:
                        best_match_type = MatchType.DETERMINISTIC_DOCUMENT

            # Bonus for multiple matching identifiers
            if len(matches) > 1:
                multi_bonus = min(0.15, 0.05 * (len(matches) - 1))
                total_score = min(1.0, total_score + multi_bonus)

            scored.append((profile_id, total_score, best_match_type))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _check_fraud_signals(
        self, incoming: list[PlayerIdentifier], candidates: dict[str, list]
    ) -> list[str]:
        """
        Casino-specific fraud detection during identity resolution.

        Flags:
        - multi_account_same_document: Same KYC document, different registration
        - excessive_device_sharing: More than MAX_DEVICE_SHARING accounts on one device
        - self_exclusion_bypass: Player on self-exclusion list creating new account
        - bonus_abuse_pattern: New account from device with recent welcome bonus claim
        """
        flags = []

        document_ids = [
            i for i in incoming if i.identifier_type == "document_id"
        ]
        for doc in document_ids:
            if doc.identifier_hash in self._identifier_index:
                existing = self._identifier_index[doc.identifier_hash]
                if len(existing) >= 1:
                    flags.append("multi_account_same_document")

        device_fps = [
            i for i in incoming if i.identifier_type == "device_fp"
        ]
        for dev in device_fps:
            if dev.identifier_hash in self._identifier_index:
                existing = self._identifier_index[dev.identifier_hash]
                if len(existing) >= self.MAX_DEVICE_SHARING:
                    flags.append("excessive_device_sharing")

        # Check self-exclusion (would query exclusion DB in production)
        for profile_id in candidates:
            profile = self.profiles.get(profile_id)
            if profile and not profile.is_active:
                flags.append("self_exclusion_bypass")
                break

        return flags

    def _create_new_profile(
        self, identifiers: list[PlayerIdentifier], source_profile_id: str
    ) -> UnifiedProfile:
        """Create a new unified profile and index its identifiers."""
        profile = UnifiedProfile(
            source_profile_ids=[source_profile_id],
            identifiers=identifiers,
        )
        self.profiles[profile.unified_id] = profile

        for ident in identifiers:
            h = ident.identifier_hash
            if h not in self._identifier_index:
                self._identifier_index[h] = []
            self._identifier_index[h].append(profile.unified_id)

        logger.info("Created new unified profile %s", profile.unified_id)
        return profile

    def _merge_profiles(
        self,
        existing_profile_id: str,
        new_identifiers: list[PlayerIdentifier],
        source_profile_id: str,
        match_type: MatchType,
    ) -> UnifiedProfile:
        """Merge incoming identifiers into an existing unified profile."""
        profile = self.profiles[existing_profile_id]
        profile.source_profile_ids.append(source_profile_id)
        profile.updated_at = datetime.utcnow()  # ty:ignore[deprecated]

        existing_hashes = {i.identifier_hash for i in profile.identifiers}
        for ident in new_identifiers:
            if ident.identifier_hash not in existing_hashes:
                profile.identifiers.append(ident)
                h = ident.identifier_hash
                if h not in self._identifier_index:
                    self._identifier_index[h] = []
                self._identifier_index[h].append(profile.unified_id)

        profile.merge_history.append({
            "merged_profile_id": source_profile_id,
            "match_type": match_type.value,
            "timestamp": datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
        })

        edge = IdentityEdge(
            source_profile_id=source_profile_id,
            target_profile_id=existing_profile_id,
            match_type=match_type,
            confidence=self.AUTO_MERGE_THRESHOLD,
            matched_on="auto_merge",
            auto_merged=True,
        )
        self.edges.append(edge)

        logger.info(
            "Merged profile %s into %s via %s",
            source_profile_id, existing_profile_id, match_type.value,
        )
        return profile

    def unmerge(self, unified_profile_id: str, source_profile_id: str) -> Optional[UnifiedProfile]:
        """
        Unmerge a source profile from a unified profile.
        Required for GDPR right-to-rectification and false-positive corrections.
        """
        profile = self.profiles.get(unified_profile_id)
        if not profile or source_profile_id not in profile.source_profile_ids:
            return None

        # Extract identifiers belonging to the source profile
        source_identifiers = [
            i for i in profile.identifiers if i.source == source_profile_id
        ]
        remaining_identifiers = [
            i for i in profile.identifiers if i.source != source_profile_id
        ]

        profile.identifiers = remaining_identifiers
        profile.source_profile_ids.remove(source_profile_id)
        profile.updated_at = datetime.utcnow()  # ty:ignore[deprecated]

        # Create new standalone profile for the unmerged identity
        new_profile = self._create_new_profile(source_identifiers, source_profile_id)

        logger.info(
            "Unmerged %s from %s -> new profile %s",
            source_profile_id, unified_profile_id, new_profile.unified_id,
        )
        return new_profile

    def gdpr_erase(self, unified_profile_id: str) -> bool:
        """
        GDPR Art. 17 - Right to erasure.
        Removes all PII from the unified profile while retaining
        anonymized aggregate data for regulatory reporting.
        """
        profile = self.profiles.get(unified_profile_id)
        if not profile:
            return False

        # Remove from identifier index
        for ident in profile.identifiers:
            h = ident.identifier_hash
            if h in self._identifier_index:
                self._identifier_index[h] = [
                    pid for pid in self._identifier_index[h]
                    if pid != unified_profile_id
                ]

        # Anonymize - keep structure for aggregate analytics
        profile.identifiers = []
        profile.is_active = False
        profile.updated_at = datetime.utcnow()  # ty:ignore[deprecated]

        logger.info("GDPR erasure completed for profile %s", unified_profile_id)
        return True


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    engine = IdentityResolutionEngine()

    # Player registers via website
    web_identifiers = [
        PlayerIdentifier("email", "player@example.com", source="web_registration"),
        PlayerIdentifier("phone", "+44-7700-900000", source="web_registration"),
        PlayerIdentifier("device_fp", "fp_abc123def456", source="web_registration"),
    ]
    result1 = engine.resolve(web_identifiers, source_profile_id="web_001")
    print(f"Web registration: {result1}")

    # Same player logs in via mobile app
    app_identifiers = [
        PlayerIdentifier("email", "player@example.com", source="app_login"),
        PlayerIdentifier("device_fp", "fp_mobile_789xyz", source="app_login"),
    ]
    result2 = engine.resolve(app_identifiers, source_profile_id="app_002")
    print(f"App login: {result2}")
    # Expected: action=merge (email deterministic match)

    # Different player on same household device
    household_identifiers = [
        PlayerIdentifier("email", "spouse@example.com", source="web_registration"),
        PlayerIdentifier("device_fp", "fp_abc123def456", source="web_registration"),
    ]
    result3 = engine.resolve(household_identifiers, source_profile_id="web_003")
    print(f"Household member: {result3}")
    # Expected: action=new (device match alone is below threshold)

    # Fraud attempt: same KYC document, new account
    fraud_identifiers = [
        PlayerIdentifier("email", "fake@throwaway.com", source="web_registration"),
        PlayerIdentifier("document_id", "PASSPORT-GB-123456", source="kyc_upload"),
    ]
    # First register the document
    engine.resolve(
        [PlayerIdentifier("document_id", "PASSPORT-GB-123456", source="kyc_upload")],
        source_profile_id="web_001",
    )
    result4 = engine.resolve(fraud_identifiers, source_profile_id="web_004")
    print(f"Fraud attempt: {result4}")
    # Expected: action=blocked, flags=["multi_account_same_document"]
