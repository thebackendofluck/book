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
gameservice.suppliers.pragmatic — Pragmatic Play Integration
=============================================================

Pragmatic Play is a multi-product supplier offering slots, live casino, and
virtual sports.  This package wraps their SEAMLESS wallet callback API.

Integration pattern: SEAMLESS
------------------------------
In SEAMLESS integrations the supplier's game server calls the operator's wallet
API directly for every bet and win.  The platform exposes inbound REST endpoints
that Pragmatic calls in real time.

Pragmatic-specific features
-----------------------------
* **Free rounds (Free Spins)**: Operator awards free spins via the Pragmatic
  back-office or bonus API.  The supplier reports free-spin rounds with a
  special ``actionId`` that maps to ``ActionCode.FREE_SPIN``.
* **Jackpots**: Pragmatic contributes a configurable percentage of each bet to
  a jackpot pool.  Jackpot wins arrive as ``CREDIT`` events.
* **Drops & Wins**: Pragmatic's tournament engine can credit prizes directly to
  the player's wallet.  These appear as ``PROMO_CREDIT`` event types.
* **Bonus Buy**: Players can purchase the bonus feature on qualifying slots.
  These bets arrive with ``actionId="BONUS_BUY"`` and command type DEBIT.

Public API::

    from acmetocasino.gameservice.suppliers.pragmatic import PragmaticAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.pragmatic.adapter import PragmaticAdapter

__all__ = ["PragmaticAdapter"]
