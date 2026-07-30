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
gameservice.models — Shared Domain Value Objects
=================================================

All types in this package are *immutable* Pydantic models or Python Enums.
They cross internal service boundaries (e.g. passed to the AccountsProvider
Protocol) and therefore must be serialisable and validatable by Pydantic.

Import convenience
------------------
Most callers can import everything they need from this sub-package::

    from acmetocasino.gameservice.models import (
        PlayerContext, LaunchRequest, RoundCommand, WalletSnapshot,
        GameMode, CommandType, ActionCode,
    )
"""

from __future__ import annotations

from acmetocasino.gameservice.models.enums import (
    ActionCode,
    CallbackStyle,
    CommandType,
    FundSource,
    GameMode,
    ProductType,
    RealityCheckAction,
)
from acmetocasino.gameservice.models.jurisdiction_profile import JurisdictionProfile
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.supplier_capabilities import SupplierCapabilities
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot

__all__ = [
    # Enums
    "ActionCode",
    "CallbackStyle",
    "CommandType",
    "FundSource",
    "GameMode",
    "ProductType",
    "RealityCheckAction",
    # Models
    "JurisdictionProfile",
    "LaunchRequest",
    "PlayerContext",
    "RoundCommand",
    "SupplierCapabilities",
    "WalletSnapshot",
]
