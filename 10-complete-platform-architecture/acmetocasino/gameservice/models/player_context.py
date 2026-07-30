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
gameservice.models.player_context — PlayerContext
==================================================

Carries all player-specific information needed to launch a game session and
process wallet operations.  Passed from the platform API layer down through
the game service into each supplier adapter.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class PlayerContext(BaseModel):
    """Immutable snapshot of player identity and account state.

    This model is constructed once per API request and threaded through the
    entire game-service call chain.  Downstream adapters use it to build
    supplier-specific session tokens and to assert compliance rules.

    Attributes
    ----------
    player_id:
        Canonical platform player UUID.
    brand_id:
        The white-label brand the player is logged into (e.g. ``"acme_uk"``).
    jurisdiction:
        Regulatory jurisdiction code, e.g. ``"MGA"``, ``"UKGC"``, ``"SE"``.
    language:
        IETF BCP-47 language tag (e.g. ``"en-GB"``, ``"sv-SE"``).
    currency:
        ISO-4217 currency code (e.g. ``"EUR"``, ``"GBP"``, ``"SEK"``).
    ip_address:
        Player's public IP at the moment of session initiation.  Used for
        geo-compliance checks.
    session_token:
        Platform-issued opaque token that identifies the player's web session.
        Passed to suppliers so they can authenticate wallet callbacks.
    cash_balance:
        Real-money balance at session-launch time (informational; authoritative
        balance is always fetched from the wallet service).
    bonus_balance:
        Bonus credit balance at session-launch time.
    kyc_verified:
        Whether the player has passed KYC.  Some jurisdictions block real-money
        play until this is ``True``.
    age_verified:
        Whether the player's age has been confirmed.
    self_excluded:
        Whether the player is currently under a self-exclusion order.
    deposit_limit_daily:
        Player-configured daily deposit limit in their currency, if set.
    """

    player_id: str
    brand_id: str
    jurisdiction: str
    language: str = "en"
    currency: str = "EUR"
    ip_address: str = ""
    session_token: str = ""
    cash_balance: Decimal = Decimal("0")
    bonus_balance: Decimal = Decimal("0")
    kyc_verified: bool = False
    age_verified: bool = True
    self_excluded: bool = False
    deposit_limit_daily: Decimal | None = None

    model_config = {"frozen": True}
