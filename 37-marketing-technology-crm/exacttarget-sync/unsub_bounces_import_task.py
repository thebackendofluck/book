# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Import unsubscribes and hard bounces from ExactTarget.

Python port of UnsubBouncesImportTask.scala referenced in chapter 37.
ExactTarget publishes two daily CSV files on its SFTP server: one
containing unsubscribes for the previous day, one containing hard
bounces. This task downloads both, parses the user ids, and persists
the resulting marketing preference changes through the
MarketingPreferencesDao. The same idempotent insert pattern means
a reprocess after a network blip produces zero duplicates.
"""

from __future__ import annotations

import csv
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SyncConfig  # noqa: E402
from marketing_preferences_dao import MarketingPreferencesDao  # noqa: E402
from sftp_uploader import SftpUploader  # noqa: E402
from task import Alerter, Task  # noqa: E402

LOG = logging.getLogger("exacttarget_sync.unsub_bounces_import")


@dataclass(frozen=True)
class ImportSummary:
    """Counts of rows processed during one task run."""

    brand_id: str
    unsubs_read: int
    bounces_read: int
    unsubs_applied: int
    bounces_applied: int
    parse_errors: int

    @property
    def total_applied(self) -> int:
        return self.unsubs_applied + self.bounces_applied


class UnsubBouncesImportTask(Task):
    """Download unsub/bounce CSVs and persist marketing preference changes."""

    alert_priority = "P2"

    #: Columns the ExactTarget daily files ship with. The first row of
    #: each CSV is a header; we match by column name so ExactTarget
    #: can add new columns without breaking the parser.
    USER_ID_COLUMN_CANDIDATES = ("SubscriberKey", "UserId", "SubscriberID", "User_Id")
    EMAIL_COLUMN_CANDIDATES = ("EmailAddress", "Email", "email")

    def __init__(
        self,
        *,
        config: SyncConfig,
        brand_id: str,
        dao: MarketingPreferencesDao,
        uploader: SftpUploader,
        alerter: Alerter | None = None,
    ) -> None:
        super().__init__(alerter=alerter)
        self._config = config
        self._brand_id = brand_id
        self._dao = dao
        self._uploader = uploader
        self._summary: ImportSummary | None = None

    @property
    def name(self) -> str:
        return f"UnsubBouncesImportTask({self._brand_id})"

    @property
    def summary(self) -> ImportSummary | None:
        return self._summary

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def do_task(self) -> None:
        brand = self._config.brand(self._brand_id)
        download_dir = Path(self._config.export.local_export_dir) / "inbox" / self._brand_id
        download_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
        unsub_remote = f"{brand.sftp_download_dir}/unsubs_{today}.csv"
        bounce_remote = f"{brand.sftp_download_dir}/bounces_{today}.csv"
        unsub_local = download_dir / Path(unsub_remote).name
        bounce_local = download_dir / Path(bounce_remote).name

        # Downloads -- both files must arrive before we touch the DB.
        unsub_result = self._uploader.download(unsub_remote, unsub_local)
        bounce_result = self._uploader.download(bounce_remote, bounce_local)

        if not unsub_result.success or not bounce_result.success:
            raise RuntimeError(
                f"unable to fetch both feed files "
                f"(unsub={unsub_result.success}, bounce={bounce_result.success})"
            )

        unsubs_read, unsubs_applied, unsub_errors = self._apply_feed(
            unsub_local, kind="unsub"
        )
        bounces_read, bounces_applied, bounce_errors = self._apply_feed(
            bounce_local, kind="bounce"
        )

        self._summary = ImportSummary(
            brand_id=self._brand_id,
            unsubs_read=unsubs_read,
            bounces_read=bounces_read,
            unsubs_applied=unsubs_applied,
            bounces_applied=bounces_applied,
            parse_errors=unsub_errors + bounce_errors,
        )
        LOG.info(
            "import (%s): unsubs=%d bounces=%d parse_errors=%d",
            self._brand_id, unsubs_applied, bounces_applied, unsub_errors + bounce_errors,
        )

    # ------------------------------------------------------------------
    # Feed parsing
    # ------------------------------------------------------------------

    def _apply_feed(self, path: Path, *, kind: str) -> tuple[int, int, int]:
        """Parse an ExactTarget CSV and apply each row through the DAO.

        Returns (rows_read, rows_applied, parse_errors).
        """
        if not path.exists():
            LOG.warning("%s feed file not found: %s", kind, path)
            return 0, 0, 0

        rows_read = 0
        applied = 0
        errors = 0
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            user_id_col = self._select_column(reader.fieldnames, self.USER_ID_COLUMN_CANDIDATES)
            if user_id_col is None:
                raise RuntimeError(
                    f"{kind} feed {path.name} has no recognised user id column "
                    f"(expected one of {self.USER_ID_COLUMN_CANDIDATES})"
                )
            for row in reader:
                rows_read += 1
                raw_id = row.get(user_id_col, "").strip()
                if not raw_id:
                    errors += 1
                    continue
                try:
                    user_id = int(raw_id)
                except ValueError:
                    # Some brands ship non-numeric subscriber keys.
                    # Skip rather than crash -- the row will surface in
                    # the next run if the brand normalises the format.
                    errors += 1
                    continue

                if kind == "unsub":
                    self._dao.record_unsubscribe(user_id, source=f"exacttarget:{self._brand_id}")
                else:
                    self._dao.record_hard_bounce(user_id, source=f"exacttarget:{self._brand_id}")
                applied += 1
        return rows_read, applied, errors

    @staticmethod
    def _select_column(
        available: "Sequence[str] | None", candidates: tuple[str, ...]
    ) -> str | None:
        if not available:
            return None
        available_list = list(available)
        norm = {c.lower(): c for c in available_list}
        for candidate in candidates:
            if candidate in available_list:
                return candidate
            lowered = candidate.lower()
            if lowered in norm:
                return norm[lowered]
        return None
