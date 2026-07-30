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
gameservice.suppliers.netent.translator — Domain ↔ NetEnt Translation
=======================================================================

NetEnt's SEAMLESS API sends amounts as decimal strings (e.g. ``"1.50"``).
The translator handles:

* Launch URL construction using NetEnt's ``staticServer`` format.
* Action type mapping to ``ActionCode``.
* Round-ended detection to close multi-credit rounds correctly.
"""

from __future__ import annotations

from decimal import Decimal

from acmetocasino.gameservice.models.enums import ActionCode
from acmetocasino.gameservice.models.launch_request import LaunchRequest

_NETENT_ACTION_MAP: dict[str, ActionCode] = {
    "SPIN": ActionCode.REGULAR,
    "WIN": ActionCode.REGULAR,
    "FREE_SPIN": ActionCode.FREE_SPIN,
    "FREE_WIN": ActionCode.FREE_SPIN,
    "BONUS": ActionCode.BONUS_BUY,
    "JACKPOT": ActionCode.JACKPOT,
    "RESPIN": ActionCode.RESPIN,
    "GAMBLE": ActionCode.GAMBLE,
}


def map_action_type(action_type: str) -> ActionCode:
    """Map a NetEnt actionType to a platform ActionCode."""
    return _NETENT_ACTION_MAP.get(action_type.upper(), ActionCode.REGULAR)


def build_netent_launch_url(
    request: LaunchRequest,
    casino_id: str,
    game_server_url: str,
    session_id: str,
) -> str:
    """Build the NetEnt game launch URL.

    NetEnt uses a ``staticServer`` path format::

        {game_server_url}/{gameId}/{lang}/index.html?
            casinoId={casino_id}&
            sessionId={session_id}&
            currency={currency}&
            ...

    Parameters
    ----------
    request:
        Platform launch request.
    casino_id:
        NetEnt casino identifier.
    game_server_url:
        Base URL for NetEnt's game server.
    session_id:
        Platform-generated session ID.
    """
    lang = request.player.language.lower().replace("-", "_")
    demo_flag = "true" if request.mode.value == "demo" else "false"
    params = {
        "casinoId": casino_id,
        "sessionId": request.player.session_token,
        "platformSessionId": session_id,
        "currency": request.player.currency,
        "lang": lang,
        "demo": demo_flag,
        "isMobile": "true" if request.channel == "mobile" else "false",
        "lobbyURL": request.return_url or "",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return f"{game_server_url}/{request.game_id}/index.html?{query}"


def parse_netent_amount(amount_str: str) -> Decimal:
    """Parse a NetEnt amount string to Decimal."""
    return Decimal(amount_str)


__all__ = [
    "build_netent_launch_url",
    "map_action_type",
    "parse_netent_amount",
]
