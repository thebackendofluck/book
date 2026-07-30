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
gameservice.suppliers.netent — NetEnt Integration
==================================================

NetEnt (part of Evolution Group since 2020) is a premium slot-game supplier.
This package implements their SEAMLESS wallet API.

NetEnt quirk: multiple credits per round
-----------------------------------------
NetEnt can emit **multiple credit callbacks** for the same round (e.g. a base
win followed by a free-spins win, each as a separate credit).  The platform
must handle this correctly:

* Each credit carries the same ``roundId`` but a unique ``transactionId``.
* The round is only closed when NetEnt sends a credit with ``roundEnded=true``.
* All credits for the same round must be accumulated before closing the round.

Public API::

    from acmetocasino.gameservice.suppliers.netent import NetEntAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.netent.adapter import NetEntAdapter

__all__ = ["NetEntAdapter"]
