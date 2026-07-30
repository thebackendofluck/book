# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""SIGAP free-bet deductibility tracking.

Implements Portaria MF 615/2023 ("Portaria de Apostas") requirements for
tracking and reporting free bets that are deductible from Gross Gaming
Revenue (GGR) in SIGAP.

Key regulatory rules (Art. 18, §§ 2–5):
  1. Free bet must be used within the campaign validity window.
  2. Only the net player WIN portion is deductible — the face value of the
     stake returned to the player (if any) is NOT deductible.
  3. Free bets used on prohibited bet types are not deductible.
  4. The operator must maintain records for 5 years (Art. 47).
  5. Monthly SIGAP report must be submitted by the 10th of the following month.

This module is responsible for:
  - Validating whether a free bet outcome qualifies for GGR deduction
  - Computing the deductible amount
  - Generating the monthly SIGAP Anexo III report
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from models import FreeBet, SigapBonusRecord, SigapBonusReport

logger = logging.getLogger(__name__)

OPERATOR_CNPJ = os.getenv("OPERATOR_CNPJ", "00.000.000/0001-00")

# Bet types that cannot contribute to GGR deductibility (Portaria 615 Anexo I)
PROHIBITED_BET_TYPES = frozenset(["VIRTUAL_SPORTS", "ESPORTS_UNREGULATED"])


class SigapBonusTracker:
    """Tracks free bet outcomes and generates SIGAP monthly reports."""

    def __init__(self) -> None:
        # In production these would be persisted to PostgreSQL
        self._free_bets: dict[str, FreeBet] = {}

    # ── Free bet lifecycle ────────────────────────────────────────────────────

    def register_free_bet(self, free_bet: FreeBet) -> FreeBet:
        """Register a new free bet issued to a player."""
        self._free_bets[str(free_bet.free_bet_id)] = free_bet
        logger.info(
            f"Registered free bet {free_bet.free_bet_id} "
            f"cpf={free_bet.cpf} value={free_bet.face_value}"
        )
        return free_bet

    def record_outcome(
        self,
        free_bet_id: str,
        outcome: str,              # WIN | LOSS | VOID
        gross_win: Decimal,        # total payout before subtracting stake
        bet_type: str = "SPORTS_PRE_MATCH",
    ) -> FreeBet:
        """Record the result of a free bet wager and compute deductibility."""
        fb = self._free_bets.get(free_bet_id)
        if not fb:
            raise KeyError(f"Free bet {free_bet_id} not found")

        now = datetime.now(timezone.utc)

        # Check validity window
        valid_until = fb.valid_until
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        if now > valid_until:
            logger.warning(f"Free bet {free_bet_id} used after expiry — not deductible")
            fb = fb.model_copy(update={
                "used": True, "used_at": now, "outcome": outcome,
                "deductible_from_ggr": False,
            })
            self._free_bets[free_bet_id] = fb
            return fb

        # Compute net player win
        # If stake_replaced=True, the player gets face_value back on a win,
        # so net profit = gross_win - face_value.
        # The deductible amount under Portaria 615 is the net player profit only.
        if outcome == "WIN":
            net_win = gross_win - (fb.face_value if fb.stake_replaced else Decimal("0"))
        elif outcome == "VOID":
            net_win = Decimal("0")
        else:  # LOSS
            net_win = Decimal("0")

        deductible = (
            outcome in ("WIN",)
            and bet_type.upper() not in PROHIBITED_BET_TYPES
            and fb.sigap_reportable
        )

        fb = fb.model_copy(update={
            "used":               True,
            "used_at":            now,
            "outcome":            outcome,
            "net_player_win":     net_win,
            "deductible_from_ggr": deductible,
        })
        self._free_bets[free_bet_id] = fb
        logger.info(
            f"Free bet {free_bet_id} outcome={outcome} net_win={net_win} "
            f"deductible={deductible}"
        )
        return fb

    # ── SIGAP reporting ───────────────────────────────────────────────────────

    def generate_monthly_report(self, period: str) -> SigapBonusReport:
        """Generate SIGAP Anexo III report for the given period (YYYY-MM).

        Must be called after month close and submitted by the 10th of next month.
        """
        year, month = period.split("-")
        period_start = datetime(int(year), int(month), 1, tzinfo=timezone.utc)
        if int(month) == 12:
            period_end = datetime(int(year) + 1, 1, 1, tzinfo=timezone.utc)
        else:
            period_end = datetime(int(year), int(month) + 1, 1, tzinfo=timezone.utc)

        records: list[SigapBonusRecord] = []
        total_face_value  = Decimal("0")
        total_deductible  = Decimal("0")

        for fb in self._free_bets.values():
            # Only include free bets used in this period
            used_at = fb.used_at
            if used_at is None:
                continue
            if used_at.tzinfo is None:
                used_at = used_at.replace(tzinfo=timezone.utc)
            if not (period_start <= used_at < period_end):
                continue

            total_face_value += fb.face_value
            if fb.deductible_from_ggr:
                total_deductible += fb.net_player_win

            records.append(SigapBonusRecord(
                report_period  = period,
                campaign_id    = str(fb.bonus_id),
                campaign_name  = "FREE_BET_CAMPAIGN",
                bonus_type     = "FREE_BET",
                cpf            = fb.cpf,
                face_value     = str(fb.face_value),
                used           = fb.used,
                deductible     = fb.deductible_from_ggr,
                net_player_win = str(fb.net_player_win),
                reported_at    = datetime.now(timezone.utc).isoformat(),
            ))

        report = SigapBonusReport(
            report_id         = f"SIGAP-BONUS-{period}-{uuid.uuid4().hex[:8].upper()}",
            period            = period,
            operator_cnpj     = OPERATOR_CNPJ,
            total_free_bets   = len(records),
            total_face_value  = str(total_face_value),
            total_deductible  = str(total_deductible),
            records           = records,
            generated_at      = datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            f"SIGAP bonus report {report.report_id}: "
            f"{report.total_free_bets} free bets, "
            f"total_face_value={total_face_value}, "
            f"deductible={total_deductible}"
        )
        return report

    def get_free_bet(self, free_bet_id: str) -> FreeBet | None:
        return self._free_bets.get(free_bet_id)

    def get_player_free_bets(self, cpf: str) -> list[FreeBet]:
        return [fb for fb in self._free_bets.values() if fb.cpf == cpf]
