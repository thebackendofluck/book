# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Configuration for the Betgenius sportsbook adapter."""

from __future__ import annotations

from pydantic import Field

from acmetocasino.gameservice.suppliers.settings import SupplierConfig


class BetgeniusConfig(SupplierConfig):
    """Betgenius-specific supplier configuration."""

    launch_url: str = Field(default="", description="Embedded sportsbook launch URL.")
    webhook_secret: str = Field(default="", description="Webhook signature secret.")
    event_timeout_seconds: int = Field(
        default=30,
        description="Maximum age of a pushed wallet event before rejection.",
    )


__all__ = ["BetgeniusConfig"]
