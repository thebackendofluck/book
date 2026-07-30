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
payment_validator_sweden.py — Sweden credit gambling ban payment validator.

Jurisdiction:       Kingdom of Sweden
Regulator:          Spelinspektionen (Swedish Gambling Authority)
                    Co-enforcement: Finansinspektionen, Konsumentverket
Regulation refs:
  - Spellagen (2018:1138) Chapter 14 — payment method restrictions
    https://www.riksdagen.se/sv/dokument-och-lagar/dokument/
    svensk-forfattningssamling/spellagen-20181138_sfs-2018-1138/
  - Spelinspektionen föreskrifter SIFS 2019:1 — operator obligations
    https://www.spelinspektionen.se/regler/foreskrifter/
  - Credit ban amendment — effective 1 April 2026 (full enforcement)
    https://igamingbusiness.com/legal-compliance/
    sweden-credit-gambling-april-2026/
  - Spelinspektionen circular: prohibited payment instruments (2025-12-01)
    https://www.spelinspektionen.se/
Penalties:
  - Fines up to 10% of annual turnover per violation
  - Licence revocation
  - Operators: mandatory reporting within 24h of detected violation
  - Managers: personal administrative fines (SIFS 2019:1 § 22)

Effective date:  1 April 2026 — full credit ban in force.

Prohibited payment methods (from 1 April 2026):
  - Visa Credit (BIN ranges 400000–499999, card type = credit)
  - Mastercard Credit (BIN ranges 510000–559999, card type = credit)
  - American Express (BIN ranges 340000–379999)
  - Diners Club / Discover (BIN ranges 300000–305999, 360000–369999)
  - Personal loans used for gambling
  - Buy Now Pay Later / invoice services (Klarna, Afterpay, Klarnapay)
  - Overdraft-funded transfers

Allowed payment methods:
  - Visa Debit (BIN type = debit)
  - Mastercard Debit / Maestro (BIN type = debit)
  - Swish (Swedish instant payment — bank-account funded)
  - Trustly (Open Banking — bank-account funded)
  - Skrill / Neteller (when funded from bank account, not credit)
  - Prepaid vouchers (Paysafecard)

Book chapter:  Chapter 36 — Financial Operations & Payment Processing
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Credit ban effective date
# ---------------------------------------------------------------------------

CREDIT_BAN_EFFECTIVE_DATE = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PaymentMethod(str, Enum):
    VISA_CREDIT = "visa_credit"
    VISA_DEBIT = "visa_debit"
    MASTERCARD_CREDIT = "mastercard_credit"
    MASTERCARD_DEBIT = "mastercard_debit"
    MAESTRO = "maestro"
    AMEX = "amex"
    DINERS = "diners"
    SWISH = "swish"
    TRUSTLY = "trustly"
    SKRILL = "skrill"
    NETELLER = "neteller"
    PAYSAFECARD = "paysafecard"
    KLARNA = "klarna"
    AFTERPAY = "afterpay"
    BNPL_OTHER = "bnpl_other"
    INVOICE = "invoice"
    UNKNOWN = "unknown"


