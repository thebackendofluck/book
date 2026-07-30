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
gameservice.suppliers.evolution.translator — Domain ↔ Evolution API Translation
=================================================================================

The translator handles all conversions between the platform's domain models and
Evolution Gaming's wire formats.

Monetary amounts
----------------
Evolution uses **integer cents** (or the equivalent smallest denomination for
other currencies).  The platform uses :class:`decimal.Decimal` with up to two
decimal places for EUR/GBP/USD and up to zero decimal places for JPY.

The translator uses a ``CURRENCY_DECIMAL_PLACES`` mapping to handle this
correctly.  For currencies not in the mapping, two decimal places are assumed.

Webhook signature verification
--------------------------------
Evolution signs every inbound webhook with HMAC-SHA256 over the raw request
body using the shared ``webhook_secret``.  The translator exposes
:func:`verify_webhook_signature` for use by the inbound HTTP handler.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from acmetocasino.gameservice.models.enums import CommandType
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.suppliers.evolution.models import (
    EvolutionChannel,
    EvolutionGameConfig,
    EvolutionGameRef,
    EvolutionPlayerData,
    EvolutionPlayerSession,
    EvolutionSessionRequest,
    EvolutionTableRef,
    EvolutionUrlConfig,
    EvolutionWebhookEvent,
)

if TYPE_CHECKING:
    pass

# Decimal places for each currency when converting to/from integer cents
CURRENCY_DECIMAL_PLACES: dict[str, int] = {
    "EUR": 2, "GBP": 2, "USD": 2, "CAD": 2, "AUD": 2, "NZD": 2,
    "SEK": 2, "NOK": 2, "DKK": 2, "CHF": 2, "BRL": 2, "MXN": 2,
    "JPY": 0, "KRW": 0,
}


def decimal_to_cents(amount: Decimal, currency: str) -> int:
    """Convert a Decimal amount to integer cents for the given currency.

    Parameters
    ----------
    amount:
        Monetary value as Decimal (e.g. ``Decimal("1.50")``).
    currency:
        ISO-4217 currency code.

    Returns
    -------
    int
        Amount in the smallest currency denomination.

    Examples
    --------
    ::

        decimal_to_cents(Decimal("1.50"), "EUR")  # → 150
        decimal_to_cents(Decimal("500"), "JPY")    # → 500
    """
    places = CURRENCY_DECIMAL_PLACES.get(currency.upper(), 2)
    multiplier = Decimal(10 ** places)
    return int((amount * multiplier).to_integral_value())


def cents_to_decimal(cents: int, currency: str) -> Decimal:
    """Convert an integer cent amount back to Decimal.

    Parameters
    ----------
    cents:
        Amount in smallest denomination.
    currency:
        ISO-4217 currency code.

    Returns
    -------
    Decimal
    """
    places = CURRENCY_DECIMAL_PLACES.get(currency.upper(), 2)
    divisor = Decimal(10 ** places)
    return Decimal(cents) / divisor


def build_session_request(
    request: LaunchRequest,
    session_id: str,
    request_uuid: str,
) -> EvolutionSessionRequest:
    """Build the Evolution session creation payload from a platform LaunchRequest.

    Parameters
    ----------
    request:
        Platform launch request.
    session_id:
        The platform-generated session ID to embed in the Evolution payload.
    request_uuid:
        Unique UUID for this API call (for Evolution's idempotency).
    """
    table_id = request.extra_params.get("evolution_table_id", "")
    return_url = request.return_url or ""
    mobile = request.channel == "mobile"

    return EvolutionSessionRequest(
        uuid=request_uuid,
        player=EvolutionPlayerData(
            id=request.player.player_id,
            firstName="",
            lastName="",
            country=request.player.jurisdiction,
            language=request.player.language.split("-")[0],
            currency=request.player.currency,
            session=EvolutionPlayerSession(
                id=request.player.session_token,
                ip=request.player.ip_address,
            ),
        ),
        config=EvolutionGameConfig(
            game=EvolutionGameRef(
                table=EvolutionTableRef(id=table_id),
            ),
            channel=EvolutionChannel(
                wrapped=True,
                mobile=mobile,
            ),
        ),
        urls=EvolutionUrlConfig(
            lobby=return_url,
        ),
    )


def parse_webhook_command_type(event_type: str) -> CommandType:
    """Map an Evolution event type string to a platform CommandType.

    Parameters
    ----------
    event_type:
        Evolution webhook event type (``DEBIT``, ``CREDIT``, ``CANCEL``,
        ``TIP``, ``PROMO``).

    Returns
    -------
    CommandType

    Raises
    ------
    ValueError
        If the event type is not recognised.
    """
    mapping = {
        "DEBIT": CommandType.DEBIT,
        "CREDIT": CommandType.CREDIT,
        "CANCEL": CommandType.ROLLBACK,
        "TIP": CommandType.TIP,
        "PROMO": CommandType.CREDIT,
    }
    if event_type not in mapping:
        raise ValueError(
            f"Unknown Evolution webhook event type: {event_type!r}. "
            f"Expected one of {list(mapping)!r}"
        )
    return mapping[event_type]


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: str,
    webhook_secret: str,
) -> bool:
    """Verify the HMAC-SHA256 signature on an inbound Evolution webhook event.

    Evolution includes the signature as a hex digest in the
    ``X-Evo-Signature`` HTTP header.

    Parameters
    ----------
    raw_body:
        The raw request body bytes.
    signature_header:
        The value of the ``X-Evo-Signature`` header.
    webhook_secret:
        The shared HMAC secret configured for this operator.

    Returns
    -------
    bool
        ``True`` if the signature is valid.
    """
    expected = hmac.new(
        key=webhook_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def session_expiry(minutes: int = 60) -> datetime:
    """Return a UTC datetime ``minutes`` from now (default: Evolution's 60-minute TTL)."""
    return datetime.now(tz=timezone.utc) + timedelta(minutes=minutes)


__all__ = [
    "build_session_request",
    "cents_to_decimal",
    "decimal_to_cents",
    "parse_webhook_command_type",
    "session_expiry",
    "verify_webhook_signature",
]
