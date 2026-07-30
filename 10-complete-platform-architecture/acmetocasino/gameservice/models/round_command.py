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
gameservice.models.round_command — Atomic Wallet Operation
===========================================================

A :class:`RoundCommand` represents a single atomic wallet operation within a
game round.  Suppliers submit one or more commands per round; the platform
applies them in order and returns a single :class:`TransactionResult`.

Multiple commands per round
---------------------------
Some games (e.g. multi-line slots, live blackjack) send a debit and credit
in the same call.  The platform processes them atomically — either all
commands succeed or none are applied.

Example — Standard slot spin::

    commands = [
        RoundCommand(
            command_type=CommandType.DEBIT,
            round_id="round-abc123",
            amount=Decimal("1.00"),
            action_code=ActionCode.REGULAR,
        ),
        RoundCommand(
            command_type=CommandType.CREDIT,
            round_id="round-abc123",
            amount=Decimal("2.50"),
            action_code=ActionCode.REGULAR,
        ),
    ]
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from acmetocasino.gameservice.models.enums import ActionCode, CommandType


class RoundCommand(BaseModel):
    """An atomic wallet operation within a game round.

    Attributes
    ----------
    command_type:
        The kind of operation: DEBIT, CREDIT, ROLLBACK, ADJUST, or TIP.
    round_id:
        The supplier's round identifier.  All commands in a single round
        share the same ``round_id``.
    amount:
        The monetary value of this command.  Must be non-negative.  A zero-
        amount ROLLBACK is valid (no-op balance effect, still recorded).
    action_code:
        Fine-grained classification (e.g. FREE_SPIN, BONUS_BUY) for wagering-
        contribution and regulatory-reporting purposes.
    supplier_ref:
        The supplier's own unique transaction reference.  Used for idempotency
        deduplication; if the platform has already applied a command with this
        ``supplier_ref``, it returns the original result without re-applying.
    metadata:
        Arbitrary supplier-provided key-value context (e.g. jackpot pool ID,
        free-spin sequence number).  Not interpreted by the platform core.
    """

    model_config = {"frozen": True}

    command_type: CommandType = Field(..., description="The wallet operation type.")
    round_id: str = Field(..., min_length=1, description="Supplier round identifier.")
    amount: Decimal = Field(
        ...,
        ge=Decimal("0"),
        description="Transaction amount in the player's currency (non-negative).",
    )
    action_code: ActionCode = Field(
        default=ActionCode.REGULAR,
        description="Fine-grained action classification for reporting.",
    )
    supplier_ref: str | None = Field(
        default=None,
        description="Supplier's unique transaction reference for idempotency.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplier-specific contextual metadata.",
    )

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_to_decimal(cls, v: Any) -> Decimal:
        """Accept int/float/str inputs and coerce to Decimal."""
        if isinstance(v, float):
            # Convert via string to avoid floating-point representation errors.
            return Decimal(str(v))
        return Decimal(v)

    def is_debit(self) -> bool:
        """Return ``True`` if this command removes funds from the wallet."""
        return self.command_type == CommandType.DEBIT

    def is_credit(self) -> bool:
        """Return ``True`` if this command adds funds to the wallet."""
        return self.command_type == CommandType.CREDIT

    def is_rollback(self) -> bool:
        """Return ``True`` if this command reverses a previous debit."""
        return self.command_type == CommandType.ROLLBACK

    def __repr__(self) -> str:
        return (
            f"RoundCommand({self.command_type.value}, "
            f"round={self.round_id!r}, "
            f"amount={self.amount})"
        )


__all__ = ["RoundCommand"]
