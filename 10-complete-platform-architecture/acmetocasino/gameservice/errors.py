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
gameservice.errors — Domain Exception Hierarchy
================================================

All domain exceptions inherit from :class:`GameServiceError`, allowing callers
to catch any game-service failure with a single ``except GameServiceError``.

Each exception carries:

* ``message``       — Human-readable description.
* ``player_id``     — The affected player (optional, for logging).
* ``correlation_id`` — Tracing ID that spans the full request lifecycle.
* ``retriable``     — Whether the caller *may* safely retry without side-effects.

Design note
-----------
Exceptions are *value-carrying* rather than bare classes.  This makes it
trivial to log structured data at the API boundary without having to parse
free-form strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@dataclass
class GameServiceError(Exception):
    """Root of the acmetocasino exception hierarchy.

    All game-service errors are deterministic (i.e. re-raising the same
    operation with the same inputs will produce the same error), *except*
    when ``retriable=True``, which indicates a transient infrastructure fault.
    """

    message: str
    player_id: str | None = None
    correlation_id: str | None = None
    retriable: bool = False

    #: HTTP status code that should be returned to the adapter layer.
    http_status: ClassVar[int] = 500

    def __str__(self) -> str:
        parts = [self.__class__.__name__, self.message]
        if self.player_id:
            parts.append(f"player={self.player_id}")
        if self.correlation_id:
            parts.append(f"correlation_id={self.correlation_id}")
        return " | ".join(parts)

    def __post_init__(self) -> None:
        # Ensure the dataclass value is also available as the standard
        # Exception ``args`` so that ``str(exc)`` and logging work correctly.
        super().__init__(str(self))


# ---------------------------------------------------------------------------
# Wallet / financial errors
# ---------------------------------------------------------------------------


@dataclass
class InsufficientFundsError(GameServiceError):
    """Raised when a debit would bring the player's available balance negative.

    Attributes
    ----------
    requested_amount:
        The debit amount that was attempted.
    available_balance:
        The player's balance at the time of the check.
    """

    requested_amount: str = "unknown"
    available_balance: str = "unknown"
    http_status: ClassVar[int] = 422


@dataclass
class TransactionBlockedError(GameServiceError):
    """Raised when a transaction is rejected by the fraud or compliance engine.

    The ``reason_code`` is a machine-readable token that maps to a specific
    rule in the fraud-detection system (e.g. ``"velocity_breach"``,
    ``"country_mismatch"``).
    """

    reason_code: str = "UNKNOWN"
    http_status: ClassVar[int] = 403


@dataclass
class NoMatchingDebitError(GameServiceError):
    """Raised when a credit or rollback references a ``round_id`` for which no
    corresponding debit exists in the ledger.

    This typically indicates a supplier bug or a re-delivered webhook whose
    original debit was never recorded.
    """

    round_id: str = "unknown"
    http_status: ClassVar[int] = 409


@dataclass
class AccountLimitReachedError(GameServiceError):
    """Raised when a player has hit a self-imposed or operator-mandated limit.

    Attributes
    ----------
    limit_type:
        One of ``"deposit"``, ``"loss"``, ``"wager"``, ``"session_duration"``.
    reset_at:
        ISO-8601 datetime string indicating when the limit period resets,
        if applicable.
    """

    limit_type: str = "unknown"
    reset_at: str | None = None
    http_status: ClassVar[int] = 403


# ---------------------------------------------------------------------------
# Session / authentication errors
# ---------------------------------------------------------------------------


@dataclass
class InvalidSessionError(GameServiceError):
    """Raised when a session token is expired, revoked, or never existed.

    Callers should redirect the player to the login flow.
    """

    session_token: str | None = None
    http_status: ClassVar[int] = 401


@dataclass
class RoundClosedError(GameServiceError):
    """Raised when a supplier tries to submit an action on a round that has
    already been settled or voided.

    Idempotent replays of *already-processed* transactions do NOT raise this
    error — they return ``TransactionResult(already_processed=True)`` instead.
    """

    round_id: str = "unknown"
    http_status: ClassVar[int] = 409


# ---------------------------------------------------------------------------
# Compliance / responsible-gambling errors
# ---------------------------------------------------------------------------


@dataclass
class GeoBlockedError(GameServiceError):
    """Raised when the player's IP address resolves to a jurisdiction that is
    not permitted for the requested game or brand.

    Attributes
    ----------
    detected_country:
        ISO-3166-1 alpha-2 country code derived from the IP (e.g. ``"US"``).
    required_jurisdiction:
        The jurisdiction code that *would* be required (e.g. ``"MGA"``).
    """

    detected_country: str = "unknown"
    required_jurisdiction: str | None = None
    http_status: ClassVar[int] = 451  # Unavailable For Legal Reasons


@dataclass
class RealityCheckExpiredError(GameServiceError):
    """Raised when a player has been playing for longer than the jurisdiction's
    reality-check interval without acknowledging the prompt.

    Play is suspended until the player dismisses the reality-check dialog.

    Attributes
    ----------
    elapsed_minutes:
        How long the player has been in-session.
    interval_minutes:
        The configured reality-check interval for this jurisdiction.
    """

    elapsed_minutes: int = 0
    interval_minutes: int = 0
    http_status: ClassVar[int] = 403


@dataclass
class KycNotApprovedError(GameServiceError):
    """Raised when a jurisdiction requires KYC verification before real-money
    play and the player has not yet completed (or passed) the KYC process.

    Attributes
    ----------
    kyc_status:
        One of ``"not_started"``, ``"pending"``, ``"rejected"``.
    """

    kyc_status: str = "not_started"
    http_status: ClassVar[int] = 403


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "GameServiceError",
    "InsufficientFundsError",
    "TransactionBlockedError",
    "NoMatchingDebitError",
    "AccountLimitReachedError",
    "InvalidSessionError",
    "RoundClosedError",
    "GeoBlockedError",
    "RealityCheckExpiredError",
    "KycNotApprovedError",
]
