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
gameservice.suppliers.igt.config — IGT Configuration
======================================================

IGT requires credentials for both the REST and SOAP layers.
"""

from __future__ import annotations

from pydantic import Field

from acmetocasino.gameservice.suppliers.settings import SupplierConfig


class IGTConfig(SupplierConfig):
    """IGT-specific configuration.

    Attributes
    ----------
    system_id:
        IGT operator system identifier.
    soap_endpoint:
        URL for IGT's SOAP-based legacy services (jackpot pool, reporting).
    soap_username:
        SOAP service authentication username.
    soap_password:
        SOAP service authentication password.
    jackpot_network_id:
        IGT MegaJackpots network identifier for progressive pool queries.
    settlement_feed_url:
        URL for polling IGT's settlement/round-closed feed.
    """

    system_id: str = Field(default="", description="IGT operator system identifier.")
    soap_endpoint: str = Field(
        default="",
        description="URL for IGT SOAP legacy services.",
    )
    soap_username: str = Field(default="", description="SOAP authentication username.")
    soap_password: str = Field(default="", description="SOAP authentication password.")
    jackpot_network_id: str = Field(
        default="",
        description="IGT MegaJackpots network ID for progressive pool queries.",
    )
    settlement_feed_url: str = Field(
        default="",
        description="URL for polling IGT's round-closed settlement feed.",
    )


__all__ = ["IGTConfig"]
