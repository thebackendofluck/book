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
Retention Enforcer — Nightly CronJob
======================================
Scans pseudonymized records whose retention window has elapsed and performs
final deletion. Logs each purge action to the retention_purge_log audit table.

Regulatory context:
  - GDPR Art. 5(1)(e): Storage limitation — data kept no longer than necessary
  - GDPR Art. 17(1): Right to erasure — platform-initiated deletion of expired records
  - AMLD Art. 40: 5-year AML record-keeping post relationship termination
  - EU VAT Directive 2006/112/EC Art. 244: 7-year tax records
  - MGA Licence Conditions 2021: 10-year records for investigations
  - FATF Recommendation 11: Customer due diligence records 5 years
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("retention_enforcer")


class RetentionCategory(str, Enum):
    AML   = "AML_5YR"    # AMLD Art. 40 — 5 years post-relationship
    TAX   = "TAX_7YR"    # EU VAT Directive — 7 years
    MGA   = "MGA_10YR"   # MGA investigations — 10 years
    RG    = "RG_5YR"     # Responsible gaming records — 5 years
    FRAUD = "FRAUD_1YR"  # Fraud prevention — 1 year (GDPR Art. 6(1)(f))
    GDPR  = "GDPR_3YR"   # General data — 3 years post-account-close


class PurgeAction(str, Enum):
    DELETED        = "DELETED"
    ALREADY_PURGED = "ALREADY_PURGED"
    SKIPPED_HOLD   = "SKIPPED_HOLD"
    ERROR          = "ERROR"


@dataclass
class RetentionRecord:
    record_id: str
    player_id: str
    table_name: str
    category: RetentionCategory
    relationship_ended: date    # account closure or last transaction date
    aml_hold_until: Optional[date]
    pseudonymized_at: Optional[date]


@dataclass
class PurgeLogEntry:
    record_id: str
    player_id: str
    table_name: str
    category: str
    action: PurgeAction
    actioned_at: str
    retention_window_end: str
    reason: str


@dataclass
class EnforcerRunSummary:
    run_at: str
    records_evaluated: int
    deleted: int
    skipped_hold: int
    already_purged: int
    errors: int
    purge_log: list[PurgeLogEntry]


# Retention window definitions in years
RETENTION_WINDOWS: dict[RetentionCategory, int] = {
    RetentionCategory.AML:   5,
    RetentionCategory.TAX:   7,
    RetentionCategory.MGA:   10,
    RetentionCategory.RG:    5,
    RetentionCategory.FRAUD: 1,
    RetentionCategory.GDPR:  3,
}


def _window_end(record: RetentionRecord) -> date:
    """Calculate the date after which the record may be finally deleted."""
    years = RETENTION_WINDOWS[record.category]
    base = record.relationship_ended
    # Account for AML hold extensions (AMLD Art. 40 — hold suspends the clock)
    if record.aml_hold_until and record.aml_hold_until > base:
        base = record.aml_hold_until
    return date(base.year + years, base.month, base.day)


# ---------------------------------------------------------------------------
# Stub data source — production queries the pseudonymized_records table.
# ---------------------------------------------------------------------------
def _fetch_pseudonymized_records() -> list[RetentionRecord]:
    """Return records that have been pseudonymized and are awaiting final deletion."""
    today = date.today()
    return [
        RetentionRecord("rec-001", "player-111", "players",           RetentionCategory.GDPR,
                        date(today.year - 4, 1, 15), None,              date(today.year - 1, 1, 16)),
        RetentionRecord("rec-002", "player-222", "payment_methods",   RetentionCategory.TAX,
                        date(today.year - 8, 6, 1),  None,              date(today.year - 2, 6, 2)),
        RetentionRecord("rec-003", "player-333", "kyc_documents",     RetentionCategory.AML,
                        date(today.year - 6, 3, 20), None,              date(today.year - 1, 3, 21)),
        RetentionRecord("rec-004", "player-444", "aml_flags",         RetentionCategory.MGA,
                        date(today.year - 2, 9, 10), today + timedelta(days=365), None),  # AML hold active
        RetentionRecord("rec-005", "player-555", "responsible_gaming",RetentionCategory.RG,
                        date(today.year - 5, 11, 30), None,             date(today.year - 1, 12, 1)),
        RetentionRecord("rec-006", "player-666", "game_sessions",     RetentionCategory.FRAUD,
                        date(today.year - 1, 2, 5),  None,              None),  # Not yet pseudonymized
    ]


