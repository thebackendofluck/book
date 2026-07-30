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
gameservice.suppliers.kambi — Kambi Sportsbook Integration
===========================================================

Kambi is a B2B sportsbook platform.  This package implements the PULL-style
integration where the platform polls Kambi for bet settlement data.

Integration pattern: PULL
--------------------------
Unlike SEAMLESS/PUSH, Kambi does not call the platform's wallet API directly
for every bet.  Instead:

1. **Bet placement**: The player places a bet via the Kambi web client.  Kambi
   notifies the platform via a bet receipt webhook (PUSH for bet events, but
   the wallet is settled separately).
2. **Balance query**: Kambi queries the operator's balance endpoint before
   presenting the bet slip.
3. **Settlement**: The platform periodically polls Kambi's settlement feed to
   retrieve settled bet data and apply credits/debits to the wallet.
4. **Cash-out**: Players can cash out open bets via Kambi's cash-out API.

Live betting and cash-out
--------------------------
Kambi provides real-time odds feeds via REST + WebSocket.  The adapter's
:meth:`get_odds_feed` method returns the current odds state for a set of
fixture IDs.

Public API::

    from acmetocasino.gameservice.suppliers.kambi import KambiAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.kambi.adapter import KambiAdapter

__all__ = ["KambiAdapter"]
