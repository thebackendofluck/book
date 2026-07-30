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
PAM Service — Biometric Verification Service
=============================================
Wraps a facial recognition provider for:
  - Selfie vs. document-face comparison
  - Liveness detection (anti-spoofing)

Provider interface is intentionally thin; swap the stub for
AWS Rekognition, Azure Face API, or a local ONNX model in production.

Confidence threshold: 0.80  (per Lei 14.790/2023 biometric accuracy guidance)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

CONFIDENCE_THRESHOLD: float = 0.80


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class BiometricError(RuntimeError):
    """Base biometric verification exception."""


class BiometricMismatchError(BiometricError):
    """Confidence score is below the acceptance threshold."""

    def __init__(self, score: float, threshold: float) -> None:
        self.score = score
        self.threshold = threshold
        super().__init__(
            f"Biometric confidence {score:.3f} is below threshold {threshold:.3f}"
        )


class LivenessFailedError(BiometricError):
    """Liveness (anti-spoofing) check failed."""


class BiometricProviderError(BiometricError):
    """Downstream provider returned an error."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class BiometricVerificationResult:
    """Outcome of a single biometric verification attempt."""

    confidence_score: float
    passed: bool
    liveness_passed: bool
    provider: str
    checked_at: datetime
    reference_hash: str  # SHA-256 of selfie bytes — for audit only, never the image


# ---------------------------------------------------------------------------
# Biometric Service
# ---------------------------------------------------------------------------


class BiometricService:
    """
    Async facade for facial recognition + liveness detection.

    Stub behaviour:
      - liveness_token == "FAIL"        → liveness fails (score 0.10)
      - liveness_token == "LOW"         → low confidence (score 0.65)
      - Any other / None liveness token → passes at 0.92

    Production wiring:
      1. Decode base64 images to bytes.
      2. Call provider SDK / REST API.
      3. Return provider confidence and liveness verdict.
      4. Store only the SHA-256 hash of the selfie bytes for audit trail.
         Never persist the raw image beyond the request lifecycle.
    """

    PROVIDER_NAME: str = "mock-biometric-provider-v1"
    _NETWORK_LATENCY_S: float = 0.10

    def __init__(self, threshold: float = CONFIDENCE_THRESHOLD) -> None:
        self.threshold = threshold

    async def verify(
        self,
        selfie_base64: str,
        document_front_base64: str,
        liveness_token: Optional[str] = None,
    ) -> BiometricVerificationResult:
        """
        Compare selfie to document face and perform liveness check.

        Args:
            selfie_base64:          Base64-encoded JPEG of live selfie.
            document_front_base64:  Base64-encoded JPEG of document front.
            liveness_token:         Provider-specific liveness challenge token.

        Returns:
            BiometricVerificationResult with confidence score and metadata.
        """
        await asyncio.sleep(self._NETWORK_LATENCY_S)

        # Compute reference hash for audit (never store raw image)
        try:
            selfie_bytes = base64.b64decode(selfie_base64 + "==")
        except Exception:
            selfie_bytes = selfie_base64.encode()
        reference_hash = hashlib.sha256(selfie_bytes).hexdigest()

        # Stub logic
        liveness_passed = liveness_token != "FAIL"
        if liveness_token == "FAIL":
            confidence = 0.10
        elif liveness_token == "LOW":
            confidence = 0.65
        else:
            confidence = 0.92

        passed = liveness_passed and confidence >= self.threshold
        now = datetime.now(timezone.utc)

        logger.info(
            "biometric_verification",
            confidence=confidence,
            passed=passed,
            liveness_passed=liveness_passed,
            provider=self.PROVIDER_NAME,
            reference_hash=reference_hash[:16] + "...",
        )

        return BiometricVerificationResult(
            confidence_score=confidence,
            passed=passed,
            liveness_passed=liveness_passed,
            provider=self.PROVIDER_NAME,
            checked_at=now,
            reference_hash=reference_hash,
        )

    async def verify_or_raise(
        self,
        selfie_base64: str,
        document_front_base64: str,
        liveness_token: Optional[str] = None,
    ) -> BiometricVerificationResult:
        """Like verify() but raises typed exceptions on failure."""
        result = await self.verify(selfie_base64, document_front_base64, liveness_token)

        if not result.liveness_passed:
            raise LivenessFailedError(
                "Liveness check failed — possible spoofing attempt detected"
            )
        if not result.passed:
            raise BiometricMismatchError(result.confidence_score, self.threshold)

        return result
