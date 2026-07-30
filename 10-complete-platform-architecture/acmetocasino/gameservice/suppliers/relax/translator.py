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
gameservice.suppliers.relax.translator — Domain ↔ Relax Translation
=====================================================================
"""

from __future__ import annotations

from acmetocasino.gameservice.models.enums import ActionCode
from acmetocasino.gameservice.models.launch_request import LaunchRequest


def map_relax_action(action_type: str, is_free_round: bool = False) -> ActionCode:
    """Map Relax actionType + free-round flag to ActionCode."""
    if is_free_round:
        return ActionCode.FREE_SPIN
    mapping = {
        "SPIN": ActionCode.REGULAR,
        "WIN": ActionCode.REGULAR,
        "FREE_SPIN": ActionCode.FREE_SPIN,
        "BONUS": ActionCode.BONUS_BUY,
    }
    return mapping.get(action_type.upper(), ActionCode.REGULAR)


def build_relax_launch_url(
    request: LaunchRequest,
    partner_id: str,
    api_base_url: str,
    session_id: str,
) -> str:
    """Build the Relax Gaming game launch URL."""
    demo = "true" if request.mode.value == "demo" else "false"
    params = {
        "partnerId": partner_id,
        "gameCode": request.game_id,
        "token": request.player.session_token,
        "lang": request.player.language,
        "currency": request.player.currency,
        "demo": demo,
        "sessionId": session_id,
        "lobbyUrl": request.return_url or "",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return f"{api_base_url}/games/launch?{query}"


__all__ = ["build_relax_launch_url", "map_relax_action"]
