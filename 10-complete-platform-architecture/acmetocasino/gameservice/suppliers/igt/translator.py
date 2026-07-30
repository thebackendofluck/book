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
gameservice.suppliers.igt.translator — Domain ↔ IGT Translation
=================================================================

IGT's hybrid REST/SOAP API requires two translation layers:

REST layer
----------
Standard JSON ↔ domain model conversion for session launch and settlement feed.

SOAP layer
----------
IGT's legacy SOAP services use WSDL-defined XML schemas.  The translator
provides helpers to build SOAP request bodies and parse XML responses.

In production, integrate with ``zeep`` (https://python-zeep.readthedocs.io)
or generate stubs from IGT's WSDL files.  The helpers here use plain string
templates to illustrate the pattern without adding a build-time dependency.

Regulatory game codes
---------------------
IGT maps games to regulatory game type codes (e.g. ``"SL"`` for slots,
``"BJ"`` for blackjack).  These codes must be included in regulatory reports.
"""

from __future__ import annotations

from decimal import Decimal

from acmetocasino.gameservice.models.enums import ActionCode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.suppliers.igt.models import (
    IGTRoundClosedEvent,
    IGTSessionRequest,
    IGTSoapRequest,
)

_IGT_GAME_TYPE_TO_PRODUCT = {
    "SL": "slots",
    "BJ": "table_games",
    "RL": "table_games",
    "VP": "table_games",
    "KN": "slots",  # Keno
}


def build_igt_session_request(
    request: LaunchRequest,
    system_id: str,
) -> IGTSessionRequest:
    """Build the IGT session creation REST payload."""
    return IGTSessionRequest(
        operatorId=system_id,
        playerId=request.player.player_id,
        sessionToken=request.player.session_token,
        gameCode=request.game_id,
        currency=request.player.currency,
        language=request.player.language,
        channel=request.channel,
    )


def parse_round_closed_event(event: IGTRoundClosedEvent) -> tuple[Decimal, Decimal]:
    """Extract (bet_amount, win_amount) from an IGT round-closed event.

    Returns
    -------
    tuple[Decimal, Decimal]
        (bet_amount, win_amount)
    """
    return Decimal(event.betAmount), Decimal(event.winAmount)


def build_jackpot_soap_request(
    network_id: str,
    username: str,
    password: str,
) -> IGTSoapRequest:
    """Build an IGT SOAP request for querying the MegaJackpots pool."""
    return IGTSoapRequest(
        operation="GetJackpotPoolValues",
        parameters={"networkId": network_id},
        username=username,
        password=password,
    )


def render_soap_envelope(req: IGTSoapRequest) -> str:
    """Render an IGT SOAP request to an XML string.

    In production this would use the zeep client.  Here we produce a
    minimal but structurally correct SOAP 1.1 envelope for illustration.
    """
    params_xml = "\n".join(
        f"            <igt:{k}>{v}</igt:{k}>"
        for k, v in req.parameters.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:igt="http://www.igt.com/services/jackpot/v1">
  <soapenv:Header>
    <igt:Security>
      <igt:Username>{req.username}</igt:Username>
      <igt:Password>{req.password}</igt:Password>
    </igt:Security>
  </soapenv:Header>
  <soapenv:Body>
    <igt:{req.operation}>
{params_xml}
    </igt:{req.operation}>
  </soapenv:Body>
</soapenv:Envelope>"""


def map_regulatory_game_code(game_type_code: str) -> str:
    """Map an IGT regulatory game type code to a platform product type string."""
    return _IGT_GAME_TYPE_TO_PRODUCT.get(game_type_code.upper(), "slots")


__all__ = [
    "build_igt_session_request",
    "build_jackpot_soap_request",
    "map_regulatory_game_code",
    "parse_round_closed_event",
    "render_soap_envelope",
]
