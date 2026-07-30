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
acmetocasino — iGaming Platform
================================

A didactic, production-grade iGaming platform implementation demonstrating
modern Python patterns for game-service integration, wallet management,
responsible-gambling controls, and multi-jurisdiction compliance.

Architecture overview
---------------------
acmetocasino/
├── gameservice/     — Core game-service domain (sessions, transactions, accounts)
│   ├── accounts/    — Wallet, bonus, limits, and ledger services
│   └── models/      — Shared domain value objects
└── platform/        — Infrastructure utilities (config, DB, retry, feature flags)
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
