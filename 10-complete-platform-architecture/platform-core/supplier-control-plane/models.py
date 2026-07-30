# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
models.py
---------
Domain models for the Supplier Integration Control Plane.

These dataclasses represent the core entities managed by the control plane:
supplier registry records, health state, capability matrices, maintenance
windows, and callback policies.

All monetary thresholds and time values are expressed in the most explicit
units (milliseconds, ISO-8601 datetimes, fractions) to avoid ambiguity
across timezone and locale boundaries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SupplierType(str, Enum):
    CASINO = "CASINO"
    SPORTS_BOOK = "SPORTS_BOOK"
    AGGREGATOR = "AGGREGATOR"
    CRASH = "CRASH"
    VIRTUAL = "VIRTUAL"


class SupplierStatus(str, Enum):
    ACTIVE = "ACTIVE"
    MAINTENANCE = "MAINTENANCE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


class WalletModel(str, Enum):
    SEAMLESS = "SEAMLESS"       # Supplier calls back into platform on every tx
    TRANSFER = "TRANSFER"       # Funds transferred in/out; supplier holds balance
    HYBRID = "HYBRID"           # Seamless for debit, transfer for settlement


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNREACHABLE = "UNREACHABLE"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass
class Credentials:
    """
    Per-brand, per-jurisdiction API credentials for a supplier integration.

    Fields
    ------
    supplier_id:   The supplier this credential set belongs to.
    brand_id:      The brand (white-label) these credentials are issued to.
    jurisdiction:  Regulatory jurisdiction (e.g. "GB", "MT", "SE").
    api_key:       Primary authentication token.
    api_secret:    HMAC signing secret or secondary token.
    operator_id:   Operator identifier assigned by the supplier.
    extra:         Arbitrary additional key/value pairs (e.g. terminal IDs).
    rotated_at:    Timestamp of the last credential rotation.
    """

    supplier_id: str
    brand_id: str
    jurisdiction: str
    api_key: str
    api_secret: str
    operator_id: str
    extra: dict[str, str] = field(default_factory=dict)
    rotated_at: Optional[datetime] = None

    def masked_key(self) -> str:
        """Return api_key with all but the last 4 chars masked."""
        if len(self.api_key) <= 4:
            return "****"
        return "*" * (len(self.api_key) - 4) + self.api_key[-4:]


# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------


@dataclass
class SupplierCapabilityMatrix:
    """
    Declares what a supplier can deliver.

    Fields
    ------
    supplier_id:   Back-reference to the supplier.
    games:         Set of game IDs or slugs offered by this supplier.
    currencies:    ISO-4217 currency codes the supplier can settle in.
    jurisdictions: Regulatory jurisdictions the supplier is licensed for.
    wallet_model:  How funds flow between platform and supplier.
    rtp_certified: Whether game RTPs are independently certified.
    max_bet_usd:   Maximum stake per round in USD equivalent (0 = unlimited).
    """

    supplier_id: str
    games: set[str] = field(default_factory=set)
    currencies: set[str] = field(default_factory=set)
    jurisdictions: set[str] = field(default_factory=set)
    wallet_model: WalletModel = WalletModel.SEAMLESS
    rtp_certified: bool = False
    max_bet_usd: float = 0.0

    def supports_jurisdiction(self, jurisdiction: str) -> bool:
        return not self.jurisdictions or jurisdiction in self.jurisdictions

    def supports_currency(self, currency: str) -> bool:
        return not self.currencies or currency in self.currencies


# ---------------------------------------------------------------------------
# Supplier record
# ---------------------------------------------------------------------------


