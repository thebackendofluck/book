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
gameservice.accounts — Wallet Domain Services
==============================================

This package contains the core financial services that underpin every
real-money game session.  Each service has a single, focused responsibility:

``wallet_service``
    Reserve, commit, release, and credit funds.  The reservation pattern
    ensures a debit is held atomically before a game round begins.

``bonus_service``
    Allocate promotional bonuses, track wagering progress, and forfeit
    unearned bonuses.

``limits_service``
    Evaluate deposit, loss, wager, and session-duration limits before
    each transaction.

``balance_policy``
    Decide the order in which fund sources (cash vs bonus) are consumed,
    and calculate wagering contributions per supplier/game type.

``ledger_adapter``
    Record immutable double-entry ledger entries and expose a
    reconciliation interface.

All services are designed to be stateless (state lives in the wallet store
or ledger), facilitating horizontal scaling.  The in-memory implementations
provided here are suitable for unit tests and local development.
"""

from __future__ import annotations

from acmetocasino.gameservice.accounts.balance_policy import BalancePolicy
from acmetocasino.gameservice.accounts.bonus_service import BonusService
from acmetocasino.gameservice.accounts.ledger_adapter import (
    InMemoryLedgerAdapter,
    LedgerAdapter,
    LedgerEntry,
    ReconciliationResult,
)
from acmetocasino.gameservice.accounts.limits_service import (
    LimitCheckResult,
    LimitsService,
)
from acmetocasino.gameservice.accounts.wallet_service import WalletService

__all__ = [
    "BalancePolicy",
    "BonusService",
    "InMemoryLedgerAdapter",
    "LedgerAdapter",
    "LedgerEntry",
    "LimitCheckResult",
    "LimitsService",
    "ReconciliationResult",
    "WalletService",
]
