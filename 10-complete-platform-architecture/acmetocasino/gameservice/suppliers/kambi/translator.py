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
gameservice.suppliers.kambi.translator — Domain ↔ Kambi Translation
=====================================================================

Handles Kambi-specific data transformations:

* Bet placement payload construction.
* Settlement event parsing into platform TransactionResults.
* Odds feed normalization (Kambi uses decimal odds; some platforms use fractional).
"""

from __future__ import annotations

from decimal import Decimal

from acmetocasino.gameservice.models.enums import ActionCode, CommandType
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.suppliers.kambi.models import (
    KambiBalanceResponse,
    KambiSettlementEvent,
)
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot


def build_balance_response(snapshot: WalletSnapshot) -> KambiBalanceResponse:
    """Convert a WalletSnapshot to a Kambi balance response."""
    return KambiBalanceResponse(
        balance=str(snapshot.cash_balance),
        bonus=str(snapshot.bonus_balance),
        currency=snapshot.currency,
    )


def settlement_to_round_command(event: KambiSettlementEvent) -> RoundCommand | None:
    """Convert a Kambi settlement event to a platform RoundCommand.

    Parameters
    ----------
    event:
        Parsed Kambi settlement event.

    Returns
    -------
    RoundCommand or None
        Returns ``None`` for VOID settlements (no financial effect).
    """
    if event.outcome == "VOID":
        return None

    payout = Decimal(event.payout)
    stake = Decimal(event.stake)

    if event.outcome in ("WIN", "PARTIAL_WIN"):
        # Net win = payout (already includes stake return in Kambi)
        return RoundCommand(
            command_type=CommandType.CREDIT,
            round_id=event.betId,
            amount=payout,
            action_code=ActionCode.REGULAR,
            supplier_ref=f"kambi-settle-{event.betId}",
        )
    elif event.outcome == "LOSS":
        # On a loss, the debit was applied at bet placement; no credit needed.
        return None

    return None


def normalize_decimal_odds(odds_str: str) -> Decimal:
    """Parse and normalise a decimal odds string to Decimal.

    Kambi provides odds as strings like ``"2.50"``.  This function validates
    the value is above 1.0 (the minimum meaningful odds in decimal format).
    """
    odds = Decimal(odds_str)
    if odds <= Decimal("1"):
        raise ValueError(f"Invalid Kambi odds value: {odds!r} (must be > 1.0)")
    return odds


__all__ = [
    "build_balance_response",
    "normalize_decimal_odds",
    "settlement_to_round_command",
]
