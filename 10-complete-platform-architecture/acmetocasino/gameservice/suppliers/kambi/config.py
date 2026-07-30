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
gameservice.suppliers.kambi.config — Kambi Configuration
=========================================================

Kambi credentials:

* ``api_key``      — Bearer token for all Kambi REST calls.
* ``offering_url`` — The operator's Kambi offering URL (e.g. acmekambi).
* ``brand_id``     — Kambi brand/client identifier.
* ``feed_url``     — WebSocket URL for the live odds feed.
"""

from __future__ import annotations

from pydantic import Field

from acmetocasino.gameservice.suppliers.settings import SupplierConfig


class KambiConfig(SupplierConfig):
    """Kambi-specific configuration.

    Attributes
    ----------
    offering_url:
        Kambi offering URL slug (e.g. ``"acme"`` → ``"api.kambi.com/acme/...``).
    brand_id:
        Kambi client/brand identifier.
    feed_url:
        WebSocket URL for the live odds/feed API.
    settlement_poll_interval_seconds:
        How often to poll Kambi's settlement feed.
    """

    offering_url: str = Field(default="", description="Kambi offering URL slug.")
    brand_id: str = Field(default="", description="Kambi brand/client identifier.")
    feed_url: str = Field(default="", description="WebSocket URL for live odds feed.")
    settlement_poll_interval_seconds: int = Field(
        default=60,
        description="Interval in seconds for polling the settlement feed.",
    )


__all__ = ["KambiConfig"]
