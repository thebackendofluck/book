# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Betgenius event-to-domain translation helpers."""

from __future__ import annotations

from decimal import Decimal

from acmetocasino.gameservice.models.enums import ActionCode, CommandType
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.suppliers.betgenius.models import BetgeniusWalletEvent


def event_to_round_command(event: BetgeniusWalletEvent) -> RoundCommand:
    """Translate a pushed Betgenius wallet event into a platform command."""

    transaction_type = event.transactionType.upper()
    command_type = {
        "DEBIT": CommandType.DEBIT,
        "CREDIT": CommandType.CREDIT,
        "ROLLBACK": CommandType.ROLLBACK,
    }.get(transaction_type)
    if command_type is None:
        raise ValueError(f"Unsupported Betgenius transaction type: {transaction_type}")

    return RoundCommand(
        command_type=command_type,
        round_id=event.roundId,
        amount=Decimal(event.amount),
        action_code=ActionCode.REGULAR,
        supplier_ref=event.supplier_ref,
        metadata={
            "event_id": event.eventId,
            "external_customer_id": event.externalCustomerId,
            "product_type": event.product_type,
            "currency": event.currency,
            "occurred_at": event.occurredAt,
        },
    )


__all__ = ["event_to_round_command"]
