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
gameservice.suppliers.igt — IGT Integration
============================================

IGT (International Game Technology) is a land-based casino giant with an
online division.  Their integration is notably more complex than pure-online
suppliers due to a hybrid REST/SOAP architecture.

Integration pattern: PULL (+ SOAP legacy layer)
-------------------------------------------------
IGT's older systems use a SOAP/XML API while newer endpoints are REST/JSON.
The adapter handles both:

* **REST** endpoints for session launch and game launch.
* **SOAP** endpoints for jackpot pool queries and regulatory reporting.
* **PULL** settlement model: the platform polls IGT's settlement feed for
  round-closed events rather than receiving real-time SEAMLESS callbacks.

Progressive jackpot pools
--------------------------
IGT operates the MegaJackpots network — a linked progressive jackpot pool
shared across multiple operators.  The adapter exposes
:meth:`query_jackpot_pool` for querying the current pool values, and
:meth:`register_jackpot_win` for processing a jackpot award.

Regulatory reporting
--------------------
IGT maintains land-based game references (game type codes, denomination codes)
that regulatory reports must include.  The translator maps these codes to
platform-neutral identifiers.

Public API::

    from acmetocasino.gameservice.suppliers.igt import IGTAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.igt.adapter import IGTAdapter

__all__ = ["IGTAdapter"]
