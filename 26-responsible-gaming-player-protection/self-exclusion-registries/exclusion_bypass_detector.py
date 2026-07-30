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
exclusion_bypass_detector.py — Cross-signal bypass detection engine.

Purpose:
  Detect self-excluded players who attempt to circumvent their exclusion
  by creating new accounts with different credentials.  Uses weighted
  multi-signal analysis: device fingerprint, payment method, IP subnet,
  email pattern, registration proximity, and name similarity.

Regulatory context:
  The responsibility lies with the operator, not the player.  If an
  excluded player successfully creates a new account, it is the operator
  that faces the regulatory penalty.

Signal weights (calibrated from enforcement case studies):
  - Device fingerprint match:        0.35
  - Payment method (last-4 + type):  0.25
  - IP /24 subnet match:             0.15
  - Email domain pattern match:      0.10
  - Registration time proximity:     0.10
  - Name similarity (Levenshtein):   0.05

Thresholds:
  - Score >= 0.60 → suspend + manual review
  - Score >= 0.80 → immediate block

Book chapter:  Chapter 26b — Self-Exclusion Registries Implementation
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from enum import Enum
from typing import Optional, Protocol

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THRESHOLD_REVIEW = 0.60
THRESHOLD_BLOCK = 0.80

WEIGHT_DEVICE_FINGERPRINT = 0.35
WEIGHT_PAYMENT_METHOD = 0.25
WEIGHT_IP_SUBNET = 0.15
WEIGHT_EMAIL_DOMAIN = 0.10
WEIGHT_REGISTRATION_PROXIMITY = 0.10
WEIGHT_NAME_SIMILARITY = 0.05

