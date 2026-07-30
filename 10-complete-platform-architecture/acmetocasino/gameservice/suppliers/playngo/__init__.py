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
gameservice.suppliers.playngo — Play'n GO Integration
======================================================

Play'n GO is a Swedish game studio known for premium slots.  This package
implements their SEAMLESS wallet callback API.

Public API::

    from acmetocasino.gameservice.suppliers.playngo import PlayngoAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.playngo.adapter import PlayngoAdapter

__all__ = ["PlayngoAdapter"]
