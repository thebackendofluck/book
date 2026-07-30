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
gameservice.suppliers.nyx — NYX / Scientific Games Integration
===============================================================

NYX Gaming Group (acquired by Scientific Games, now Light & Wonder) is a
game aggregator that distributes titles from multiple studios via a single
integration.  This package implements their SEAMLESS aggregator wallet API.

Multi-studio aggregation
------------------------
NYX acts as a middleware layer: the operator integrates once with NYX and
gains access to games from NYX's own studios as well as partner studios.
The ``studioId`` field in each callback identifies the originating studio,
which may affect wagering contribution rates or bonus eligibility.

Free rounds distribution
------------------------
NYX supports free-round awards natively.  The operator calls NYX's bonus API
to award free spins; NYX coordinates with the specific studio and returns the
results to the platform via the standard SEAMLESS wallet callbacks.

Public API::

    from acmetocasino.gameservice.suppliers.nyx import NYXAdapter
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.nyx.adapter import NYXAdapter

__all__ = ["NYXAdapter"]
