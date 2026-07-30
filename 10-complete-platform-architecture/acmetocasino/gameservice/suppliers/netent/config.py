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
gameservice.suppliers.netent.config — NetEnt Configuration
===========================================================

NetEnt requires:

* ``casino_id``      — Operator identifier assigned by NetEnt.
* ``game_server_url`` — NetEnt's game hosting domain (environment-specific).
* ``wallet_url``     — The platform's wallet callback URL registered with NetEnt.

NetEnt issues a ``casinoSessionId`` on every launch which must be stored and
used for all subsequent wallet callbacks for that session.
"""

from __future__ import annotations

from pydantic import Field

from acmetocasino.gameservice.suppliers.settings import SupplierConfig


class NetEntConfig(SupplierConfig):
    """NetEnt-specific configuration.

    Attributes
    ----------
    casino_id:
        NetEnt operator/casino identifier.
    game_server_url:
        Base URL for NetEnt's game server (differs per environment).
    static_server_url:
        URL for static game assets (optional, for custom CDN configuration).
    """

    casino_id: str = Field(default="", description="NetEnt casino/operator ID.")
    game_server_url: str = Field(
        default="",
        description="NetEnt game server base URL.",
    )
    static_server_url: str = Field(
        default="",
        description="NetEnt static assets server URL (optional).",
    )


__all__ = ["NetEntConfig"]
