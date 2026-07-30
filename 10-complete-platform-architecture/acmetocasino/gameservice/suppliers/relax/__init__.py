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
gameservice.suppliers.relax — Relax Gaming Integration
=======================================================

Relax Gaming is a B2B aggregator and game studio known for the
"Silver Bullet" distribution platform.  This package implements their
SEAMLESS wallet API.

Silver Bullet aggregation
--------------------------
Relax Gaming's Silver Bullet program distributes games from multiple partner
studios (e.g. Push Gaming, Hacksaw, Nolimit City) through a single Relax
integration.  The ``partnerStudioId`` field on callbacks identifies which
studio's game was played, similar to NYX's ``studioId``.

Operators who integrate directly with Relax gain access to both Relax's own
titles and all Silver Bullet partner studio content through a single
wallet callback endpoint.

Free rounds
-----------
Relax supports operator-awarded free rounds via their bonus API.  The
``isFreeRound`` flag on callbacks identifies bonus-funded spins.

Tournaments
-----------
Relax's partner studios can participate in operator-configured tournaments.
Tournament results are reported via a separate feed.

Public API::

    from acmetocasino.gameservice.suppliers.relax import RelaxAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.relax.adapter import RelaxAdapter

__all__ = ["RelaxAdapter"]
