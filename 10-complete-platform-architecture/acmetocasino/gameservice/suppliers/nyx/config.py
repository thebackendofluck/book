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
gameservice.suppliers.nyx.config — NYX Configuration
======================================================
"""

from __future__ import annotations

from pydantic import Field

from acmetocasino.gameservice.suppliers.settings import SupplierConfig


class NYXConfig(SupplierConfig):
    """NYX / Scientific Games configuration.

    Attributes
    ----------
    operator_id:
        NYX operator identifier.
    feed_url:
        URL for the NYX game feed (list of available games).
    bonus_api_url:
        URL for the NYX bonus/free-rounds award API.
    """

    operator_id: str = Field(default="", description="NYX operator identifier.")
    feed_url: str = Field(default="", description="NYX game feed URL.")
    bonus_api_url: str = Field(default="", description="NYX bonus API URL.")


__all__ = ["NYXConfig"]