REGISTRATION_PROXIMITY_HOURS = 48


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class RecommendedAction(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"           # score >= 0.60
    BLOCK = "block"             # score >= 0.80


@dataclass
class PlayerProfile:
    """Identity and behavioural profile of a player account."""
    player_id: str
    first_name: str
    last_name: str
    email: str
    device_fingerprint: Optional[str] = None
    payment_last4: Optional[str] = None
    payment_type: Optional[str] = None      # "visa", "mastercard", "pix", etc.
    ip_address: Optional[str] = None
    registered_at: Optional[datetime] = None


@dataclass
class SignalMatch:
    signal_name: str
    weight: float
    matched: bool
    detail: str


@dataclass
class BypassAnalysis:
    analysis_id: str
    excluded_player_id: str
    suspect_player_id: str
    risk_score: float
    recommended_action: RecommendedAction
    signals: list[SignalMatch]
    analysed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Set by ExclusionBypassDetector.enforce(): whether the recommended
    # action was actually applied to the suspect account, and how.
    enforced: bool = False
    enforcement_detail: Optional[str] = None


class AccountEnforcer(Protocol):
    """
    Interface for applying bypass-detection outcomes to a real account.

    A scoring engine that only recommends an action is advisory and easy
    to silently ignore. Implement this against the operator's account
    service (closing sessions, freezing withdrawals, flagging for review,
    etc.) and pass it to ExclusionBypassDetector.enforce()/scan_and_enforce()
    so REVIEW/BLOCK outcomes are actually applied, not just logged.
    """

    def suspend_account(self, player_id: str, reason: str) -> None:
        """Suspend the account pending manual review (score >= 0.60)."""
        ...

    def block_account(self, player_id: str, reason: str) -> None:
        """Immediately block the account (score >= 0.80)."""
        ...


# ---------------------------------------------------------------------------
# Bypass detection engine
# ---------------------------------------------------------------------------

class ExclusionBypassDetector:
    """
    Cross-reference engine for detecting self-exclusion bypass attempts.

    Takes an excluded player's profile and compares it against a candidate
    (suspect) account, returning a weighted risk score. Scoring alone does
    not protect anyone: call enforce() (or pass an AccountEnforcer to
    scan_candidates()) to actually apply the REVIEW/BLOCK outcome to the
    suspect account rather than leaving it as an unread recommendation.
    """

    def __init__(self, enforcer: Optional[AccountEnforcer] = None) -> None:
        self._enforcer = enforcer

    def analyse(
        self,
        excluded: PlayerProfile,
        suspect: PlayerProfile,
    ) -> BypassAnalysis:
        """
        Compare excluded player profile against a suspect account.

        Returns a BypassAnalysis with a composite risk score and
        per-signal breakdown.
        """
        analysis_id = f"BYP-{uuid.uuid4().hex[:10].upper()}"
        signals: list[SignalMatch] = []

        # 1. Device fingerprint
        signals.append(self._check_device_fingerprint(excluded, suspect))

        # 2. Payment method
        signals.append(self._check_payment_method(excluded, suspect))

        # 3. IP /24 subnet
        signals.append(self._check_ip_subnet(excluded, suspect))

        # 4. Email domain pattern
        signals.append(self._check_email_domain(excluded, suspect))

        # 5. Registration time proximity
        signals.append(self._check_registration_proximity(excluded, suspect))

        # 6. Name similarity
        signals.append(self._check_name_similarity(excluded, suspect))

        # Composite score
        risk_score = sum(s.weight for s in signals if s.matched)

        if risk_score >= THRESHOLD_BLOCK:
            action = RecommendedAction.BLOCK
        elif risk_score >= THRESHOLD_REVIEW:
            action = RecommendedAction.REVIEW
        else:
            action = RecommendedAction.ALLOW

        analysis = BypassAnalysis(
            analysis_id=analysis_id,
            excluded_player_id=excluded.player_id,
            suspect_player_id=suspect.player_id,
            risk_score=round(risk_score, 3),
            recommended_action=action,
            signals=signals,
        )

        log.info(
            "bypass_detector: analysis complete",
            analysis_id=analysis_id,
            excluded_id=excluded.player_id,
            suspect_id=suspect.player_id,
            risk_score=analysis.risk_score,
            action=action.value,
        )

        if self._enforcer is not None:
            self.enforce(analysis)

        return analysis

    def enforce(self, analysis: BypassAnalysis) -> BypassAnalysis:
        """
        Apply the recommended action to the suspect account via the
        configured AccountEnforcer. Mutates and returns `analysis` with
        `enforced`/`enforcement_detail` set so callers (and audit logs)
        can see whether the score >= 0.60 / >= 0.80 policy described in
        Chapter 26b Section 9 was actually applied, not merely computed.

        No-op (advisory-only) when no enforcer was configured.
        """
        if self._enforcer is None:
            analysis.enforcement_detail = "no enforcer configured; advisory only"
            return analysis

        reason = (
            f"bypass_detector: analysis={analysis.analysis_id} "
            f"excluded={analysis.excluded_player_id} "
            f"risk_score={analysis.risk_score}"
        )

        if analysis.recommended_action == RecommendedAction.BLOCK:
            self._enforcer.block_account(analysis.suspect_player_id, reason)
            analysis.enforced = True
            analysis.enforcement_detail = "account blocked"
        elif analysis.recommended_action == RecommendedAction.REVIEW:
            self._enforcer.suspend_account(analysis.suspect_player_id, reason)
            analysis.enforced = True
            analysis.enforcement_detail = "account suspended pending review"
        else:
            analysis.enforcement_detail = "below review threshold; no action"

        log.info(
            "bypass_detector: enforcement applied",
            analysis_id=analysis.analysis_id,
            suspect_id=analysis.suspect_player_id,
            action=analysis.recommended_action.value,
            enforced=analysis.enforced,
        )
        return analysis

    def scan_candidates(
        self,
        excluded: PlayerProfile,
        candidates: list[PlayerProfile],
    ) -> list[BypassAnalysis]:
        """
        Scan multiple candidate accounts against an excluded player.

        Returns only candidates with score >= THRESHOLD_REVIEW, sorted
        by risk score descending.
        """
        results: list[BypassAnalysis] = []
        for candidate in candidates:
            if candidate.player_id == excluded.player_id:
                continue
            analysis = self.analyse(excluded, candidate)
            if analysis.risk_score >= THRESHOLD_REVIEW:
                results.append(analysis)

        results.sort(key=lambda a: a.risk_score, reverse=True)
        log.info("bypass_detector: scan complete",
                 excluded_id=excluded.player_id,
                 candidates_scanned=len(candidates),
                 flagged=len(results))
        return results

    # ------------------------------------------------------------------
    # Signal checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_device_fingerprint(
        excluded: PlayerProfile, suspect: PlayerProfile
    ) -> SignalMatch:
        matched = (
            excluded.device_fingerprint is not None
            and suspect.device_fingerprint is not None
            and excluded.device_fingerprint == suspect.device_fingerprint
        )
        return SignalMatch(
            signal_name="device_fingerprint",
            weight=WEIGHT_DEVICE_FINGERPRINT,
            matched=matched,
            detail=(
                "exact fingerprint match"
                if matched else "no match or data missing"
            ),
        )

    @staticmethod
    def _check_payment_method(
        excluded: PlayerProfile, suspect: PlayerProfile
    ) -> SignalMatch:
        matched = (
            excluded.payment_last4 is not None
            and suspect.payment_last4 is not None
            and excluded.payment_last4 == suspect.payment_last4
            and excluded.payment_type is not None
            and suspect.payment_type is not None
            and excluded.payment_type == suspect.payment_type
        )
        return SignalMatch(
            signal_name="payment_method",
            weight=WEIGHT_PAYMENT_METHOD,
            matched=matched,
            detail=(
                f"same {excluded.payment_type} ending {excluded.payment_last4}"
                if matched else "no match or data missing"
            ),
        )

    @staticmethod
    def _check_ip_subnet(
        excluded: PlayerProfile, suspect: PlayerProfile
    ) -> SignalMatch:
        """Compare /24 subnet (first three octets of IPv4)."""
        if excluded.ip_address and suspect.ip_address:
            excl_parts = excluded.ip_address.split(".")
            susp_parts = suspect.ip_address.split(".")
            matched = (
                len(excl_parts) >= 3
                and len(susp_parts) >= 3
                and excl_parts[:3] == susp_parts[:3]
            )
        else:
            matched = False

        return SignalMatch(
            signal_name="ip_subnet_24",
            weight=WEIGHT_IP_SUBNET,
            matched=matched,
            detail="same /24 subnet" if matched else "different subnet or data missing",
        )

    @staticmethod
    def _check_email_domain(
        excluded: PlayerProfile, suspect: PlayerProfile
    ) -> SignalMatch:
        """
        Check if emails share the same domain (excluding common providers).

        Common providers (gmail, outlook, yahoo, etc.) are excluded because
        a shared domain is not meaningful.
        """
        common_providers = {
            "gmail.com", "outlook.com", "hotmail.com", "yahoo.com",
            "icloud.com", "protonmail.com", "live.com", "mail.com",
        }
        excl_domain = excluded.email.split("@")[-1].lower() if "@" in excluded.email else ""
        susp_domain = suspect.email.split("@")[-1].lower() if "@" in suspect.email else ""

        matched = (
            excl_domain != ""
            and susp_domain != ""
            and excl_domain == susp_domain
            and excl_domain not in common_providers
        )
        return SignalMatch(
            signal_name="email_domain",
            weight=WEIGHT_EMAIL_DOMAIN,
            matched=matched,
            detail=(
                f"shared uncommon domain: {excl_domain}"
                if matched else "different domain or common provider"
            ),
        )

    @staticmethod
    def _check_registration_proximity(
        excluded: PlayerProfile, suspect: PlayerProfile
    ) -> SignalMatch:
        """Flag if suspect registered within 48h of the exclusion event."""
        if excluded.registered_at and suspect.registered_at:
            delta = abs(
                (suspect.registered_at - excluded.registered_at).total_seconds()
            )
            threshold = REGISTRATION_PROXIMITY_HOURS * 3600
            matched = delta <= threshold
            detail = f"registered {delta / 3600:.1f}h apart"
        else:
            matched = False
            detail = "registration timestamp missing"

        return SignalMatch(
            signal_name="registration_proximity",
            weight=WEIGHT_REGISTRATION_PROXIMITY,
            matched=matched,
            detail=detail,
        )

    @staticmethod
    def _check_name_similarity(
        excluded: PlayerProfile, suspect: PlayerProfile
    ) -> SignalMatch:
        """Fuzzy name comparison using SequenceMatcher."""
        full_excl = f"{excluded.first_name} {excluded.last_name}".lower()
        full_susp = f"{suspect.first_name} {suspect.last_name}".lower()
        ratio = SequenceMatcher(None, full_excl, full_susp).ratio()
        matched = ratio >= 0.80
        return SignalMatch(
            signal_name="name_similarity",
            weight=WEIGHT_NAME_SIMILARITY,
            matched=matched,
            detail=f"similarity ratio: {ratio:.2f}",
        )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

