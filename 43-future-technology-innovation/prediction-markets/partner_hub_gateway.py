#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Pattern 1: embedded partner hub -- session handoff, wallet bridge, category filter.

Chapter 43c reference implementation. Pattern 1 is the Matchbook / Fanatics
Markets shape: a locally licensed operator (the *host*) embeds a prediction-
market supplier's product inside its own site. The host has done the
licensing, the KYC and the geofencing; the supplier never sees the player's
identity and never touches the player's money.

Three trust boundaries fall out of that split, and this module is one file
per boundary:

1. Session handoff. The host resolves who the player is and hands the
   supplier a signed, pseudonymous session claim -- never a name, an email,
   a document number. ``issue_handoff_token`` / ``verify_handoff_token``
   implement that as a small HMAC-signed token (no JWT library needed for
   three claims and one signature). Pseudonymity is enforced in code, not
   just in the docstring: ``issue_handoff_token`` refuses to mint a token
   whose ``account_ref`` looks like an email address or a long digit string
   (a CPF, a phone number). If it looks like PII, it doesn't leave the host.

2. Money. The player's balance never leaves the host's ledger -- the
   supplier only ever asks the host to reserve, capture or release cents
   against a pseudonymous account. ``WalletBridge`` is that ledger's public
   face: every mutating call takes an idempotency key, because the network
   between host and supplier will retry, and a retried "reserve $50" must
   not become two reservations.

3. Catalogue. The supplier's own geofencing is not a control the host can
   audit, so the host filters again on its own side before anything renders
   in the embedded surface. ``HubCategoryFilter`` re-runs
   ``JurisdictionGate`` locally and drops whatever the supplier should not
   have offered in the first place -- belt and braces, because the host
   carries the licence and the liability.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from jurisdiction_gate import GateDecision, JurisdictionGate
from market_lifecycle import MarketCategory

# -- 1. session handoff ---------------------------------------------------

_EMAIL_LIKE = re.compile(r"@")
_LONG_DIGIT_RUN = re.compile(r"^\d{11,}$")  # CPF (11), phone numbers, etc.


def _looks_like_pii(account_ref: str) -> bool:
    return bool(_EMAIL_LIKE.search(account_ref) or _LONG_DIGIT_RUN.match(account_ref))


class TokenError(Exception):
    """Malformed, tampered or expired handoff token."""


@dataclass(frozen=True)
class HandoffClaims:
    host_operator: str          # "matchbook"
    account_ref: str            # pseudonymous ref, NEVER PII (e.g. "mb-7f3a...")
    jurisdiction: str           # ISO code the host resolved via ITS geofencing
    allowed_categories: tuple   # category .value strings host permits this session
    issued_at: float
    expires_at: float


