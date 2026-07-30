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
gameservice.suppliers.playngo.translator — Domain ↔ Play'n GO Translation
===========================================================================

Play'n GO action type mapping and launch URL construction.
"""

from __future__ import annotations

from decimal import Decimal

from acmetocasino.gameservice.models.enums import ActionCode
from acmetocasino.gameservice.models.launch_request import LaunchRequest

_PLAYNGO_ACTION_MAP: dict[str, ActionCode] = {
    "SPIN": ActionCode.REGULAR,
    "WIN": ActionCode.REGULAR,
    "FREE_SPIN": ActionCode.FREE_SPIN,
    "FREE_WIN": ActionCode.FREE_SPIN,
    "BONUS": ActionCode.BONUS_BUY,
    "JACKPOT": ActionCode.JACKPOT,
}


def map_playngo_action(action_type: str, free_round: bool = False) -> ActionCode:
    """Map a Play'n GO actionType to a platform ActionCode."""
    if free_round:
        return ActionCode.FREE_SPIN
    return _PLAYNGO_ACTION_MAP.get(action_type.upper(), ActionCode.REGULAR)


def build_playngo_launch_url(
    request: LaunchRequest,
    partner_id: str,
    endpoint: str,
    session_id: str,
) -> str:
    """Build the Play'n GO game launch URL."""
    demo = "1" if request.mode.value == "demo" else "0"
    mobile = "1" if request.channel == "mobile" else "0"
    params = {
        "pid": partner_id,
        "gid": request.game_id,
        "token": request.player.session_token,
        "lang": request.player.language,
        "cur": request.player.currency,
        "demo": demo,
        "mobile": mobile,
        "sid": session_id,
        "lobbyURL": request.return_url or "",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return f"{endpoint}/game/launch?{query}"


__all__ = ["build_playngo_launch_url", "map_playngo_action"]
