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
gameservice.suppliers.evolution — Evolution Gaming Integration
==============================================================

Evolution Gaming is the world's leading live-casino supplier.  This package
provides the adapter for integrating Evolution's PUSH-style wallet callback API.

Integration pattern: PUSH
-------------------------
Unlike SEAMLESS integrations (where the supplier calls our wallet API for every
bet), Evolution uses a PUSH model:

1. The operator registers a set of webhook endpoints with Evolution.
2. When a player tips a dealer, places a side bet, or a round settles,
   Evolution POSTs a signed webhook payload to the operator's server.
3. The operator processes the event and responds with the updated balance.

This is architecturally different from SEAMLESS — the platform must expose
inbound HTTP endpoints (handled by the API layer) rather than making outbound
calls per transaction.  The adapter therefore focuses on:

* Session launch (outbound call to Evolution's session API).
* Webhook event handling (invoked by the API layer on inbound POST).
* HMAC signature verification for all inbound events.

Live-casino-specific features
------------------------------
* **Tipping**: Players can send monetary tips to live dealers.  Tips are
  treated as debits with ``CommandType.TIP``.
* **Multi-seat tables**: A player can sit at multiple seats simultaneously.
  Each seat has its own ``seat_id`` threaded through the session context.
* **Dealer-initiated events**: The dealer can trigger re-shuffles, game
  pauses, etc. that the platform must acknowledge.

Public API::

    from acmetocasino.gameservice.suppliers.evolution import EvolutionAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.evolution.adapter import EvolutionAdapter

__all__ = ["EvolutionAdapter"]