def _b64url_encode(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _b64url_decode(data: bytes) -> bytes:
    padded = data + b"=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def _claims_payload(claims: HandoffClaims) -> bytes:
    return json.dumps(
        {
            "host_operator": claims.host_operator,
            "account_ref": claims.account_ref,
            "jurisdiction": claims.jurisdiction,
            "allowed_categories": list(claims.allowed_categories),
            "issued_at": claims.issued_at,
            "expires_at": claims.expires_at,
        },
        sort_keys=True,
    ).encode("utf-8")


def issue_handoff_token(secret: bytes, claims: HandoffClaims) -> str:
    """Mint a signed handoff token. Refuses to sign a PII-looking account_ref."""
    if _looks_like_pii(claims.account_ref):
        raise ValueError(
            f"account_ref {claims.account_ref!r} looks like PII (email or "
            "an 11+ digit run); the hub only accepts pseudonymous refs"
        )
    if claims.expires_at <= claims.issued_at:
        raise ValueError("expires_at must be after issued_at")

    payload_b64 = _b64url_encode(_claims_payload(claims))
    signature_b64 = _b64url_encode(
        hmac.new(secret, payload_b64, hashlib.sha256).digest()
    )
    return (payload_b64 + b"." + signature_b64).decode("ascii")


def verify_handoff_token(secret: bytes, token: str,
                          clock: Callable[[], float]) -> HandoffClaims:
    """Verify signature and expiry, returning the recovered claims.

    Raises ``TokenError`` on any tamper, malformed structure or expiry --
    never returns a partially-trusted claim.
    """
    parts = token.encode("ascii").split(b".")
    if len(parts) != 2:
        raise TokenError("malformed token: expected '<payload>.<signature>'")
    payload_b64, signature_b64 = parts

    expected_signature = hmac.new(secret, payload_b64, hashlib.sha256).digest()
    try:
        given_signature = _b64url_decode(signature_b64)
    except Exception as exc:  # binascii.Error, ValueError, etc.
        raise TokenError(f"malformed signature: {exc}") from None

    if not hmac.compare_digest(expected_signature, given_signature):
        raise TokenError("signature mismatch: token has been tampered with")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:
        raise TokenError(f"malformed payload: {exc}") from None

    try:
        claims = HandoffClaims(
            host_operator=payload["host_operator"],
            account_ref=payload["account_ref"],
            jurisdiction=payload["jurisdiction"],
            allowed_categories=tuple(payload["allowed_categories"]),
            issued_at=payload["issued_at"],
            expires_at=payload["expires_at"],
        )
    except (KeyError, TypeError) as exc:
        raise TokenError(f"malformed claims: missing {exc}") from None

    if clock() >= claims.expires_at:
        raise TokenError(
            f"token expired at {claims.expires_at}, now {clock()}"
        )
    return claims


# -- 2. host-side wallet bridge --------------------------------------------

class WalletError(Exception):
    """Base class for wallet-bridge failures."""


class UnknownAccount(WalletError):
    pass


class InsufficientFunds(WalletError):
    pass


class UnknownReservation(WalletError):
    pass


class ReservationClosed(WalletError):
    """Reservation was already captured or released."""


@dataclass
class Reservation:
    reservation_id: str
    account_ref: str
    amount_cents: int
    open: bool = True


class WalletBridge:
    """Host-side ledger the supplier calls against. Money never leaves the host.

    Every mutating method is keyed by an ``idempotency_key`` supplied by the
    caller. The bridge remembers the result of the first call for a given
    key and replays it verbatim on retry -- it never re-applies the effect.
    A journal of every applied (not replayed) mutation is kept for
    reconciliation against the supplier's own ledger.
    """

    def __init__(self) -> None:
        self._balances: Dict[str, int] = {}
        self._reservations: Dict[str, Reservation] = {}
        self._idempotent_results: Dict[str, object] = {}
        self.journal: List[Tuple[str, str, str, int]] = []  # (op, key, account_ref, amount_cents)

    def open_account(self, account_ref: str, balance_cents: int) -> None:
        if account_ref in self._balances:
            raise ValueError(f"account {account_ref!r} already open")
        if balance_cents < 0:
            raise ValueError("balance_cents cannot be negative")
        self._balances[account_ref] = balance_cents

    def balance(self, account_ref: str) -> int:
        if account_ref not in self._balances:
            raise UnknownAccount(account_ref)
        return self._balances[account_ref]

    def reserve(self, account_ref: str, amount_cents: int,
                idempotency_key: str) -> Reservation:
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]  # type: ignore[return-value]
        if account_ref not in self._balances:
            raise UnknownAccount(account_ref)
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if self._balances[account_ref] < amount_cents:
            raise InsufficientFunds(
                f"{account_ref}: requested {amount_cents}, available "
                f"{self._balances[account_ref]}"
            )

        self._balances[account_ref] -= amount_cents
        reservation = Reservation(
            reservation_id=f"rsv-{uuid.uuid4().hex[:16]}",
            account_ref=account_ref,
            amount_cents=amount_cents,
        )
        self._reservations[reservation.reservation_id] = reservation
        self.journal.append(("reserve", idempotency_key, account_ref, amount_cents))
        self._idempotent_results[idempotency_key] = reservation
        return reservation

    def capture(self, reservation_id: str, idempotency_key: str) -> int:
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]  # type: ignore[return-value]
        reservation = self._require_open_reservation(reservation_id)
        reservation.open = False
        self.journal.append(
            ("capture", idempotency_key, reservation.account_ref,
             reservation.amount_cents)
        )
        self._idempotent_results[idempotency_key] = reservation.amount_cents
        return reservation.amount_cents

    def release(self, reservation_id: str, idempotency_key: str) -> int:
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]  # type: ignore[return-value]
        reservation = self._require_open_reservation(reservation_id)
        reservation.open = False
        self._balances[reservation.account_ref] += reservation.amount_cents
        self.journal.append(
            ("release", idempotency_key, reservation.account_ref,
             reservation.amount_cents)
        )
        self._idempotent_results[idempotency_key] = reservation.amount_cents
        return reservation.amount_cents

    def credit_settlement(self, account_ref: str, amount_cents: int,
                           idempotency_key: str) -> int:
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]  # type: ignore[return-value]
        if account_ref not in self._balances:
            raise UnknownAccount(account_ref)
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")

        self._balances[account_ref] += amount_cents
        self.journal.append(
            ("credit_settlement", idempotency_key, account_ref, amount_cents)
        )
        self._idempotent_results[idempotency_key] = amount_cents
        return amount_cents

    def _require_open_reservation(self, reservation_id: str) -> Reservation:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            raise UnknownReservation(reservation_id)
        if not reservation.open:
            raise ReservationClosed(
                f"reservation {reservation_id} was already captured or released"
            )
        return reservation


# -- 3. host-side category filter ------------------------------------------

@dataclass(frozen=True)
class FilterResult:
    allowed: list
    dropped: list  # (market_id, reason)


class HubCategoryFilter:
    """Re-applies jurisdiction policy on the host side; never trusts the supplier.

    The supplier's own geofencing is not a control the host can audit or be
    held accountable for. This filter runs the host's own
    ``JurisdictionGate`` against every market the supplier offers for a
    session and drops anything the host's licence does not cover.
    """

    def __init__(self, gate: JurisdictionGate) -> None:
        self.gate = gate

    def filter_markets(self, jurisdiction: str,
                        markets: list) -> FilterResult:
        allowed: list = []
        dropped: list = []
        decisions_by_category: Dict[MarketCategory, GateDecision] = {}

        for market_id, category in markets:
            if category not in decisions_by_category:
                decisions_by_category[category] = self.gate.evaluate(
                    jurisdiction, category
                )
            decision = decisions_by_category[category]
            if decision.allowed:
                allowed.append((market_id, category))
            else:
                reason = "; ".join(decision.reasons) if decision.reasons else \
                    f"category {category.value} not permitted in {jurisdiction}"
                dropped.append((market_id, reason))

        return FilterResult(allowed=allowed, dropped=dropped)
