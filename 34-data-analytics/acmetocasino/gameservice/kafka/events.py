# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
gameservice.kafka.events — Domain Event Models
===============================================

Every game-service state transition is expressed as an immutable Pydantic model
that inherits from :class:`GameEvent`.

Design principles
-----------------
* **Audit-friendly** — every event carries ``player_id``, ``brand_id``,
  ``jurisdiction``, and ``correlation_id`` for compliance and tracing.
* **Monetary precision** — all amounts are :class:`decimal.Decimal`; callers
  must never pass raw floats.
* **Immutable** — models are frozen after construction (``model_config``).
* **Self-describing** — ``event_type`` is a string literal so consumers can
  route events without importing this module.

Event hierarchy
---------------
::

    GameEvent
    ├── SessionLaunchedEvent      (event_type = "session.launched")
    ├── RoundStartedEvent         (event_type = "round.started")
    ├── RoundCompletedEvent       (event_type = "round.completed")
    ├── TransactionProcessedEvent (event_type = "transaction.processed")
    ├── BalanceChangedEvent       (event_type = "balance.changed")
    ├── ComplianceViolationEvent  (event_type = "compliance.violation")
    └── SupplierErrorEvent        (event_type = "supplier.error")
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------


class GameEvent(BaseModel):
    """Root of the acmetocasino event hierarchy.

    Every event published to Kafka must carry the fields defined here so that
    downstream consumers (analytics, compliance, fraud) can operate on a
    consistent schema without parsing event-specific payloads.

    Attributes
    ----------
    event_id:
        UUID v4, auto-generated.  Uniquely identifies this specific event
        envelope; consumers should use it to deduplicate replayed messages.
    event_type:
        Dot-separated string that identifies the event kind
        (e.g. ``"session.launched"``).  Sub-classes override this with a
        fixed literal via ``model_fields_set``.
    timestamp:
        UTC datetime at which the event was created inside the producer.
    correlation_id:
        Caller-supplied trace ID that spans the full HTTP request lifecycle.
    player_id:
        Operator-assigned player identifier.
    brand_id:
        Identifies the white-label brand (e.g. ``"brand-uk"``).
    jurisdiction:
        Regulatory jurisdiction code (e.g. ``"UKGC"``, ``"MGA"``).
    supplier_id:
        Game-supplier identifier when the event is supplier-specific;
        ``None`` for platform-level events.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    correlation_id: str
    player_id: str
    brand_id: str
    jurisdiction: str
    supplier_id: str | None = None

    def as_audit_dict(self) -> dict[str, Any]:
        """Return a flat dict suitable for audit-trail serialisation.

        All :class:`~decimal.Decimal` values are converted to strings to
        preserve precision through JSON without loss.
        """
        raw = self.model_dump(mode="json")
        # Ensure Decimals (serialised by pydantic as strings in json mode)
        # are preserved as strings; datetime → ISO-8601 string.
        return raw


# ---------------------------------------------------------------------------
# Session events
# ---------------------------------------------------------------------------


class SessionLaunchedEvent(GameEvent):
    """Fired when a player successfully launches a game session.

    Attributes
    ----------
    game_id:
        Supplier game identifier (e.g. ``"book-of-dead"``).
    mode:
        One of ``"real_money"``, ``"demo"``, or ``"free_round"``.
    channel:
        Delivery channel — ``"web"``, ``"mobile"``, or ``"retail"``.
    """

    event_type: str = "session.launched"
    game_id: str
    mode: str  # real_money | demo | free_round
    channel: str


# ---------------------------------------------------------------------------
# Round events
# ---------------------------------------------------------------------------


class RoundStartedEvent(GameEvent):
    """Fired when a new game round is opened (debit accepted by wallet).

    Attributes
    ----------
    round_id:
        Supplier-issued round identifier; unique within ``supplier_id``.
    game_id:
        Supplier game identifier.
    wager_amount:
        Total stake placed for this round (monetary precision required).
    currency:
        ISO-4217 currency code (e.g. ``"GBP"``).
    """

    event_type: str = "round.started"
    round_id: str
    game_id: str
    wager_amount: Decimal
    currency: str


class RoundCompletedEvent(GameEvent):
    """Fired when a round is fully settled (credit or zero-payout processed).

    Attributes
    ----------
    round_id:
        Supplier-issued round identifier.
    game_id:
        Supplier game identifier.
    wager_amount:
        Total stake placed at round start.
    payout_amount:
        Total amount returned to the player; ``0`` for a losing round.
    currency:
        ISO-4217 currency code.
    cash_usage:
        Portion of the wager drawn from the player's real-money balance.
    bonus_usage:
        Portion of the wager drawn from bonus funds.
    duration_ms:
        Wall-clock time between round-start and settlement in milliseconds.
    """

    event_type: str = "round.completed"
    round_id: str
    game_id: str
    wager_amount: Decimal
    payout_amount: Decimal
    currency: str
    cash_usage: Decimal
    bonus_usage: Decimal
    duration_ms: int


# ---------------------------------------------------------------------------
# Transaction events
# ---------------------------------------------------------------------------


class TransactionProcessedEvent(GameEvent):
    """Fired after every wallet operation, regardless of outcome.

    Attributes
    ----------
    transaction_type:
        One of ``"debit"``, ``"credit"``, ``"rollback"``, or ``"adjust"``.
    round_id:
        The round this transaction belongs to.
    amount:
        Absolute value of the monetary movement.
    currency:
        ISO-4217 currency code.
    succeeded:
        ``True`` if the wallet accepted the operation; ``False`` on rejection
        or error.
    error_message:
        Human-readable failure reason when ``succeeded`` is ``False``.
    latency_ms:
        Round-trip time to the wallet service in milliseconds.
    """

    event_type: str = "transaction.processed"
    transaction_type: str  # debit | credit | rollback | adjust
    round_id: str
    amount: Decimal
    currency: str
    succeeded: bool
    error_message: str | None = None
    latency_ms: int


# ---------------------------------------------------------------------------
# Balance events
# ---------------------------------------------------------------------------


class BalanceChangedEvent(GameEvent):
    """Fired whenever a player's wallet balance changes.

    Carries both the before and after values so consumers can reconstruct
    the full ledger history without querying the database.

    Attributes
    ----------
    previous_balance:
        Player's balance immediately before the change.
    new_balance:
        Player's balance immediately after the change.
    change_amount:
        Signed delta (positive = credit, negative = debit).
    reason:
        Short machine-readable label — e.g. ``"round_debit"``,
        ``"round_credit"``, ``"manual_adjust"``.
    """

    event_type: str = "balance.changed"
    previous_balance: Decimal
    new_balance: Decimal
    change_amount: Decimal
    reason: str


# ---------------------------------------------------------------------------
# Compliance events
# ---------------------------------------------------------------------------


class ComplianceViolationEvent(GameEvent):
    """Fired whenever a compliance rule blocks or warns a player action.

    Attributes
    ----------
    violation_type:
        One of ``"geo_blocked"``, ``"kyc_missing"``, ``"limit_exceeded"``,
        ``"self_excluded"``, or ``"reality_check"``.
    details:
        Arbitrary key/value map with rule-specific context (e.g. country
        code, limit type, KYC status).
    action_taken:
        One of ``"blocked"``, ``"warned"``, or ``"logged"``.
    """

    event_type: str = "compliance.violation"
    violation_type: str  # geo_blocked | kyc_missing | limit_exceeded | self_excluded | reality_check
    details: dict[str, Any] = Field(default_factory=dict)
    action_taken: str  # blocked | warned | logged


# ---------------------------------------------------------------------------
# Supplier health events
# ---------------------------------------------------------------------------


class SupplierErrorEvent(GameEvent):
    """Fired whenever a supplier integration returns an error response.

    Used by the SRE dashboard and supplier-health alerts.  When
    ``recoverable`` is ``True`` the platform will attempt a retry; callers
    should inspect ``retry_count`` to distinguish first attempts from
    exhausted retries.

    Attributes
    ----------
    error_code:
        Supplier-defined error code (e.g. ``"ROUND_NOT_FOUND"``).
    error_message:
        Human-readable description from the supplier.
    retry_count:
        Number of retries already attempted before this event was emitted.
    recoverable:
        ``True`` if a retry is safe; ``False`` for permanent failures.
    """

    event_type: str = "supplier.error"
    error_code: str
    error_message: str
    retry_count: int = 0
    recoverable: bool = True


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "GameEvent",
    "SessionLaunchedEvent",
    "RoundStartedEvent",
    "RoundCompletedEvent",
    "TransactionProcessedEvent",
    "BalanceChangedEvent",
    "ComplianceViolationEvent",
    "SupplierErrorEvent",
]
