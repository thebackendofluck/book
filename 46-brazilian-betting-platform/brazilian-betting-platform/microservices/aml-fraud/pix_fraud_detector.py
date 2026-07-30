# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AML/Fraud Detection Service — PIX Fraud Detector
=================================================
Detects fraud patterns specific to the Brazilian PIX instant payment system.

Patterns detected (aligned with Resolução BCB 1/2020 and COAF IN-01/2017):

  VELOCITY      — too many PIX transactions from one sender in a 1-hour window
  SMURFING      — amounts just below the COAF reporting threshold (R$ 10.000)
                  designed to evade mandatory reporting
  MULE_ACCOUNT  — sender or receiver is a flagged mule CPF
  ROUND_TRIP    — A sends to B, then B sends back to A within the session window
  OFF_HOURS     — transaction between 00:00–06:00 BRT combined with high amount
  DEVICE_ANOMALY— transaction originates from a device fingerprint never seen
                  before for this CPF AND the amount is above threshold

All state is in-memory — replace with Redis for horizontal scaling.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import structlog

from models import PIXFraudCheckRequest, PIXFraudResult, PIXPattern

log = structlog.get_logger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

_VELOCITY_WINDOW_SECS: int = 3600          # 1 hour
_VELOCITY_MAX_COUNT: int = 10
_SMURFING_THRESHOLD_BRL: Decimal = Decimal("10000.00")   # COAF cash threshold
_SMURFING_RATIO_LOW: float = 0.75          # 75 % of threshold
_OFF_HOURS_START: int = 0                  # midnight BRT (UTC-3)
_OFF_HOURS_END: int = 6                    # 06:00 BRT
_OFF_HOURS_AMOUNT_BRL: Decimal = Decimal("5000.00")
_DEVICE_ANOMALY_AMOUNT_BRL: Decimal = Decimal("2000.00")


class PIXFraudDetector:
    """Stateful PIX fraud pattern detector (singleton-safe)."""

    def __init__(self) -> None:
        # cpf -> list of epoch timestamps (velocity window)
        self._velocity: dict[str, list[float]] = defaultdict(list)
        # cpfs flagged as mule accounts
        self._mule_set: set[str] = set()
        # cpf -> set of receiver cpfs (round-trip detection)
        self._recent_sends: dict[str, set[str]] = defaultdict(set)
        # cpf -> set of device fingerprints seen
        self._device_registry: dict[str, set[str]] = defaultdict(set)

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, req: PIXFraudCheckRequest) -> PIXFraudResult:
        """Run all fraud pattern checks and return a consolidated result."""
        patterns: list[PIXPattern] = []
        blocked = False

        # 1. Velocity
        if self._check_velocity(req.sender_cpf):
            patterns.append(PIXPattern.VELOCITY)
            blocked = True

        # 2. Mule account
        if self._mule_set.intersection({req.sender_cpf, req.receiver_cpf}):
            patterns.append(PIXPattern.MULE_ACCOUNT)
            blocked = True

        # 3. Smurfing
        if self._check_smurfing(req.amount):
            patterns.append(PIXPattern.SMURFING)

        # 4. Round-trip
        if self._check_round_trip(req.sender_cpf, req.receiver_cpf):
            patterns.append(PIXPattern.ROUND_TRIP)
            blocked = True

        # 5. Off-hours + high amount
        if self._check_off_hours(req.transaction_time, req.amount):
            patterns.append(PIXPattern.OFF_HOURS)

        # 6. Device anomaly
        if req.device_fingerprint and self._check_device_anomaly(
            req.sender_cpf, req.device_fingerprint, req.amount
        ):
            patterns.append(PIXPattern.DEVICE_ANOMALY)

        # Update state after checks (avoid self-influence)
        self._record_send(req.sender_cpf, req.receiver_cpf)

        probability = self._compute_probability(patterns, req.amount)

        log.info(
            "pix_fraud_detector.check_complete",
            sender=req.sender_cpf,
            receiver=req.receiver_cpf,
            amount=str(req.amount),
            patterns=[p.value for p in patterns],
            blocked=blocked,
            probability=round(probability, 4),
        )

        return PIXFraudResult(
            pix_key=req.pix_key,
            fraud_probability=probability,
            patterns=patterns,
            blocked=blocked,
            review_required=probability >= 0.4,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def flag_mule(self, cpf: str) -> None:
        """Mark a CPF as a known mule account."""
        self._mule_set.add(cpf)
        log.info("pix_fraud_detector.mule_flagged", cpf=cpf)

    def clear_mule(self, cpf: str) -> None:
        """Remove mule flag after a review clears the CPF."""
        self._mule_set.discard(cpf)
        log.info("pix_fraud_detector.mule_cleared", cpf=cpf)

    # ── Pattern checks ────────────────────────────────────────────────────────

    def _check_velocity(self, cpf: str) -> bool:
        """True if sender has exceeded the transaction rate limit."""
        now = time.monotonic()
        cutoff = now - _VELOCITY_WINDOW_SECS
        # Prune old entries
        self._velocity[cpf] = [t for t in self._velocity[cpf] if t > cutoff]
        self._velocity[cpf].append(now)
        return len(self._velocity[cpf]) > _VELOCITY_MAX_COUNT

    def _check_smurfing(self, amount: Decimal) -> bool:
        """True if amount is in the smurfing band (75–99.9 % of COAF threshold)."""
        lower = _SMURFING_THRESHOLD_BRL * Decimal(str(_SMURFING_RATIO_LOW))
        return lower < amount < _SMURFING_THRESHOLD_BRL

    def _check_round_trip(self, sender_cpf: str, receiver_cpf: str) -> bool:
        """True if the receiver has previously sent to the sender (A→B→A)."""
        return sender_cpf in self._recent_sends.get(receiver_cpf, set())

    def _check_off_hours(self, transaction_time: datetime, amount: Decimal) -> bool:
        """True if transaction is during off-hours (BRT) with a high amount."""
        # Ensure timezone awareness
        if transaction_time.tzinfo is None:
            transaction_time = transaction_time.replace(tzinfo=timezone.utc)

        # Convert to BRT (UTC-3)
        brt_hour = (transaction_time.hour - 3) % 24
        is_off_hours = _OFF_HOURS_START <= brt_hour < _OFF_HOURS_END
        return is_off_hours and amount > _OFF_HOURS_AMOUNT_BRL

    def _check_device_anomaly(
        self, cpf: str, fingerprint: str, amount: Decimal
    ) -> bool:
        """True if device fingerprint is new for this CPF and amount is above threshold."""
        known = self._device_registry[cpf]
        if not known:
            # Register first device; not anomalous
            known.add(fingerprint)
            return False
        if fingerprint in known:
            return False
        # New device for an existing CPF with a significant amount
        known.add(fingerprint)
        return amount >= _DEVICE_ANOMALY_AMOUNT_BRL

    # ── State mutation ────────────────────────────────────────────────────────

    def _record_send(self, sender_cpf: str, receiver_cpf: str) -> None:
        self._recent_sends[sender_cpf].add(receiver_cpf)

    # ── Scoring ───────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_probability(patterns: list[PIXPattern], amount: Decimal) -> float:
        """Weighted fraud probability combining pattern count and transaction amount."""
        max_patterns = len(PIXPattern)
        base = len(patterns) / max_patterns
        amount_factor = (
            0.2 if amount > Decimal("50000")
            else 0.1 if amount > Decimal("10000")
            else 0.0
        )
        return min(1.0, base + amount_factor)
