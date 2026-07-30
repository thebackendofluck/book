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
gameservice.models.jurisdiction_profile — JurisdictionProfile
=============================================================

Encodes the regulatory rules that apply in a given jurisdiction.  Adapters
and the game-service orchestrator use this to gate access to features such as
demo play, auto-spin, and bonus games.
"""

from __future__ import annotations

from pydantic import BaseModel


class JurisdictionProfile(BaseModel):
    """Regulatory profile for a single licensing jurisdiction.

    Attributes
    ----------
    jurisdiction_code:
        Short code used throughout the platform (e.g. ``"MGA"``, ``"UKGC"``,
        ``"SE"``, ``"DK"``, ``"DE-SH"``).
    display_name:
        Human-readable name (e.g. ``"UK Gambling Commission"``).
    demo_allowed:
        Whether un-authenticated demo play is permitted.
    auto_spin_allowed:
        Whether the "auto-spin" / turbo feature may be offered.
    bonus_games_allowed:
        Whether bonus buy and gamble features may be offered.
    reality_check_interval_minutes:
        If non-zero, sessions must display a reality-check prompt at this
        frequency.  Zero means no mandatory prompt.
    max_bet_eur:
        Maximum stake per spin in EUR equivalent (``None`` = no cap).
    kyc_before_play:
        Whether full KYC must be verified before a real-money session starts.
    mandatory_loss_limit:
        Whether the platform must collect and enforce a loss limit on
        registration.
    speed_limit_ms:
        Minimum time in milliseconds between spin initiation and result
        display (``0`` = no restriction).
    allowed_currencies:
        List of ISO-4217 codes permitted in this jurisdiction.  Empty list
        means all currencies are allowed.
    """

    jurisdiction_code: str
    display_name: str
    demo_allowed: bool = True
    auto_spin_allowed: bool = True
    bonus_games_allowed: bool = True
    reality_check_interval_minutes: int = 0
    max_bet_eur: float | None = None
    kyc_before_play: bool = False
    mandatory_loss_limit: bool = False
    speed_limit_ms: int = 0
    allowed_currencies: list[str] = []

    model_config = {"frozen": True}
