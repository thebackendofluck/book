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
gameservice.suppliers.hacksaw — Hacksaw Gaming Integration
===========================================================

Hacksaw Gaming is a Malta-based studio specialising in crash games, slots, and
scratch cards.  This package implements their SEAMLESS wallet API.

Hacksaw-specific features
--------------------------
* **Crash games**: Hacksaw's crash games (e.g. "Mines", "Plinko") have a
  different round lifecycle — the player can cash out at any multiplier before
  the round ends.  This is modelled as a credit before the round closes.
* **Tournaments**: Hacksaw exposes an operator-configured tournament API where
  players compete for a leaderboard position.
* **Bonus Buy**: Select Hacksaw slots support direct bonus feature purchases.

Public API::

    from acmetocasino.gameservice.suppliers.hacksaw import HacksawAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.hacksaw.adapter import HacksawAdapter

__all__ = ["HacksawAdapter"]
