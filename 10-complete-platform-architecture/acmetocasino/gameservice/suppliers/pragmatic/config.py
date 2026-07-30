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
gameservice.suppliers.pragmatic.config — Pragmatic Play Configuration
======================================================================

Pragmatic Play uses two primary credentials:

* ``secure_login`` — The operator's login identifier for Pragmatic's API.
* ``secret_key``   — HMAC-MD5 signing key for request authentication.

Every API call must include an MD5 hash of the parameter string concatenated
with the ``secret_key``.  See :mod:`translator` for the signing logic.
"""

from __future__ import annotations

from pydantic import Field

from acmetocasino.gameservice.suppliers.settings import SupplierConfig


class PragmaticConfig(SupplierConfig):
    """Pragmatic Play configuration extending the base SupplierConfig.

    Attributes
    ----------
    secure_login:
        Operator login string provided by Pragmatic Play.
    secret_key:
        HMAC-MD5 signing key for API request authentication.
    casino_id:
        Optional alternative operator identifier used in some Pragmatic APIs.
    free_rounds_endpoint:
        Endpoint for awarding free rounds (may differ from the main API URL).
    """

    secure_login: str = Field(default="", description="Pragmatic Play operator login.")
    secret_key: str = Field(default="", description="HMAC-MD5 signing key.")
    casino_id: str = Field(default="", description="Alternative operator identifier.")
    free_rounds_endpoint: str = Field(
        default="",
        description="Endpoint for awarding free rounds via Pragmatic back-office.",
    )


__all__ = ["PragmaticConfig"]
