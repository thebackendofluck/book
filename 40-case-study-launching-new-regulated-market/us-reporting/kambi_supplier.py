# Companion code for "The Backend of Luck" - Chapter 40, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Kambi Supplier
# Source: Production casino platform (sanitized)
# Chapter 40 - Case Study
#
# Sports betting data queried directly from the platform database.
# Unlike IGT/NetEnt/Evolution which deliver CSV files via SFTP,
# Kambi data lives in the platform's own PostgreSQL schema.
#
# The casino day boundary (e.g., 06:00 AM Eastern) must be applied
# when querying to align with the regulatory reporting period.
# =============================================================================

from __future__ import annotations

import logging
from datetime import datetime, time
from decimal import Decimal
from typing import Sequence
from zoneinfo import ZoneInfo

from models import GameType, StuckBetRow, WsrData
from reporting_supplier import ReportingSupplier

logger = logging.getLogger(__name__)


class KambiSupplier(ReportingSupplier):
    """
    Fetches Kambi sports betting totals from the platform database,
    grouped by event category, applying timezone-aware casino day start time.
    """

    def __init__(self, casino_day_start_time: time, timezone: ZoneInfo) -> None:
        self._start_time = casino_day_start_time
        self._tz = timezone

    @property
    def name(self) -> str:
        return "KAMBI"

    def _to_utc_start(self, from_date: datetime) -> datetime:
        """Convert the casino day start time to UTC for querying."""
        local_start = datetime(
            from_date.year, from_date.month, from_date.day,
            self._start_time.hour, self._start_time.minute,
        ).replace(tzinfo=self._tz)
        return local_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    def get_wsr_data(self, db, from_date: datetime) -> list[WsrData]:
        """
        Query sports betting totals grouped by event category.
        Applies timezone-aware casino day start time.
        """
        logger.info("Running Kambi supplier for date %s", from_date.date())
        utc_start = self._to_utc_start(from_date)

        rows = db.execute(
            """
            SELECT
                event_group_id,
                event_group,
                COALESCE(SUM(bonus), 0)           AS bonus,
                COALESCE(SUM(cash), 0)            AS cash,
                COALESCE(SUM(resettled_amount), 0) AS resettled_amount,
                COALESCE(SUM(voided_bets), 0)     AS voided_bets,
                COALESCE(SUM(total_stake), 0)     AS total_stake,
                COALESCE(SUM(cash_win), 0)        AS cash_win,
                COALESCE(SUM(bonus_win), 0)       AS bonus_win,
                COALESCE(SUM(total_payout), 0)    AS total_payout,
                COALESCE(SUM(total_win), 0)       AS total_win
            FROM bmc_games_totals
            WHERE from_date = %s
            GROUP BY event_group_id, event_group
            """,
            (utc_start,),
        )

        return [self._map_row(row) for row in rows]

    def _map_row(self, row) -> WsrData:
        event_group_id = row["event_group_id"]
        return WsrData(
            game_provider="Kambi",
            game_type_id="" if event_group_id == -1 else str(event_group_id),
            product_desc=row.get("event_group") or "",
            game_type=GameType.SPORTS,
            bonus_wager=Decimal(str(row["bonus"])),
            cash_wager=Decimal(str(row["cash"])),
            resettled_bets=Decimal(str(row["resettled_amount"])),
            voided_bets=Decimal(str(row["voided_bets"])),
            total_wager=Decimal(str(row["total_stake"])),
            cash_win_amount=Decimal(str(row["cash_win"])),
            bonus_win_amount=Decimal(str(row["bonus_win"])),
            win_amount=Decimal(str(row["total_payout"])),
            win_or_loss_amount=Decimal(str(row["total_win"])),
        )

    def get_stuck_bets(self, db, from_date: datetime) -> list[StuckBetRow]:
        # Kambi settles bets in real time; stuck bet detection is not applicable
        return []
