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
# Wagering Summary Report (WSR)
# Source: Production casino platform (sanitized)
# Chapter 40 - Case Study
#
# Aggregates wager and payout data across all suppliers into the regulatory
# format. Produces four sheets:
#   - Master WSR (all game types combined)
#   - Table WSR
#   - Sport WSR
#   - Slot WSR
# =============================================================================

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from models import GameType, WsrData
from reporting_supplier import ReportingSupplier

logger = logging.getLogger(__name__)

# Column order for the WSR sheet (matches regulator template)
WSR_COLUMNS = [
    "Game Provider",
    "Game Type Id",
    "Product Desc",
    "Game Type",
    "Bonus Wager",
    "Cash Wager",
    "Resettled Bets",
    "Cancelled/Voided Bets",
    "Total Wager",
    "Win Amount (cash)",
    "Win Amount (bonus)",
    "Win Amount",
    "Win or Loss Amount",
]


def _row_to_dict(row: WsrData) -> dict:
    return {
        "Game Provider":      row.game_provider,
        "Game Type Id":       row.game_type_id,
        "Product Desc":       row.product_desc,
        "Game Type":          row.game_type,
        "Bonus Wager":        row.bonus_wager,
        "Cash Wager":         row.cash_wager,
        "Resettled Bets":     row.resettled_bets,
        "Cancelled/Voided Bets": row.voided_bets,
        "Total Wager":        row.total_wager,
        "Win Amount (cash)":  row.cash_win_amount,
        "Win Amount (bonus)": row.bonus_win_amount,
        "Win Amount":         row.win_amount,
        "Win or Loss Amount": row.win_or_loss_amount,
    }


def _calculate_totals(rows: list[dict]) -> dict:
    totals: dict = {"Game Provider": "Totals", "Game Type Id": "", "Product Desc": "ALL", "Game Type": "ALL"}
    for col in WSR_COLUMNS[4:]:
        totals[col] = sum(r.get(col, Decimal(0)) for r in rows)
    return totals


def build_wsr_sheet(
    report_name: str,
    from_date: datetime,
    data: list[WsrData],
) -> list[dict]:
    """Return a list of dicts (header + totals + data) for the given WSR sheet."""
    rows = [_row_to_dict(r) for r in data]
    totals = _calculate_totals(rows)
    header = {col: col for col in WSR_COLUMNS}
    title = {WSR_COLUMNS[0]: f"AcmeSports Online Sportsbook — {report_name} — {from_date.strftime('%m/%d/%Y')}"}
    return [title, header, totals] + rows


def write_sheet_to_csv(sheet: list[dict], output: io.StringIO) -> None:
    writer = csv.DictWriter(output, fieldnames=WSR_COLUMNS, extrasaction="ignore")
    for row in sheet:
        writer.writerow(row)


class WsrReports:
    """
    Entry point: collects data from all suppliers, enriches with platform
    stats, and segments into game-type-specific sheets.
    """

    def create(
        self,
        db,
        from_date: datetime,
        suppliers: Sequence[ReportingSupplier],
        output_dir: str = ".",
    ) -> dict[str, list[dict]]:
        """
        Collect data from all suppliers and produce WSR sheets.

        Returns a dict of sheet_name -> list[row_dict].
        """
        all_data: list[WsrData] = []
        for supplier in suppliers:
            try:
                data = supplier.get_wsr_data(db, from_date)
                all_data.extend(data)
            except Exception as exc:
                logger.error("Failed to fetch WSR data from %s: %s", supplier.name, exc)

        # Cross-reference with platform stats for Table/Slot games
        all_data = self._enrich_with_analytics_dw(db, from_date, all_data)

        segmented = defaultdict(list)
        for row in all_data:
            segmented[row.game_type].append(row)

        sheets = {
            "Master WSR": build_wsr_sheet("Master WSR", from_date, all_data),
            "Table WSR":  build_wsr_sheet("Table WSR",  from_date, segmented.get(GameType.TABLE, [])),
            "Sport WSR":  build_wsr_sheet("Sport WSR",  from_date, segmented.get(GameType.SPORTS, [])),
            "Slot WSR":   build_wsr_sheet("Slot WSR",   from_date, segmented.get(GameType.SLOT, [])),
        }
        return sheets

    def _enrich_with_analytics_dw(
        self, db, from_date: datetime, data: list[WsrData]
    ) -> list[WsrData]:
        """
        For Table and Slot games, override supplier-reported amounts with
        authoritative platform data (cash and bonus win/wager separated).
        """
        if db is None:
            return data

        game_ids = [r.game_type_id for r in data if r.game_type in (GameType.TABLE, GameType.SLOT)]
        if not game_ids:
            return data

        try:
            rows = db.execute(
                """
                SELECT sgc.supplier_code,
                       SUM(dpgs.cash_return)  AS cash_return,
                       SUM(dpgs.bonus_return) AS bonus_return,
                       SUM(dpgs.cash_stake)   AS cash_stake,
                       SUM(dpgs.bonus_stake)  AS bonus_stake
                FROM supplier_game_codes sgc
                JOIN daily_player_game_stats dpgs ON dpgs.game_id = sgc.game_id
                WHERE sgc.supplier_code = ANY(%s)
                  AND dpgs.on_date = %s
                GROUP BY sgc.supplier_code
                """,
                (game_ids, from_date),
            )
            stats = {r["supplier_code"]: r for r in rows}
        except Exception as exc:
            logger.error("Failed to fetch platform stats for WSR: %s", exc)
            return data

        enriched = []
        for row in data:
            if row.game_type in (GameType.TABLE, GameType.SLOT) and row.game_type_id in stats:
                s = stats[row.game_type_id]
                row = WsrData(
                    game_provider=row.game_provider,
                    game_type_id=row.game_type_id,
                    product_desc=row.product_desc,
                    game_type=row.game_type,
                    bonus_wager=Decimal(str(s.get("bonus_stake") or 0)),
                    cash_wager=Decimal(str(s.get("cash_stake") or 0)),
                    resettled_bets=row.resettled_bets,
                    voided_bets=row.voided_bets,
                    total_wager=row.total_wager,
                    cash_win_amount=Decimal(str(s.get("cash_return") or 0)),
                    bonus_win_amount=Decimal(str(s.get("bonus_return") or 0)),
                    win_amount=row.win_amount,
                    win_or_loss_amount=row.win_or_loss_amount,
                )
            enriched.append(row)
        return enriched
