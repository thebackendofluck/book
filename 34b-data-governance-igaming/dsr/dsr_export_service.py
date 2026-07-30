# Companion code for "The Backend of Luck" - Chapter 34b, Data Governance for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
DSR Export Service — GDPR Art. 20 Portable Data Export
=======================================================
Generates a structured ZIP archive containing the player's personal data
in portable, machine-readable formats (JSON / CSV) with per-field Art. 20
eligibility flags.

Regulatory context:
  - GDPR Art. 20: Right to data portability — data provided by the subject and
    processed on consent or contract basis must be exportable in structured,
    commonly-used, machine-readable format.
  - GDPR Art. 15: Right of access — full subject access request export.
  - LGPD Art. 18(V): Right to portability.
  - CCPA § 1798.100: Right to know / access.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dsr_export_service")


@dataclass
class ExportField:
    """Represents a single field included in the portable export."""
    name: str
    value: Any
    # GDPR Art. 20 eligibility: field was provided by the subject AND
    # processed under Art. 6(1)(a) consent or Art. 6(1)(b) contract.
    art20_eligible: bool
    category: str   # mirrors PIICategory labels for cross-reference


@dataclass
class ExportManifest:
    player_id: str
    generated_at: str
    regulation: str
    files_included: list[str]
    total_fields_exported: int
    art20_eligible_fields: int


# ---------------------------------------------------------------------------
# Stub data fetchers — production versions query the read-replica or
# data-warehouse via SQLAlchemy / BigQuery client.
# ---------------------------------------------------------------------------

def _fetch_account_data(player_id: str) -> list[ExportField]:
    """Return account profile fields for the player."""
    _ = player_id
    return [
        ExportField("player_id",        player_id,          art20_eligible=False, category="Direct PII"),
        ExportField("email",             "player@example.com", art20_eligible=True,  category="Direct PII"),
        ExportField("full_name",         "Jane Doe",          art20_eligible=True,  category="Direct PII"),
        ExportField("date_of_birth",     "1988-07-14",        art20_eligible=True,  category="Direct PII"),
        ExportField("country",           "MT",                art20_eligible=True,  category="Direct PII"),
        ExportField("phone",             "+356-XXXXXXX",      art20_eligible=True,  category="Direct PII"),
        ExportField("created_at",        "2019-03-15T10:22:00Z", art20_eligible=False, category="Metadata"),
        ExportField("marketing_optin",   True,                art20_eligible=True,  category="Consent"),
    ]


def _fetch_transactions(player_id: str) -> list[dict[str, Any]]:
    """Return transaction history rows."""
    _ = player_id
    return [
        {"txn_id": "TXN-001", "type": "deposit",    "amount_eur": 100.00, "currency": "EUR",
         "timestamp": "2023-01-10T14:30:00Z", "method": "card", "status": "completed"},
        {"txn_id": "TXN-002", "type": "withdrawal", "amount_eur": 50.00,  "currency": "EUR",
         "timestamp": "2023-02-18T09:15:00Z", "method": "bank_transfer", "status": "completed"},
        {"txn_id": "TXN-003", "type": "deposit",    "amount_eur": 200.00, "currency": "EUR",
         "timestamp": "2023-04-05T19:47:00Z", "method": "card", "status": "completed"},
    ]


def _fetch_game_history(player_id: str) -> list[dict[str, Any]]:
    """Return game session history rows."""
    _ = player_id
    return [
        {"session_id": "GS-101", "game": "Book of Dead",  "provider": "Play'n GO",
         "stake_eur": 1.00, "return_eur": 0.00, "duration_s": 1200,
         "started_at": "2023-01-11T20:00:00Z"},
        {"session_id": "GS-102", "game": "Starburst",     "provider": "NetEnt",
         "stake_eur": 0.50, "return_eur": 2.50, "duration_s": 450,
         "started_at": "2023-03-22T22:15:00Z"},
    ]


