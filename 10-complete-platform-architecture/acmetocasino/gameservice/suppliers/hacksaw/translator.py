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
gameservice.suppliers.hacksaw.translator — Domain ↔ Hacksaw Translation
=========================================================================
"""

from __future__ import annotations

from decimal import Decimal

from acmetocasino.gameservice.models.enums import ActionCode
from acmetocasino.gameservice.models.launch_request import LaunchRequest


def map_hacksaw_bet_type(bet_type: str) -> ActionCode:
    """Map Hacksaw betType to ActionCode."""
    mapping = {
        "BET": ActionCode.REGULAR,
        "BONUS_BUY": ActionCode.BONUS_BUY,
        "FREE_BET": ActionCode.FREE_SPIN,
    }
    return mapping.get(bet_type.upper(), ActionCode.REGULAR)


def build_hacksaw_launch_url(
    request: LaunchRequest,
    operator_id: str,
    api_base_url: str,
    session_id: str,
) -> str:
    """Build the Hacksaw game launch URL."""
    demo = "true" if request.mode.value == "demo" else "false"
    params = {
        "operatorId": operator_id,
        "sessionId": session_id,
        "token": request.player.session_token,
        "gameCode": request.game_id,
        "lang": request.player.language,
        "currency": request.player.currency,
        "demo": demo,
        "lobbyUrl": request.return_url or "",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return f"{api_base_url}/game/launch?{query}"


__all__ = ["build_hacksaw_launch_url", "map_hacksaw_bet_type"]