def _hard_delete(record: RetentionRecord) -> bool:
    """
    Execute final deletion of the record.

    Production implementation:
      DELETE FROM {table} WHERE pseudonymized_token = :token AND record_id = :id
    Also destroys the Vault DEK via crypto_shredder (see crypto_shredder.py).
    Returns True on success.
    """
    logger.info("  [DELETE] %s / %s — %s", record.table_name, record.record_id, record.category.value)
    # Stub: always succeeds
    return True


def _write_purge_log(entries: list[PurgeLogEntry]) -> None:
    """Persist purge log entries to the audit table."""
    # Production: INSERT INTO retention_purge_log (...)
    logger.info("Purge log: %d entries written to retention_purge_log", len(entries))


def run_enforcer(dry_run: bool = False) -> EnforcerRunSummary:
    """
    Main enforcer logic.

    Iterates all pseudonymized records, checks whether the retention window has
    elapsed, and either deletes or skips each record.
    """
    today = date.today()
    logger.info("Retention enforcer started (dry_run=%s, date=%s)", dry_run, today)

    records = _fetch_pseudonymized_records()
    summary = EnforcerRunSummary(
        run_at=datetime.now(timezone.utc).isoformat(),
        records_evaluated=len(records),
        deleted=0,
        skipped_hold=0,
        already_purged=0,
        errors=0,
        purge_log=[],
    )

    for rec in records:
        window_end = _window_end(rec)

        # Skip: AML hold still active — AMLD Art. 40 trumps GDPR Art. 17
        if rec.aml_hold_until and rec.aml_hold_until > today:
            logger.warning("SKIP (AML hold) %s — hold until %s", rec.record_id, rec.aml_hold_until)
            summary.skipped_hold += 1
            summary.purge_log.append(PurgeLogEntry(
                record_id=rec.record_id,
                player_id=rec.player_id,
                table_name=rec.table_name,
                category=rec.category.value,
                action=PurgeAction.SKIPPED_HOLD,
                actioned_at=datetime.now(timezone.utc).isoformat(),
                retention_window_end=window_end.isoformat(),
                reason=f"AML hold active until {rec.aml_hold_until}",
            ))
            continue

        # Skip: not yet pseudonymized
        if rec.pseudonymized_at is None:
            logger.info("SKIP (not pseudonymized) %s", rec.record_id)
            summary.already_purged += 1
            continue

        # Skip: retention window not elapsed
        if today < window_end:
            logger.debug("DEFER %s — window ends %s", rec.record_id, window_end)
            continue

        # Delete
        if not dry_run:
            try:
                success = _hard_delete(rec)
                action = PurgeAction.DELETED if success else PurgeAction.ERROR
                if success:
                    summary.deleted += 1
                else:
                    summary.errors += 1
            except Exception as exc:
                logger.error("Error deleting %s: %s", rec.record_id, exc)
                action = PurgeAction.ERROR
                summary.errors += 1
        else:
            logger.info("  [DRY-RUN] Would delete %s / %s", rec.table_name, rec.record_id)
            action = PurgeAction.DELETED
            summary.deleted += 1

        summary.purge_log.append(PurgeLogEntry(
            record_id=rec.record_id,
            player_id=rec.player_id,
            table_name=rec.table_name,
            category=rec.category.value,
            action=action,
            actioned_at=datetime.now(timezone.utc).isoformat(),
            retention_window_end=window_end.isoformat(),
            reason=f"Retention window elapsed. Category={rec.category.value}",
        ))

    _write_purge_log(summary.purge_log)
    logger.info(
        "Enforcer complete — evaluated=%d deleted=%d skipped_hold=%d errors=%d",
        summary.records_evaluated, summary.deleted, summary.skipped_hold, summary.errors,
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the nightly retention enforcement job.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without performing deletions")
    parser.add_argument("--output", default="-", help="Write summary JSON to file (default: stdout)")
    args = parser.parse_args()

    result = run_enforcer(dry_run=args.dry_run)
    payload = json.dumps(asdict(result), indent=2, default=str)

    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        with open(args.output, "w") as fh:
            fh.write(payload + "\n")
        logger.info("Summary written to %s", args.output)
