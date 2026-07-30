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
gameservice.suppliers.push_gaming.config — Push Gaming Configuration
====================================================================
"""

from __future__ import annotations

from pydantic import Field

from acmetocasino.gameservice.suppliers.settings import SupplierConfig


class PushGamingConfig(SupplierConfig):
    """Push Gaming configuration.

    Attributes
    ----------
    casino_id:
        Push Gaming casino/operator identifier.
    api_secret:
        Shared secret for HMAC-SHA256 callback authentication.
    """

    casino_id: str = Field(default="", description="Push Gaming casino identifier.")
    api_secret: str = Field(
        default="",
        description="Shared secret for HMAC-SHA256 callback authentication.",
    )


__all__ = ["PushGamingConfig"]
