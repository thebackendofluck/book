# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Wagering requirement engine.

Implements weighted contribution rules per bet type, based on common
industry practice and Portaria MF 615/2023 bonus deductibility guidelines.

Contribution weights:
  Sports pre-match : 100%
  Sports live      :  80%
  Casino slots     : 100%
  Casino table     :  20%  (blackjack, baccarat, roulette)
  Live casino      :  10%

A wagering requirement is considered cleared when:
  total_wagered_weighted >= bonus_amount × wagering_multiplier
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from models import WageringContribution, WageringRequirement, WageringCheckResponse

logger = logging.getLogger(__name__)

# ── Contribution table ────────────────────────────────────────────────────────

CONTRIBUTION_WEIGHTS: dict[WageringContribution, Decimal] = {
    WageringContribution.SPORTS_PRE_MATCH: Decimal("1.00"),
    WageringContribution.SPORTS_LIVE:      Decimal("0.80"),
    WageringContribution.CASINO_SLOTS:     Decimal("1.00"),
    WageringContribution.CASINO_TABLE:     Decimal("0.20"),
    WageringContribution.LIVE_CASINO:      Decimal("0.10"),
}


class WageringEngine:
    """Stateless wagering calculation engine."""

    def create_requirement(
        self,
        bonus_id: UUID,
        cpf: str,
        bonus_amount: Decimal,
        wagering_multiplier: int,
    ) -> WageringRequirement:
        total = bonus_amount * Decimal(wagering_multiplier)
        logger.info(
            f"Creating wagering requirement: cpf={cpf} bonus_id={bonus_id} "
            f"amount={bonus_amount} multiplier={wagering_multiplier} total={total}"
        )
        return WageringRequirement(
            bonus_id       = bonus_id,
            cpf            = cpf,
            total_required = total,
            remaining      = total,
        )

    def apply_wager(
        self,
        requirement: WageringRequirement,
        wager_amount: Decimal,
        bet_type: WageringContribution,
        bet_id: str | None = None,
    ) -> WageringRequirement:
        """Apply a wager to a requirement and return the updated requirement.

        `bet_id`, when provided, makes this call idempotent: a settlement
        event replayed (e.g. webhook retry) must not credit the same bet
        twice toward the rollover requirement.
        """
        if requirement.completed:
            logger.debug(f"Wagering already completed for bonus {requirement.bonus_id}")
            return requirement

        if bet_id is not None and bet_id in requirement.processed_bet_ids:
            logger.info(
                f"Bet {bet_id} already credited to bonus {requirement.bonus_id}; "
                f"skipping duplicate settlement event"
            )
            return requirement

        weight         = CONTRIBUTION_WEIGHTS.get(bet_type, Decimal("1.00"))
        effective_amount = wager_amount * weight
        new_total      = requirement.total_wagered + effective_amount

        # Track per-type contribution
        contributions  = dict(requirement.contributions)
        key            = bet_type.value
        contributions[key] = contributions.get(key, Decimal("0")) + effective_amount

        processed_bet_ids = list(requirement.processed_bet_ids)
        if bet_id is not None:
            processed_bet_ids.append(bet_id)

        updated = requirement.model_copy(update={
            "total_wagered": new_total,
            "remaining":     max(Decimal("0"), requirement.total_required - new_total),
            "contributions": contributions,
            "processed_bet_ids": processed_bet_ids,
        })

        # Recompute completion
        if updated.total_wagered >= updated.total_required:
            from datetime import datetime, timezone
            updated = updated.model_copy(update={
                "completed":    True,
                "completed_at": datetime.now(timezone.utc),
                "remaining":    Decimal("0"),
            })
            logger.info(
                f"Wagering requirement completed: cpf={requirement.cpf} "
                f"bonus_id={requirement.bonus_id}"
            )

        return updated

    def build_response(
        self, cpf: str, req: WageringRequirement
    ) -> WageringCheckResponse:
        pct = (
            float(req.total_wagered / req.total_required * 100)
            if req.total_required > 0 else 100.0
        )
        return WageringCheckResponse(
            cpf                 = cpf,
            wagering_completed  = req.completed,
            total_required      = str(req.total_required),
            total_wagered       = str(req.total_wagered),
            remaining           = str(req.remaining),
            percentage_done     = round(min(100.0, pct), 2),
        )

    def validate_bet_type(self, bet_type_str: str) -> WageringContribution:
        try:
            return WageringContribution(bet_type_str.upper())
        except ValueError:
            raise ValueError(
                f"Unknown bet type: {bet_type_str}. "
                f"Valid types: {[e.value for e in WageringContribution]}"
            )
