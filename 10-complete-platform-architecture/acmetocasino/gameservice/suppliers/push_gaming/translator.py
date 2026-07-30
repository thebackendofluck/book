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
gameservice.suppliers.push_gaming.translator — Domain ↔ Push Gaming Translation
=================================================================================

Push Gaming uses HMAC-SHA256 signed callbacks.  The signature is computed over
the concatenation of the request body and the api_secret.
"""

from __future__ import annotations

import hashlib
import hmac

from acmetocasino.gameservice.models.enums import ActionCode
from acmetocasino.gameservice.models.launch_request import LaunchRequest


def map_push_gaming_action(action_type: str) -> ActionCode:
    """Map Push Gaming actionType to ActionCode."""
    mapping = {
        "BET": ActionCode.REGULAR,
        "WIN": ActionCode.REGULAR,
        "FREE_SPIN": ActionCode.FREE_SPIN,
        "FREE_WIN": ActionCode.FREE_SPIN,
        "JACKPOT": ActionCode.JACKPOT,
    }
    return mapping.get(action_type.upper(), ActionCode.REGULAR)


def verify_push_gaming_signature(
    raw_body: bytes,
    signature: str,
    api_secret: str,
) -> bool:
    """Verify Push Gaming HMAC-SHA256 callback signature."""
    expected = hmac.new(
        key=api_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def build_push_gaming_launch_url(
    request: LaunchRequest,
    casino_id: str,
    api_base_url: str,
    session_id: str,
) -> str:
    """Build Push Gaming game launch URL."""
    demo = "true" if request.mode.value == "demo" else "false"
    params = {
        "casinoId": casino_id,
        "gameRef": request.game_id,
        "token": request.player.session_token,
        "lang": request.player.language,
        "currency": request.player.currency,
        "demo": demo,
        "sessionId": session_id,
        "lobbyUrl": request.return_url or "",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return f"{api_base_url}/launch?{query}"


__all__ = [
    "build_push_gaming_launch_url",
    "map_push_gaming_action",
    "verify_push_gaming_signature",
]