class _LoggingAccountEnforcer:
    """
    Minimal AccountEnforcer for the demo. A production implementation
    would call the operator's account service instead of printing.
    """

    def suspend_account(self, player_id: str, reason: str) -> None:
        print(f"  -> ENFORCED: suspended {player_id} ({reason})")

    def block_account(self, player_id: str, reason: str) -> None:
        print(f"  -> ENFORCED: blocked {player_id} ({reason})")


def _demo() -> None:
    detector = ExclusionBypassDetector(enforcer=_LoggingAccountEnforcer())

    excluded = PlayerProfile(
        player_id="plr-excl-001",
        first_name="John",
        last_name="Smith",
        email="john.smith@company.co.uk",
        device_fingerprint="fp-abc123def456",
        payment_last4="4242",
        payment_type="visa",
        ip_address="192.168.1.100",
        registered_at=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
    )

    suspect = PlayerProfile(
        player_id="plr-new-099",
        first_name="Jon",
        last_name="Smyth",
        email="jon.smyth@company.co.uk",
        device_fingerprint="fp-abc123def456",  # same device
        payment_last4="4242",                   # same card
        payment_type="visa",
        ip_address="192.168.1.105",             # same /24 subnet
        registered_at=datetime(2026, 1, 16, 8, 0, tzinfo=timezone.utc),
    )

    analysis = detector.analyse(excluded, suspect)

    print("Exclusion bypass detection demo")
    print(f"Excluded player: {excluded.player_id}")
    print(f"Suspect account: {suspect.player_id}")
    print(f"Risk score:      {analysis.risk_score}")
    print(f"Recommended:     {analysis.recommended_action.value}")
    print(f"Enforced:        {analysis.enforced} ({analysis.enforcement_detail})")
    print()
    for signal in analysis.signals:
        flag = "[X]" if signal.matched else "[ ]"
        print(f"  {flag} {signal.signal_name} (w={signal.weight}) — {signal.detail}")


if __name__ == "__main__":
    _demo()