class CardType(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    PREPAID = "prepaid"
    UNKNOWN = "unknown"


class CardNetwork(str, Enum):
    VISA = "visa"
    MASTERCARD = "mastercard"
    AMEX = "amex"
    DINERS = "diners"
    MAESTRO = "maestro"
    UNKNOWN = "unknown"


class ValidationDecision(str, Enum):
    ALLOWED = "allowed"
    BLOCKED_CREDIT_CARD = "blocked_credit_card"
    BLOCKED_BNPL = "blocked_bnpl"
    BLOCKED_PROHIBITED_NETWORK = "blocked_prohibited_network"
    BLOCKED_UNKNOWN_CARD_TYPE = "blocked_unknown_card_type"   # fail-closed
    ALLOWED_PASS_THROUGH = "allowed_pass_through"             # Swish, Trustly


# ---------------------------------------------------------------------------
# BIN database models
# ---------------------------------------------------------------------------

@dataclass
class BinRecord:
    """
    Record from the Bank Identification Number (BIN) lookup database.

    In production: use a commercial BIN database (e.g. Mastercard BIN
    lookup API, Visa BIN lookup, Binlist.net, or similar).  The BIN is
    the first 6–8 digits of the PAN.
    """
    bin_prefix: str                  # 6- or 8-digit BIN
    network: CardNetwork
    card_type: CardType
    issuing_country: str             # ISO 3166-1 alpha-2
    issuing_bank: str
    is_prepaid: bool = False
    is_corporate: bool = False


@dataclass
class PaymentInstrument:
    """Represents a payment instrument submitted by the player."""
    instrument_id: str
    method: PaymentMethod
    bin_prefix: Optional[str] = None          # first 8 digits of card PAN
    masked_pan: Optional[str] = None          # e.g. "4111 **** **** 1111"
    expiry_month: Optional[int] = None
    expiry_year: Optional[int] = None
    network: Optional[CardNetwork] = None
    card_type: Optional[CardType] = None
    issuing_country: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of validating a payment instrument for use in Sweden."""
    validation_id: str
    instrument_id: str
    player_id: str
    decision: ValidationDecision
    method: PaymentMethod
    reason: str
    checked_at: datetime
    bin_lookup_performed: bool = False
    bin_record: Optional[BinRecord] = None
    spelinspektionen_loggable: bool = True   # all results are audit-logged


# ---------------------------------------------------------------------------
# BIN lookup (stub — replace with commercial provider in production)
# ---------------------------------------------------------------------------

# Minimal static BIN database for demonstration.
# Production: integrate with Mastercard BIN API, Visa BIN service, or
# a commercial provider such as Routable or BINCheck.
_STATIC_BIN_DB: dict[str, BinRecord] = {
    # Visa Debit examples
    "411111": BinRecord("411111", CardNetwork.VISA, CardType.DEBIT, "SE", "Swedbank"),
    "413600": BinRecord("413600", CardNetwork.VISA, CardType.DEBIT, "SE", "SEB"),
    # Visa Credit examples
    "422222": BinRecord("422222", CardNetwork.VISA, CardType.CREDIT, "SE", "Nordea"),
    "454321": BinRecord("454321", CardNetwork.VISA, CardType.CREDIT, "GB", "Barclays"),
    # Mastercard Debit
    "510000": BinRecord("510000", CardNetwork.MASTERCARD, CardType.DEBIT, "SE", "Handelsbanken"),
    # Mastercard Credit
    "521234": BinRecord("521234", CardNetwork.MASTERCARD, CardType.CREDIT, "SE", "Nordea"),
    # Maestro (always debit)
    "630495": BinRecord("630495", CardNetwork.MAESTRO, CardType.DEBIT, "SE", "SEB"),
    # Amex (always credit)
    "371449": BinRecord("371449", CardNetwork.AMEX, CardType.CREDIT, "US", "American Express"),
}


class BinLookupService:
    """
    Looks up card type and network from BIN prefix.

    In production: replace _static_lookup with calls to the chosen
    commercial BIN database API.  The BIN must be resolved BEFORE
    accepting any card for deposit.
    """

    def lookup(self, bin_prefix: str) -> Optional[BinRecord]:
        """Look up a BIN prefix.  Returns None if not found."""
        # Try 8-digit first, then 6-digit (fall back gracefully)
        for length in (8, 6):
            key = bin_prefix[:length]
            if key in _STATIC_BIN_DB:
                return _STATIC_BIN_DB[key]
        log.warning("bin_lookup: BIN prefix not found in database",
                    bin_prefix=bin_prefix[:6] + "**")
        return None


# ---------------------------------------------------------------------------
# Prohibited payment method registry
# ---------------------------------------------------------------------------

_PROHIBITED_METHODS: frozenset[PaymentMethod] = frozenset({
    PaymentMethod.VISA_CREDIT,
    PaymentMethod.MASTERCARD_CREDIT,
    PaymentMethod.AMEX,
    PaymentMethod.DINERS,
    PaymentMethod.KLARNA,
    PaymentMethod.AFTERPAY,
    PaymentMethod.BNPL_OTHER,
    PaymentMethod.INVOICE,
})

_PROHIBITED_NETWORKS: frozenset[CardNetwork] = frozenset({
    CardNetwork.AMEX,
    CardNetwork.DINERS,
})

# Methods that pass through unconditionally (bank-funded, no credit risk)
_PASS_THROUGH_METHODS: frozenset[PaymentMethod] = frozenset({
    PaymentMethod.SWISH,
    PaymentMethod.TRUSTLY,
})


# ---------------------------------------------------------------------------
# Sweden payment validator
# ---------------------------------------------------------------------------

class SwedenPaymentValidator:
    """
    Validates payment instruments against the Swedish credit gambling ban
    (Spellagen Chapter 14, effective 1 April 2026).

    Decision logic:
      1. Pass-through methods (Swish, Trustly) — always ALLOWED
      2. BNPL / invoice methods — always BLOCKED
      3. Known-credit method enum — BLOCKED
      4. Card instruments: perform BIN lookup
         a. BIN found, card_type = DEBIT → ALLOWED
         b. BIN found, card_type = CREDIT → BLOCKED
         c. BIN not found → BLOCKED (fail-closed per Spelinspektionen guidance)
      5. All results are logged for Spelinspektionen audit
    """

    def __init__(self, bin_lookup: Optional[BinLookupService] = None) -> None:
        self._bin_lookup = bin_lookup or BinLookupService()

    def validate(
        self,
        player_id: str,
        instrument: PaymentInstrument,
    ) -> ValidationResult:
        """
        Validate a single payment instrument.  Call before every deposit.

        The result must be persisted in the operator's audit log with
        full instrument details (masked PAN only, never full PAN).
        """
        validation_id = f"SEV-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.now(timezone.utc)

        # 1. Pass-through methods
        if instrument.method in _PASS_THROUGH_METHODS:
            result = ValidationResult(
                validation_id=validation_id,
                instrument_id=instrument.instrument_id,
                player_id=player_id,
                decision=ValidationDecision.ALLOWED_PASS_THROUGH,
                method=instrument.method,
                reason=f"{instrument.method.value} is bank-funded; credit ban does not apply",
                checked_at=now,
                bin_lookup_performed=False,
            )
            self._log(result)
            return result

        # 2. BNPL / invoice methods — always blocked
        if instrument.method in (
            PaymentMethod.KLARNA, PaymentMethod.AFTERPAY,
            PaymentMethod.BNPL_OTHER, PaymentMethod.INVOICE
        ):
            result = ValidationResult(
                validation_id=validation_id,
                instrument_id=instrument.instrument_id,
                player_id=player_id,
                decision=ValidationDecision.BLOCKED_BNPL,
                method=instrument.method,
                reason="BNPL and invoice payment methods are prohibited under Spellagen Chapter 14",
                checked_at=now,
            )
            self._log(result)
            return result

        # 3. Method enum clearly identifies credit card
        if instrument.method in _PROHIBITED_METHODS:
            result = ValidationResult(
                validation_id=validation_id,
                instrument_id=instrument.instrument_id,
                player_id=player_id,
                decision=ValidationDecision.BLOCKED_CREDIT_CARD,
                method=instrument.method,
                reason=f"{instrument.method.value} is a prohibited credit instrument",
                checked_at=now,
            )
            self._log(result)
            return result

        # 4. Card instrument — perform BIN lookup
        if instrument.bin_prefix:
            bin_record = self._bin_lookup.lookup(instrument.bin_prefix)

            if bin_record is None:
                # BIN not found — fail-closed per Spelinspektionen
                result = ValidationResult(
                    validation_id=validation_id,
                    instrument_id=instrument.instrument_id,
                    player_id=player_id,
                    decision=ValidationDecision.BLOCKED_UNKNOWN_CARD_TYPE,
                    method=instrument.method,
                    reason="BIN not found in lookup database; blocked to comply with credit ban (fail-closed)",
                    checked_at=now,
                    bin_lookup_performed=True,
                )
                self._log(result)
                return result

            # Check prohibited network (Amex, Diners always blocked)
            if bin_record.network in _PROHIBITED_NETWORKS:
                result = ValidationResult(
                    validation_id=validation_id,
                    instrument_id=instrument.instrument_id,
                    player_id=player_id,
                    decision=ValidationDecision.BLOCKED_PROHIBITED_NETWORK,
                    method=instrument.method,
                    reason=f"{bin_record.network.value} is a prohibited card network in Sweden",
                    checked_at=now,
                    bin_lookup_performed=True,
                    bin_record=bin_record,
                )
                self._log(result)
                return result

            # Check card type from BIN
            if bin_record.card_type == CardType.CREDIT:
                result = ValidationResult(
                    validation_id=validation_id,
                    instrument_id=instrument.instrument_id,
                    player_id=player_id,
                    decision=ValidationDecision.BLOCKED_CREDIT_CARD,
                    method=instrument.method,
                    reason=f"BIN lookup confirms card type = CREDIT ({bin_record.network.value}); blocked under Spellagen",
                    checked_at=now,
                    bin_lookup_performed=True,
                    bin_record=bin_record,
                )
                self._log(result)
                return result

            if bin_record.card_type in (CardType.DEBIT, CardType.PREPAID):
                result = ValidationResult(
                    validation_id=validation_id,
                    instrument_id=instrument.instrument_id,
                    player_id=player_id,
                    decision=ValidationDecision.ALLOWED,
                    method=instrument.method,
                    reason=f"BIN lookup confirms card type = {bin_record.card_type.value}; allowed",
                    checked_at=now,
                    bin_lookup_performed=True,
                    bin_record=bin_record,
                )
                self._log(result)
                return result

            # Fallback: unknown card type from BIN — fail-closed
            result = ValidationResult(
                validation_id=validation_id,
                instrument_id=instrument.instrument_id,
                player_id=player_id,
                decision=ValidationDecision.BLOCKED_UNKNOWN_CARD_TYPE,
                method=instrument.method,
                reason="BIN found but card type unresolvable; blocked (fail-closed)",
                checked_at=now,
                bin_lookup_performed=True,
                bin_record=bin_record,
            )
            self._log(result)
            return result

        # No BIN available for a card instrument — fail-closed
        result = ValidationResult(
            validation_id=validation_id,
            instrument_id=instrument.instrument_id,
            player_id=player_id,
            decision=ValidationDecision.BLOCKED_UNKNOWN_CARD_TYPE,
            method=instrument.method,
            reason="Card instrument without BIN; blocked (fail-closed)",
            checked_at=now,
            bin_lookup_performed=False,
        )
        self._log(result)
        return result

    @staticmethod
    def _log(result: ValidationResult) -> None:
        """
        Emit a structured audit log entry.

        Spelinspektionen may request these logs during inspections.
        Retain for at least 5 years per Spellagen Chapter 12.
        """
        level = "info" if result.decision in (
            ValidationDecision.ALLOWED,
            ValidationDecision.ALLOWED_PASS_THROUGH,
        ) else "warning"

        log_method = getattr(log, level)
        log_method(
            "sweden_credit_ban: validation result",
            validation_id=result.validation_id,
            player_id=result.player_id,
            method=result.method.value,
            decision=result.decision.value,
            reason=result.reason,
            bin_lookup=result.bin_lookup_performed,
        )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    validator = SwedenPaymentValidator()

    test_instruments = [
        PaymentInstrument(
            instrument_id="inst-001",
            method=PaymentMethod.VISA_DEBIT,
            bin_prefix="411111",
            masked_pan="4111 **** **** 1111",
        ),
        PaymentInstrument(
            instrument_id="inst-002",
            method=PaymentMethod.VISA_CREDIT,
            bin_prefix="422222",
            masked_pan="4222 **** **** 2222",
        ),
        PaymentInstrument(
            instrument_id="inst-003",
            method=PaymentMethod.SWISH,
        ),
        PaymentInstrument(
            instrument_id="inst-004",
            method=PaymentMethod.KLARNA,
        ),
        PaymentInstrument(
            instrument_id="inst-005",
            method=PaymentMethod.MASTERCARD_DEBIT,
            bin_prefix="510000",
            masked_pan="5100 **** **** 0000",
        ),
        PaymentInstrument(
            instrument_id="inst-006",
            method=PaymentMethod.AMEX,
            bin_prefix="371449",
            masked_pan="3714 ****** 49010",
        ),
    ]

    print(f"Sweden credit ban validator — effective: {CREDIT_BAN_EFFECTIVE_DATE.date()}")
    print("-" * 70)
    for instrument in test_instruments:
        result = validator.validate("player-se-5001", instrument)
        status = "ALLOWED" if result.decision in (
            ValidationDecision.ALLOWED, ValidationDecision.ALLOWED_PASS_THROUGH
        ) else "BLOCKED"
        print(f"[{status:7}] {instrument.method.value:25} — {result.reason[:55]}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    _demo()
