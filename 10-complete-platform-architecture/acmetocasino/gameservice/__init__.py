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
gameservice — Game-Session Domain
==================================

Responsible for the full lifecycle of a game session:

* Player authentication and session management
* Balance enquiry and real-time wallet operations
* Debit / credit / rollback round lifecycle
* Bonus activation and free-round orchestration
* Responsible-gambling checks (reality-check, session limits)
* Multi-jurisdiction compliance enforcement
* Idempotent transaction deduplication

Public surface
--------------
Most callers interact with :class:`~acmetocasino.gameservice.accounts_bridge.AccountsBridge`,
which coordinates all of the above through a single entry point.
"""

from __future__ import annotations
