# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Pre-transaction fraud scoring engine.

Signals evaluated (each contributes a weighted score 0–1):

  - Velocity check   — same user, too many deposits in a short window
  - Amount anomaly   — deposit significantly outside user's historical range
  - IP reputation    — datacenter / TOR exit node IP
  - Country mismatch — billing country != IP geo-location country
  - Device change    — new device fingerprint for established account
  - Blacklist        — user or card on explicit blocklist

Final score is a weighted average in [0, 1].
  < 0.30  → ALLOW
  0.30–0.70 → REVIEW  (manual review queue)
  > 0.70  → BLOCK

This is an in-process lightweight engine.  In production you would
call a dedicated risk service (e.g. Kount, Sift, or in-house ML).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from models import Deposit, FraudDecision, FraudScore

import structlog
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Signal weights
# ---------------------------------------------------------------------------

_WEIGHTS: dict[str, float] = {
    "velocity": 0.30,
    "amount_anomaly": 0.20,
    "ip_reputation": 0.25,
    "country_mismatch": 0.10,
    "blacklist": 0.15,
}

_ALLOW_THRESHOLD = 0.30
_REVIEW_THRESHOLD = 0.70


# ---------------------------------------------------------------------------
# Store interface (injectable)
# ---------------------------------------------------------------------------


class FraudDataStore(Protocol):
    """Read-only view of payment history used for fraud signals."""

    def recent_deposit_count(self, user_id: int, window_minutes: int) -> int:
        """Number of deposit attempts by this user in the given window."""
        ...

    def user_avg_deposit_amount(self, user_id: int) -> float:
        """Historical average deposit amount (minor units) for this user."""
        ...

    def is_blacklisted_user(self, user_id: int) -> bool: ...

    def is_blacklisted_ip(self, ip: str) -> bool: ...

    def is_datacenter_ip(self, ip: str) -> bool: ...

    def user_registered_country(self, user_id: int) -> str: ...


# ---------------------------------------------------------------------------
# In-memory stub store (used in tests / local dev)
# ---------------------------------------------------------------------------


class InMemoryFraudStore:
    """Minimal in-memory store that returns safe defaults."""

    def recent_deposit_count(self, user_id: int, window_minutes: int) -> int:
        return 0

    def user_avg_deposit_amount(self, user_id: int) -> float:
        return 5000.0  # £50 default

    def is_blacklisted_user(self, user_id: int) -> bool:
        return False

    def is_blacklisted_ip(self, ip: str) -> bool:
        return False

    def is_datacenter_ip(self, ip: str) -> bool:
        return ip.startswith("10.")  # treat RFC-1918 as "datacenter" in tests

    def user_registered_country(self, user_id: int) -> str:
        return "GB"


# ---------------------------------------------------------------------------
# Fraud checker
# ---------------------------------------------------------------------------


class FraudChecker:
    """
    Evaluates fraud signals and returns a FraudScore.

    Designed to be fast (< 5 ms) so it can run synchronously in the
    deposit request path before any PSP call is made.
    """

    # How many deposits per hour before velocity signal fires
    VELOCITY_LIMIT = 5
    VELOCITY_WINDOW_MINUTES = 60

    # Amount Z-score threshold beyond which anomaly signal fires
    AMOUNT_ANOMALY_FACTOR = 5.0

    def __init__(self, store: FraudDataStore | None = None) -> None:
        self._store: FraudDataStore = store or InMemoryFraudStore()

    def evaluate(self, deposit: Deposit) -> FraudScore:
        signals: list[str] = []
        weighted_score = 0.0

        # 1. Blacklist (hard block — bypass all other checks)
        if self._store.is_blacklisted_user(deposit.user_id):
            return FraudScore(
                payment_id=deposit.payment_id,
                user_id=deposit.user_id,
                score=1.0,
                decision=FraudDecision.BLOCK,
                signals=["blacklisted_user"],
            )
        if self._store.is_blacklisted_ip(deposit.user_ip):
            return FraudScore(
                payment_id=deposit.payment_id,
                user_id=deposit.user_id,
                score=1.0,
                decision=FraudDecision.BLOCK,
                signals=["blacklisted_ip"],
            )

        # 2. Velocity check
        recent = self._store.recent_deposit_count(
            deposit.user_id, self.VELOCITY_WINDOW_MINUTES
        )
        velocity_score = min(1.0, recent / self.VELOCITY_LIMIT) if self.VELOCITY_LIMIT > 0 else 0.0
        if velocity_score > 0.5:
            signals.append(f"velocity:{recent}_in_{self.VELOCITY_WINDOW_MINUTES}m")
        weighted_score += velocity_score * _WEIGHTS["velocity"]

        # 3. Amount anomaly
        avg = self._store.user_avg_deposit_amount(deposit.user_id)
        if avg > 0:
            ratio = deposit.amount / avg
            anomaly_score = min(1.0, max(0.0, (ratio - self.AMOUNT_ANOMALY_FACTOR) / 5.0))
        else:
            anomaly_score = 0.0
        if anomaly_score > 0.3:
            signals.append(f"amount_anomaly:ratio={deposit.amount / max(1, avg):.1f}x")
        weighted_score += anomaly_score * _WEIGHTS["amount_anomaly"]

        # 4. IP reputation
        ip_score = 0.5 if self._store.is_datacenter_ip(deposit.user_ip) else 0.0
        if ip_score > 0:
            signals.append(f"datacenter_ip:{deposit.user_ip}")
        weighted_score += ip_score * _WEIGHTS["ip_reputation"]

        # 5. Country mismatch (IP country vs registered country)
        registered_country = self._store.user_registered_country(deposit.user_id)
        if deposit.country_code and registered_country:
            if deposit.country_code.upper() != registered_country.upper():
                weighted_score += 1.0 * _WEIGHTS["country_mismatch"]
                signals.append(
                    f"country_mismatch:{deposit.country_code}!={registered_country}"
                )

        final_score = round(min(1.0, weighted_score), 4)
        decision = _score_to_decision(final_score)

        log.info(
            "Fraud score for payment %s: %.4f → %s signals=%s",
            deposit.payment_id,
            final_score,
            decision.value,
            signals,
        )

        return FraudScore(
            payment_id=deposit.payment_id,
            user_id=deposit.user_id,
            score=final_score,
            decision=decision,
            signals=signals,
        )


def _score_to_decision(score: float) -> FraudDecision:
    if score >= _REVIEW_THRESHOLD:
        return FraudDecision.BLOCK
    if score >= _ALLOW_THRESHOLD:
        return FraudDecision.REVIEW
    return FraudDecision.ALLOW
