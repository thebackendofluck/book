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
gameservice.suppliers.push_gaming — Push Gaming Integration
============================================================

Push Gaming is a UK-based slot studio known for titles like Jammin' Jars.
This package implements their SEAMLESS wallet callback API.

Public API::

    from acmetocasino.gameservice.suppliers.push_gaming import PushGamingAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.push_gaming.adapter import PushGamingAdapter

__all__ = ["PushGamingAdapter"]
