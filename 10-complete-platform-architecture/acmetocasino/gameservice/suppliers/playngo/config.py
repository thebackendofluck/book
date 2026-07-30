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
gameservice.suppliers.playngo.config — Play'n GO Configuration
===============================================================
"""

from __future__ import annotations

from pydantic import Field

from acmetocasino.gameservice.suppliers.settings import SupplierConfig


class PlayngoConfig(SupplierConfig):
    """Play'n GO-specific configuration.

    Attributes
    ----------
    partner_id:
        Play'n GO partner/operator identifier.
    partner_key:
        Authentication key for SEAMLESS wallet callbacks.
    endpoint:
        The Play'n GO game server endpoint.
    """

    partner_id: str = Field(default="", description="Play'n GO partner ID.")
    partner_key: str = Field(default="", description="Authentication key for callbacks.")
    endpoint: str = Field(default="", description="Play'n GO game server endpoint URL.")


__all__ = ["PlayngoConfig"]
