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
gameservice.suppliers.nyx.translator — Domain ↔ NYX Translation
=================================================================

NYX is a multi-studio aggregator.  The ``studioId`` in each callback is
used to look up studio-specific wagering contribution rules in the platform's
bonus engine.  The translator provides a mapping helper for this.
"""

from __future__ import annotations

from acmetocasino.gameservice.models.enums import ActionCode
from acmetocasino.gameservice.models.launch_request import LaunchRequest


def map_nyx_action(is_free_round: bool) -> ActionCode:
    """Map NYX free-round flag to ActionCode."""
    return ActionCode.FREE_SPIN if is_free_round else ActionCode.REGULAR


def build_nyx_launch_url(
    request: LaunchRequest,
    operator_id: str,
    api_base_url: str,
    session_id: str,
) -> str:
    """Build the NYX game launch URL."""
    demo = "true" if request.mode.value == "demo" else "false"
    params = {
        "operatorId": operator_id,
        "gameId": request.game_id,
        "token": request.player.session_token,
        "lang": request.player.language,
        "currency": request.player.currency,
        "demo": demo,
        "platformSessionId": session_id,
        "lobbyUrl": request.return_url or "",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return f"{api_base_url}/launch?{query}"


__all__ = ["build_nyx_launch_url", "map_nyx_action"]
