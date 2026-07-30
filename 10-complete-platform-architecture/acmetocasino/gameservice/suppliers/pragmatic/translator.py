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
gameservice.suppliers.pragmatic.translator — Domain ↔ Pragmatic API Translation
=================================================================================

Handles conversion between platform domain models and Pragmatic Play's
SEAMLESS wallet callback formats.

Authentication
--------------
Pragmatic authenticates callbacks with an MD5 hash of all request parameters
sorted alphabetically by key and concatenated as ``key=value&...`` followed
by the ``secret_key``::

    hash = MD5("amount=1.00&currency=EUR&...&userId=player1" + secret_key)

The platform verifies this hash on every inbound callback using
:func:`verify_pragmatic_hash`.

Action code mapping
-------------------
Pragmatic's ``actionId`` field maps to the platform's ``ActionCode``::

    SPIN          → ActionCode.REGULAR
    FREE_SPIN     → ActionCode.FREE_SPIN
    BONUS_BUY     → ActionCode.BONUS_BUY
    JACKPOT       → ActionCode.JACKPOT
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from acmetocasino.gameservice.models.enums import ActionCode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.suppliers.pragmatic.models import PragmaticCallbackResponse

_ACTION_CODE_MAP: dict[str, ActionCode] = {
    "SPIN": ActionCode.REGULAR,
    "FREE_SPIN": ActionCode.FREE_SPIN,
    "FREESPIN": ActionCode.FREE_SPIN,
    "BONUS_BUY": ActionCode.BONUS_BUY,
    "BONUSBUY": ActionCode.BONUS_BUY,
    "JACKPOT": ActionCode.JACKPOT,
    "RESPIN": ActionCode.RESPIN,
    "GAMBLE": ActionCode.GAMBLE,
}


def map_action_code(action_id: str) -> ActionCode:
    """Map a Pragmatic actionId string to a platform ActionCode.

    Unknown action IDs fall back to ``ActionCode.REGULAR`` to avoid hard
    failures on new Pragmatic action types.
    """
    return _ACTION_CODE_MAP.get(action_id.upper(), ActionCode.REGULAR)


def compute_pragmatic_hash(params: dict[str, str], secret_key: str) -> str:
    """Compute the Pragmatic MD5 authentication hash for a set of parameters.

    Parameters
    ----------
    params:
        Request parameters (excluding the ``hash`` key itself).
    secret_key:
        The operator's Pragmatic secret key.

    Returns
    -------
    str
        Lowercase hexadecimal MD5 digest.
    """
    sorted_pairs = "&".join(
        f"{k}={v}" for k, v in sorted(params.items()) if k != "hash"
    )
    raw = sorted_pairs + secret_key
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def verify_pragmatic_hash(
    params: dict[str, str],
    received_hash: str,
    secret_key: str,
) -> bool:
    """Verify the hash on an inbound Pragmatic callback.

    Parameters
    ----------
    params:
        All request parameters (excluding ``hash``).
    received_hash:
        The ``hash`` value from the request.
    secret_key:
        The operator secret key.

    Returns
    -------
    bool
        ``True`` if the hash is valid.
    """
    expected = compute_pragmatic_hash(params, secret_key)
    return expected == received_hash.lower()


def build_launch_url(
    request: LaunchRequest,
    secure_login: str,
    api_base_url: str,
) -> str:
    """Build the Pragmatic Play game launch URL.

    Pragmatic constructs launch URLs as::

        {api_base_url}/gs2c/openGame.do?
            token={session_token}&
            siteId={secure_login}&
            gameSymbol={game_id}&
            language={language}&
            ...

    Parameters
    ----------
    request:
        Platform launch request.
    secure_login:
        Pragmatic operator login string.
    api_base_url:
        Pragmatic API base URL.

    Returns
    -------
    str
        Fully qualified launch URL.
    """
    params = {
        "token": request.player.session_token,
        "siteId": secure_login,
        "gameSymbol": request.game_id,
        "language": request.player.language.replace("-", "_"),
        "currency": request.player.currency,
        "lobbyUrl": request.return_url or "",
        "isMobile": "true" if request.channel == "mobile" else "false",
    }
    if request.mode.value == "demo":
        params["playMode"] = "DEMO"

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{api_base_url}/gs2c/openGame.do?{query}"


def build_callback_response(
    transaction_id: str,
    cash: Decimal,
    bonus: Decimal,
    currency: str,
    error_code: int = 0,
    description: str = "",
) -> PragmaticCallbackResponse:
    """Construct the JSON response body for a Pragmatic wallet callback.

    Parameters
    ----------
    transaction_id:
        Platform transaction ID.
    cash:
        Cash balance after the operation.
    bonus:
        Bonus balance after the operation.
    currency:
        ISO-4217 currency code.
    error_code:
        0 for success; non-zero for errors.  See Pragmatic error code table.
    description:
        Human-readable error description (empty on success).
    """
    return PragmaticCallbackResponse(
        error=error_code,
        description=description,
        currency=currency,
        cash=str(cash),
        bonus=str(bonus),
        transactionId=transaction_id,
    )


__all__ = [
    "build_callback_response",
    "build_launch_url",
    "compute_pragmatic_hash",
    "map_action_code",
    "verify_pragmatic_hash",
]