def _fetch_responsible_gaming(player_id: str) -> dict[str, Any]:
    """Return responsible gaming settings and history."""
    _ = player_id
    return {
        "deposit_limit_daily_eur":   500,
        "deposit_limit_weekly_eur":  2000,
        "session_reminder_minutes":  60,
        "self_exclusion_active":     False,
        "self_exclusion_start":      None,
        "self_exclusion_end":        None,
        "cooling_off_periods":       [],
        "rg_assessment_score":       "low",
        # Art. 20 note: RG data is platform-generated, not "provided by subject"
        # → flagged as not Art. 20 eligible, but included under Art. 15 access right.
        "_art20_eligible":           False,
    }


# ---------------------------------------------------------------------------
# Export builders
# ---------------------------------------------------------------------------

def _build_account_json(fields: list[ExportField]) -> bytes:
    payload = {
        "_regulation": "GDPR Art. 20 / Art. 15",
        "_generated_at": datetime.now(timezone.utc).isoformat(),
        "fields": [
            {
                "name": f.name,
                "value": f.value,
                "art20_eligible": f.art20_eligible,
                "category": f.category,
            }
            for f in fields
        ],
    }
    return json.dumps(payload, indent=2).encode()


def _build_csv(rows: list[dict[str, Any]], fieldnames: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode()


def _build_manifest(
    player_id: str,
    files: list[str],
    account_fields: list[ExportField],
    rg_data: dict[str, Any],
) -> bytes:
    # Count Art. 20 eligible fields across all data categories
    art20_count = sum(1 for f in account_fields if f.art20_eligible)
    manifest = ExportManifest(
        player_id=player_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        regulation="GDPR Art. 20 (portability) + Art. 15 (access)",
        files_included=files,
        total_fields_exported=len(account_fields) + len(rg_data),
        art20_eligible_fields=art20_count,
    )
    return json.dumps(
        {k: v for k, v in manifest.__dict__.items()},
        indent=2,
    ).encode()


def generate_export(player_id: str, output_path: Path) -> Path:
    """
    Generate the portable DSR ZIP export for player_id.

    Returns the path to the created ZIP file.
    """
    logger.info("Generating DSR export for player %s", player_id)

    account_fields = _fetch_account_data(player_id)
    transactions   = _fetch_transactions(player_id)
    game_history   = _fetch_game_history(player_id)
    rg_data        = _fetch_responsible_gaming(player_id)

    zip_path = output_path / f"dsr_export_{player_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.zip"
    files_included: list[str] = []

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Account profile — JSON (Art. 20 eligible fields marked)
        acct_bytes = _build_account_json(account_fields)
        zf.writestr("account_data.json", acct_bytes)
        files_included.append("account_data.json")
        logger.info("  Added account_data.json (%d bytes)", len(acct_bytes))

        # 2. Transaction history — CSV
        if transactions:
            txn_cols = list(transactions[0].keys())
            txn_bytes = _build_csv(transactions, txn_cols)
            zf.writestr("transaction_history.csv", txn_bytes)
            files_included.append("transaction_history.csv")
            logger.info("  Added transaction_history.csv (%d rows)", len(transactions))

        # 3. Game history — CSV
        if game_history:
            game_cols = list(game_history[0].keys())
            game_bytes = _build_csv(game_history, game_cols)
            zf.writestr("game_history.csv", game_bytes)
            files_included.append("game_history.csv")
            logger.info("  Added game_history.csv (%d rows)", len(game_history))

        # 4. Responsible gaming — JSON
        rg_bytes = json.dumps(rg_data, indent=2).encode()
        zf.writestr("responsible_gaming.json", rg_bytes)
        files_included.append("responsible_gaming.json")
        logger.info("  Added responsible_gaming.json")

        # 5. Manifest
        manifest_bytes = _build_manifest(player_id, files_included, account_fields, rg_data)
        zf.writestr("manifest.json", manifest_bytes)
        files_included.append("manifest.json")
        logger.info("  Added manifest.json")

    logger.info("Export ZIP created at %s (%d bytes)", zip_path, zip_path.stat().st_size)
    return zip_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a GDPR Art. 20 portable data export ZIP for a player."
    )
    parser.add_argument("--player-id", required=True, help="Player UUID or identifier")
    parser.add_argument("--output-dir", default=".", help="Directory to write the ZIP file")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = generate_export(args.player_id, out_dir)
    print(result)