@dataclass
class SupplierRecord:
    """
    Canonical registry entry for a supplier integration.

    Fields
    ------
    id:                    Unique slug (e.g. "evolution", "pragmatic").
    name:                  Human-readable display name.
    type:                  Broad category of the integration.
    status:                Current operational status.
    capabilities:          What the supplier can deliver.
    credentials_per_brand: Map of brand_id -> Credentials list (one per jurisdiction).
    contact_email:         Supplier technical contact for escalation.
    created_at:            When this record was first registered.
    updated_at:            When this record was last modified.
    """

    id: str
    name: str
    type: SupplierType
    status: SupplierStatus = SupplierStatus.ACTIVE
    capabilities: Optional[SupplierCapabilityMatrix] = None
    credentials_per_brand: dict[str, list[Credentials]] = field(default_factory=dict)
    contact_email: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def get_credentials(self, brand_id: str, jurisdiction: str) -> Optional[Credentials]:
        """Return the credentials for a specific brand + jurisdiction combination."""
        creds = self.credentials_per_brand.get(brand_id, [])
        for c in creds:
            if c.jurisdiction == jurisdiction:
                return c
        return None

    def is_available(self) -> bool:
        return self.status == SupplierStatus.ACTIVE


# ---------------------------------------------------------------------------
# Health snapshot
# ---------------------------------------------------------------------------


@dataclass
class SupplierHealth:
    """
    Point-in-time health reading for a supplier.

    Fields
    ------
    supplier_id:  Back-reference.
    last_check:   UTC timestamp of the most recent health probe.
    latency_ms:   Round-trip latency of the health-check request in ms.
    error_rate:   Fraction of requests that errored in the sampling window (0.0-1.0).
    status:       Derived health bucket.
    message:      Optional detail from the supplier's health endpoint.
    consecutive_failures: Number of back-to-back failed checks.
    """

    supplier_id: str
    last_check: datetime
    latency_ms: float
    error_rate: float       # 0.0 – 1.0
    status: HealthStatus = HealthStatus.UNKNOWN
    message: str = ""
    consecutive_failures: int = 0

    # Thresholds used by health_monitor when deriving status
    LATENCY_THRESHOLD_MS: float = 2000.0
    ERROR_RATE_THRESHOLD: float = 0.05   # 5 %

    def is_degraded(self) -> bool:
        return (
            self.error_rate > self.ERROR_RATE_THRESHOLD
            or self.latency_ms > self.LATENCY_THRESHOLD_MS
        )


# ---------------------------------------------------------------------------
# Maintenance window
# ---------------------------------------------------------------------------


@dataclass
class MaintenanceWindow:
    """
    Scheduled or emergency maintenance window for a supplier.

    Fields
    ------
    id:          Unique window identifier (UUID).
    supplier_id: Which supplier this window applies to.
    start:       Planned start time (UTC).
    end:         Planned end time (UTC).
    reason:      Human-readable description of why maintenance is needed.
    created_by:  Operator who scheduled this window.
    """

    supplier_id: str
    start: datetime
    end: datetime
    reason: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_by: str = "system"

    def is_active(self, at: Optional[datetime] = None) -> bool:
        """Return True if the window covers the given moment (default: now)."""
        now = at or datetime.now(timezone.utc)
        return self.start <= now <= self.end

    def overlaps(self, other: "MaintenanceWindow") -> bool:
        """Return True if this window overlaps with another window."""
        return self.start < other.end and self.end > other.start


# ---------------------------------------------------------------------------
# Callback policy
# ---------------------------------------------------------------------------


@dataclass
class CallbackPolicy:
    """
    Retry and timeout configuration for supplier callbacks into the platform.

    Fields
    ------
    supplier_id:           Back-reference.
    retry_count:           Maximum number of retry attempts after the first failure.
    timeout_ms:            Per-attempt HTTP timeout in milliseconds.
    idempotency_key_field: Name of the request field that carries the idempotency key.
    backoff_factor:        Exponential backoff multiplier between retries.
    circuit_breaker_threshold: Consecutive failures before the circuit opens.
    """

    supplier_id: str
    retry_count: int = 3
    timeout_ms: int = 5000
    idempotency_key_field: str = "transaction_id"
    backoff_factor: float = 2.0
    circuit_breaker_threshold: int = 5

    def effective_timeout_s(self) -> float:
        return self.timeout_ms / 1000.0
