# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Incremental player export to ExactTarget via SFTP.

Python port of PlayersExportTask.scala referenced in chapter 37. The
task reads the last successful export timestamp from a sentinel file,
queries every player modified since that timestamp (minus a safety
overlap to cover replication lag), writes the results to one or more
split CSV files, uploads them to ExactTarget's SFTP inbox, and
updates the sentinel file on success. A `--fullRange` flag bypasses
the incremental window for the rare cases where the platform needs
to force a full reimport.

Email tokenisation for GDPR compliance is supported via a flag that
replaces the local part of each player's email address with their
numeric user id -- ExactTarget can still track subscriber engagement
but no longer holds the real address.
"""

from __future__ import annotations

import csv  # noqa: F401  -- reserved for future inline exports
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

# Sibling module imports -- the directory name has a hyphen so the
# files cannot form a Python package; explicit sys.path insertion is
# the same pattern chapter 26 uses.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SyncConfig  # noqa: E402
from csv_exporter import CsvExporter, cleanup_old_files  # noqa: E402
from sftp_uploader import SftpUploader  # noqa: E402
from task import Alerter, Task  # noqa: E402

LOG = logging.getLogger("exacttarget_sync.players_export")


@dataclass(frozen=True)
class PlayerRecord:
    """Shape of a row returned by the player repository."""

    user_id: int
    email: str
    first_name: str
    last_name: str
    modified_at: datetime


class PlayerRepository(Protocol):
    """Minimal interface for player lookups. Production wraps a
    PostgreSQL cursor; tests inject an in-memory list.
    """

    def find_modified_since(
        self, since: datetime | None
    ) -> list[PlayerRecord]: ...


class PlayersExportTask(Task):
    """Export modified players to CSV and upload to ExactTarget.

    Parameters
    ----------
    config:
        Fully-resolved SyncConfig with at least one brand configured.
    brand_id:
        Which brand to run the export for. A production cron entry
        passes the brand via argv so the same CLI binary handles every
        brand with a single systemd timer.
    repository:
        PlayerRepository implementation.
    uploader:
        SftpUploader bound to the brand's SFTP endpoint.
    now:
        Injectable clock used by tests. Defaults to `datetime.now(tz=utc)`.
    tokenized:
        When True, replaces the local part of each email with the
        user id (`"12345@gmail.com"`). Documented GDPR-compliance
        mode from chapter 37.
    full_range:
        When True, ignores the last-export timestamp and exports the
        full player table. Use for rare force-reimport scenarios.
    """

    alert_priority = "P2"

    def __init__(
        self,
        *,
        config: SyncConfig,
        brand_id: str,
        repository: PlayerRepository,
        uploader: SftpUploader,
        alerter: Alerter | None = None,
        now: "object | None" = None,
        tokenized: bool = False,
        full_range: bool = False,
    ) -> None:
        super().__init__(alerter=alerter)
        self._config = config
        self._brand_id = brand_id
        self._repository = repository
        self._uploader = uploader
        self._tokenized = tokenized
        self._full_range = full_range
        self._now = now or (lambda: datetime.now(tz=timezone.utc))
        self._files_written: list[Path] = []
        self._since: datetime | None = None

    @property
    def name(self) -> str:
        return f"PlayersExportTask({self._brand_id})"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def before(self) -> None:
        """Compute the incremental window and log it."""
        if self._full_range:
            self._since = None
            LOG.info("players export (%s): FULL range", self._brand_id)
            return

        last = self._load_last_export_timestamp()
        if last is None:
            # First run: fall back to a full export.
            self._since = None
            LOG.info("players export (%s): first run -- FULL range", self._brand_id)
        else:
            overlap = timedelta(hours=self._config.export.safety_overlap_hours)
            self._since = last - overlap
            LOG.info(
                "players export (%s): incremental since %s (overlap %s)",
                self._brand_id, self._since.isoformat(), overlap,
            )

    def do_task(self) -> None:
        players = self._repository.find_modified_since(self._since)
        LOG.info(
            "players export (%s): %d players since %s",
            self._brand_id, len(players), self._since,
        )

        exporter = CsvExporter(
            destination_dir=self._config.export.local_export_dir,
            base_name=f"players_{self._brand_id}",
            header=["UserId", "EmailAddress", "FirstName", "LastName", "ModifiedAt"],
            split_line_count=self._config.export.csv_split_line_count,
        )
        result = exporter.export(self._row_stream(players))
        self._files_written = list(result.files_written)
        LOG.info(
            "players export (%s): wrote %d files totaling %d rows",
            self._brand_id, result.file_count, result.total_rows,
        )

        brand = self._config.brand(self._brand_id)
        transfer_results = self._uploader.upload_all(
            [(path, f"{brand.sftp_upload_dir}/{path.name}") for path in self._files_written]
        )
        failures = [r for r in transfer_results if not r.success]
        if failures:
            raise RuntimeError(
                f"{len(failures)} of {len(transfer_results)} uploads failed"
            )

    def after(self) -> None:
        """Update the sentinel timestamp and prune old files."""
        # Persist the successful run time so the next incremental
        # window starts from here.
        now = self._current_now()
        self._save_last_export_timestamp(now)
        deleted = cleanup_old_files(
            self._config.export.local_export_dir,
            retention_days=self._config.export.retention_days,
        )
        if deleted:
            LOG.info(
                "players export (%s): cleaned %d old files", self._brand_id, deleted
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_stream(self, players: list[PlayerRecord]):
        """Transform PlayerRecord list into dict rows for CsvExporter.

        Yields lazily so that very large exports stream through the
        writer without materialising the whole transformed list.
        """
        for p in players:
            email = self._tokenize_email(p.email, p.user_id) if self._tokenized else p.email
            yield {
                "UserId": p.user_id,
                "EmailAddress": email,
                "FirstName": p.first_name,
                "LastName": p.last_name,
                "ModifiedAt": p.modified_at.isoformat(),
            }

    @staticmethod
    def _tokenize_email(email: str, user_id: int) -> str:
        """Replace the local part of the email with the numeric user id.

        The domain is preserved so ExactTarget can still compute
        deliverability analytics per domain. Emails without an `@`
        fall back to a synthetic address so the export row is never
        dropped.
        """
        if "@" in email:
            _, _, domain = email.rpartition("@")
            return f"{user_id}@{domain}"
        return f"{user_id}@tokenized.invalid"

    def _current_now(self) -> datetime:
        factory = self._now
        if callable(factory):
            return factory()
        return datetime.now(tz=timezone.utc)

    def _timestamp_file(self) -> Path:
        return Path(self._config.export.local_export_dir) / f"{self._brand_id}.{self._config.export.last_export_timestamp_file}"

    def _load_last_export_timestamp(self) -> datetime | None:
        path = self._timestamp_file()
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8").strip()
            return datetime.fromisoformat(raw)
        except ValueError:
            LOG.warning(
                "players export (%s): unreadable timestamp file %s -- treating as first run",
                self._brand_id, path,
            )
            return None

    def _save_last_export_timestamp(self, when: datetime) -> None:
        path = self._timestamp_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(when.isoformat(), encoding="utf-8")
