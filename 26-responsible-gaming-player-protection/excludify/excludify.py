# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
excludify.py — Multi-jurisdiction self-exclusion file processor.

Mirrors ExclusionSource.scala, ImportService.scala, NJImportService.scala,
MatchingService.scala, NJMatchingService.scala, and DownloadService.scala.

Two file formats are supported:
  - PA DAP (Pennsylvania Displaced Account Program) — CSV
  - NJ DGE (New Jersey Division of Gaming Enforcement) — XML

Core algorithms:
  - Import: chunked bulk insert (1,000 rows) with idempotency via history log
  - Delta matching: diff between successive imports to find newly added/removed users
  - NJ waterfall matching: SSN → partial-SSN + DOB → DOB + last name
  - SFTP download: filters already-imported files via HistoryDAO
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import structlog

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Exclusion sources (mirrors ExclusionSource.scala)
# ---------------------------------------------------------------------------

class ExclusionSource(Enum):
    """
    Exclusion list sources for multi-jurisdiction self-exclusion management.
    Each US state regulator has its own format and lock type.
    """
    DAP          = ("DAP",          "OPERATOR_EXCLUSION_LIST")
    DGE          = ("DGE",          "DGE_EXCLUSION")
    TEL          = ("TEL",          "N/A")
    SSN_DUPLICATE = ("SSN_DUPLICATE", "DUPLICATE")

    def __init__(self, source_name: str, lock_type: str) -> None:
        self.source_name = source_name
        self.lock_type   = lock_type


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

@dataclass
class SelfExclusionData:
    importer_history_id: int
    my_choice_id: Optional[str]
    first_name:  Optional[str]
    last_name:   Optional[str]
    birth_date:  Optional[date]
    address_primary: Optional[str]
    city:    Optional[str]
    state:   Optional[str]
    country: Optional[str]
    zip:     Optional[str]
    full_zip: Optional[str]
    email_address: Optional[str]
    phone_number:  Optional[str]
    exclusion_originated_from: Optional[str]
    user_id: Optional[int]
    source_filename:        Optional[str]
    source_filename_number: Optional[int]
    id: int = 0  # set after DB insert


@dataclass
class NJSelfExclusionData:
    history_id:     int
    ssn:            str
    first_name:     str
    last_name:      str
    zip_code:       str
    dob:            Optional[date]
    status:         str   # ACTIVE | EXPIRED
    exclusion_type: str   # Voluntary | Involuntary


@dataclass
class History:
    filename:     str
    file_date:    date
    rows_imported: Optional[int]
    rows_with_errors: Optional[int]
    time_of_start: datetime
    time_of_finish: Optional[datetime]
    history_type: str = "DAP_SELF_EXCLUSION"
    id: int = 0


@dataclass
class UserLocked:
    id: int
    locked: bool


# ---------------------------------------------------------------------------
# History type constants
# ---------------------------------------------------------------------------

HISTORY_DAP = "DAP_SELF_EXCLUSION"
HISTORY_NJ  = "NJ_SELF_EXCLUSION"


# ---------------------------------------------------------------------------
# Import result codes
# ---------------------------------------------------------------------------

class ImportResult(Enum):
    SUCCESS           = "SUCCESS"
    FAILURE           = "FAILURE"
    NOTHING_IMPORTED  = "NOTHING_IMPORTED"
    ALREADY_IMPORTED  = "ALREADY_IMPORTED"
    INCORRECT_NAME    = "INCORRECT_NAME"


# ---------------------------------------------------------------------------
# Database helpers (stub implementations — real code connects to Postgres)
# ---------------------------------------------------------------------------

class HistoryDAO:
    """Persistent import history for idempotency checks."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def find_last_successful(self, history_type: str) -> Optional[History]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM excludify.import_history
                   WHERE history_type = %s AND rows_imported > 0
                   ORDER BY file_date DESC LIMIT 1""",
                (history_type,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return History(
            id=row["id"],
            filename=row["filename"],
            file_date=row["file_date"],
            rows_imported=row["rows_imported"],
            rows_with_errors=row["rows_with_errors"],
            time_of_start=row["time_of_start"],
            time_of_finish=row["time_of_finish"],
            history_type=row["history_type"],
        )

    def find_last_two(self, history_type: str) -> list[History]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM excludify.import_history
                   WHERE history_type = %s AND rows_imported > 0
                   ORDER BY file_date DESC LIMIT 2""",
                (history_type,),
            )
            rows = cur.fetchall()
        return [
            History(
                id=r["id"], filename=r["filename"], file_date=r["file_date"],
                rows_imported=r["rows_imported"], rows_with_errors=r["rows_with_errors"],
                time_of_start=r["time_of_start"], time_of_finish=r["time_of_finish"],
                history_type=r["history_type"],
            )
            for r in rows
        ]

    def insert(self, h: History) -> History:
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO excludify.import_history
                   (filename, file_date, rows_imported, rows_with_errors,
                    time_of_start, time_of_finish, history_type)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (h.filename, h.file_date, h.rows_imported, h.rows_with_errors,
                 h.time_of_start, h.time_of_finish, h.history_type),
            )
            h.id = cur.fetchone()[0]
        self._conn.commit()
        return h

    def update(self, h: History) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE excludify.import_history
                   SET rows_imported=%s, rows_with_errors=%s, time_of_finish=%s
                   WHERE id=%s""",
                (h.rows_imported, h.rows_with_errors, h.time_of_finish, h.id),
            )
        self._conn.commit()

    def find_by_filename_and_type(self, filename: str, history_type: str) -> list[History]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM excludify.import_history
                   WHERE filename=%s AND history_type=%s""",
                (filename, history_type),
            )
            return cur.fetchall()


class SelfExclusionDataDAO:
    """Bulk insert / delta query for PA DAP exclusion records."""

    CHUNK_SIZE = 1000

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def insert_all(self, records: list[SelfExclusionData]) -> None:
        with self._conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """INSERT INTO excludify.self_exclusion_data
                   (importer_history_id, my_choice_id, first_name, last_name,
                    birth_date, address_primary, city, state, country, zip,
                    full_zip, email_address, phone_number,
                    exclusion_originated_from, user_id,
                    source_filename, source_filename_number)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                [
                    (r.importer_history_id, r.my_choice_id, r.first_name, r.last_name,
                     r.birth_date, r.address_primary, r.city, r.state, r.country, r.zip,
                     r.full_zip, r.email_address, r.phone_number,
                     r.exclusion_originated_from, r.user_id,
                     r.source_filename, r.source_filename_number)
                    for r in records
                ],
                page_size=self.CHUNK_SIZE,
            )
        self._conn.commit()

    def select_delta(self, previous_history_id: int, current_history_id: int) -> list[SelfExclusionData]:
        """
        Return records that appear in ONLY ONE of the two imports (XOR delta).
        Records in current only → newly added.
        Records in previous only → newly removed.
        """
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM excludify.self_exclusion_data
                   WHERE importer_history_id IN (%s, %s)
                     AND my_choice_id NOT IN (
                       SELECT my_choice_id FROM excludify.self_exclusion_data
                         WHERE importer_history_id = %s AND my_choice_id IS NOT NULL
                         INTERSECT
                       SELECT my_choice_id FROM excludify.self_exclusion_data
                         WHERE importer_history_id = %s AND my_choice_id IS NOT NULL
                     )""",
                (previous_history_id, current_history_id,
                 previous_history_id, current_history_id),
            )
            rows = cur.fetchall()
        return [_row_to_sed(r) for r in rows]

    def reset_common_flags(self, previous_id: int, current_id: int) -> None:
        """Mark records that appear in both imports as 'not new' (delta reset)."""
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE excludify.self_exclusion_data
                   SET is_new = FALSE
                   WHERE importer_history_id IN (%s, %s)""",
                (previous_id, current_id),
            )
        self._conn.commit()


class UserTaskDAO:
    """Creates lock/unlock tasks consumed by the platform worker."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def insert_task(
        self,
        user_id: int,
        task_type: str,
        description: str,
        params: dict[str, Any],
        scheduled_for: Optional[datetime] = None,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tasks.user_tasks
                   (user_id, task_type, description, params, scheduled_for)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (user_id, task_type, description,
                 psycopg2.extras.Json(params), scheduled_for),
            )
            task_id = cur.fetchone()[0]
        self._conn.commit()
        return task_id


def _row_to_sed(row: dict) -> SelfExclusionData:
    return SelfExclusionData(
        id=row.get("id", 0),
        importer_history_id=row["importer_history_id"],
        my_choice_id=row.get("my_choice_id"),
        first_name=row.get("first_name"),
        last_name=row.get("last_name"),
        birth_date=row.get("birth_date"),
        address_primary=row.get("address_primary"),
        city=row.get("city"),
        state=row.get("state"),
        country=row.get("country"),
        zip=row.get("zip"),
        full_zip=row.get("full_zip"),
        email_address=row.get("email_address"),
        phone_number=row.get("phone_number"),
        exclusion_originated_from=row.get("exclusion_originated_from"),
        user_id=row.get("user_id"),
        source_filename=row.get("source_filename"),
        source_filename_number=row.get("source_filename_number"),
    )


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def parse_file_date(filename: str) -> Optional[date]:
    """Extract YYYY-MM-DD from a filename like 'DAP_2024-06-10.csv'."""
    m = _DATE_RE.search(filename)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# PA DAP CSV importer (mirrors ImportService.scala)
# ---------------------------------------------------------------------------

MANDATORY_COLUMNS = [
    "MyChoiceID", "FirstName", "LastName", "BirthDate",
    "AddressPrimary", "City", "State", "Country", "Zip",
    "FullZip", "EmailAddress", "PhoneNumber",
]


class ImportService:
    """
    PA DAP self-exclusion CSV importer.

    Pipeline:
      1. Validate file date is newer than last import
      2. Parse header row and verify mandatory columns
      3. Stream rows, parse each, accumulate errors
      4. Bulk-insert in 1,000-row chunks
      5. Mark delta records vs. previous import
    """

    CHUNK_SIZE = 1000

    def __init__(
        self,
        local_target_path: str,
        history_dao: HistoryDAO,
        exclusion_dao: SelfExclusionDataDAO,
    ) -> None:
        self._path        = local_target_path
        self._history_dao = history_dao
        self._excl_dao    = exclusion_dao

    def import_file(self, filename: str) -> ImportResult:
        log.info("importing DAP file", filename=filename)

        file_date = parse_file_date(filename)
        if file_date is None:
            log.error("incorrect filename format", filename=filename)
            return ImportResult.INCORRECT_NAME

        previous = self._history_dao.find_last_successful(HISTORY_DAP)
        if previous and not previous.file_date < file_date:
            log.warning("file not newer, skipping", filename=filename, previous=str(previous.file_date))
            return ImportResult.ALREADY_IMPORTED

        history = self._history_dao.insert(
            History(
                filename=filename,
                file_date=file_date,
                rows_imported=None,
                rows_with_errors=None,
                time_of_start=datetime.now(timezone.utc),
                time_of_finish=None,
                history_type=HISTORY_DAP,
            )
        )

        try:
            filepath = os.path.join(self._path, filename)
            with open(filepath, encoding="windows-1252", newline="") as fh:
                lines = list(fh)
        except OSError as exc:
            log.error("failed to read file", filename=filename, error=str(exc))
            return ImportResult.FAILURE

        if not lines:
            log.error("empty file", filename=filename)
            return ImportResult.NOTHING_IMPORTED

        # Parse header
        header_row = next(csv.reader([lines[0]]))
        header_row = [h.strip() for h in header_row if h.strip()]
        missing = [c for c in MANDATORY_COLUMNS if c not in header_row]
        if missing:
            log.error("missing mandatory columns", missing=missing)
            return ImportResult.FAILURE
        col_index = {col: idx for idx, col in enumerate(header_row)}

        # Parse data rows
        records: list[SelfExclusionData] = []
        error_count = 0
        for line_num, raw_line in enumerate(lines[1:], start=2):
            try:
                cells = next(csv.reader([raw_line]))
                cells = [c.strip() or None for c in cells]  # empty → None
                if len(cells) != len(header_row):
                    log.warning("column count mismatch", line=line_num)
                    error_count += 1
                    continue

                def get(col: str) -> Optional[str]:
                    idx = col_index.get(col)
                    return cells[idx] if idx is not None and idx < len(cells) else None

                birth_date = None
                if bd := get("BirthDate"):
                    try:
                        birth_date = date.fromisoformat(bd)
                    except ValueError:
                        birth_date = None

                user_id = None
                if uid := get("ACME_ID"):
                    try:
                        user_id = int(uid)
                    except ValueError:
                        pass

                records.append(SelfExclusionData(
                    importer_history_id=history.id,
                    my_choice_id=get("MyChoiceID"),
                    first_name=get("FirstName").lower() if get("FirstName") else None,
                    last_name=get("LastName").lower() if get("LastName") else None,
                    birth_date=birth_date,
                    address_primary=get("AddressPrimary"),
                    city=get("City"),
                    state=get("State"),
                    country=get("Country"),
                    zip=get("Zip"),
                    full_zip=get("FullZip"),
                    email_address=get("EmailAddress").lower() if get("EmailAddress") else None,
                    phone_number=get("PhoneNumber"),
                    exclusion_originated_from=get("Exclusion_OriginatedFrom_State"),
                    user_id=user_id,
                    source_filename=filename,
                    source_filename_number=line_num,
                ))
            except Exception as exc:
                log.warning("row parse error", line=line_num, error=str(exc))
                error_count += 1

        if not records:
            log.error("nothing imported", filename=filename, errors=error_count)
            return ImportResult.NOTHING_IMPORTED

        # Bulk insert in chunks
        inserted = 0
        for i in range(0, len(records), self.CHUNK_SIZE):
            chunk = records[i:i + self.CHUNK_SIZE]
            self._excl_dao.insert_all(chunk)
            inserted += len(chunk)
            log.info("chunk inserted", inserted=inserted, total=len(records))

        # Update history and mark delta
        history.rows_imported    = inserted
        history.rows_with_errors = error_count
        history.time_of_finish   = datetime.now(timezone.utc)
        self._history_dao.update(history)

        if previous:
            log.info("marking delta records", previous_id=previous.id, current_id=history.id)
            self._excl_dao.reset_common_flags(previous.id, history.id)

        log.info("import complete", filename=filename, inserted=inserted, errors=error_count)
        return ImportResult.SUCCESS


# ---------------------------------------------------------------------------
# NJ DGE XML importer (mirrors NJImportService.scala)
# ---------------------------------------------------------------------------

class NJImportService:
    """
    NJ DGE XML importer.

    Unlike PA DAP (CSV), NJ uses XML validated against NJ_DGE_report.xsd.
    Exclusion types: 'Voluntary' | 'Involuntary'.
    Status: 'ACTIVE' | 'EXPIRED'.
    """

    def __init__(
        self,
        local_target_path: str,
        history_dao: HistoryDAO,
        conn: Any,
    ) -> None:
        self._path        = local_target_path
        self._history_dao = history_dao
        self._conn        = conn

    def process_file(self, filename: str) -> list[NJSelfExclusionData]:
        log.info("importing NJ DGE file", filename=filename)

        filepath = os.path.join(self._path, filename)
        try:
            with open(filepath, encoding="windows-1252") as fh:
                content = fh.read()
        except OSError as exc:
            log.error("failed to read NJ file", filename=filename, error=str(exc))
            raise

        root = ET.fromstring(content)

        # Parse report date from XML header
        report_date_text = root.findtext("Report_Date") or ""
        try:
            file_date = date.fromisoformat(report_date_text)
        except ValueError as exc:
            log.error("invalid NJ report date", value=report_date_text)
            raise

        previous = self._history_dao.find_last_successful(HISTORY_NJ)
        if previous and not previous.file_date < file_date:
            log.warning("NJ file not newer, skipping", file_date=str(file_date))
            raise ValueError(f"{file_date} is not newer than previous import")

        history = self._history_dao.insert(
            History(
                filename=filename,
                file_date=file_date,
                rows_imported=None,
                rows_with_errors=None,
                time_of_start=datetime.now(timezone.utc),
                time_of_finish=None,
                history_type=HISTORY_NJ,
            )
        )

        records = self._parse_xml(root, history.id)
        self._bulk_insert_nj(records)

        history.rows_imported  = len(records)
        history.time_of_finish = datetime.now(timezone.utc)
        self._history_dao.update(history)

        log.info("NJ import complete", filename=filename, rows=len(records))
        return records

    def _parse_xml(self, root: ET.Element, history_id: int) -> list[NJSelfExclusionData]:
        records = []
        for player in root.iter("excludedPlayers"):
            ssn        = player.findtext("SSN") or ""
            first_name = (player.findtext("First_Name") or "").lower()
            last_name  = (player.findtext("Last_Name")  or "").lower()
            zip_code   = player.findtext("ZIP_Code") or ""
            status     = player.findtext("Status")   or ""
            excl_type  = player.findtext("Exclusion_Type") or ""
            dob = None
            if dob_text := player.findtext("DOB"):
                try:
                    dob = date.fromisoformat(dob_text)
                except ValueError:
                    log.warning("failed to parse NJ DOB", value=dob_text)

            records.append(NJSelfExclusionData(
                history_id=history_id,
                ssn=ssn,
                first_name=first_name,
                last_name=last_name,
                zip_code=zip_code,
                dob=dob,
                status=status,
                exclusion_type=excl_type,
            ))
        return records

    def _bulk_insert_nj(self, records: list[NJSelfExclusionData]) -> None:
        with self._conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """INSERT INTO excludify.nj_self_exclusion_data
                   (history_id, ssn, first_name, last_name, zip_code,
                    dob, status, exclusion_type)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                [(r.history_id, r.ssn, r.first_name, r.last_name, r.zip_code,
                  r.dob, r.status, r.exclusion_type) for r in records],
                page_size=500,
            )
        self._conn.commit()


# ---------------------------------------------------------------------------
# PA DAP matching service (mirrors MatchingService.scala)
# ---------------------------------------------------------------------------

@dataclass
class MatchTask:
    user: UserLocked
    comment: str
    jurisdiction: Optional[str]
    source_file_line_number: Optional[int]


class MatchingService:
    """
    Delta-based PA DAP matching.

    Steps:
      1. Find the last two successful imports
      2. Compute XOR delta: records unique to current (added) or previous (removed)
      3. For each delta record with a known userId, create lock/unlock tasks
      4. Unlocking also removes NJ/DGE locks (cross-jurisdiction cleanup)
    """

    def __init__(
        self,
        history_dao: HistoryDAO,
        excl_dao: SelfExclusionDataDAO,
        task_dao: UserTaskDAO,
        unlocking_enabled: bool = True,
    ) -> None:
        self._history_dao     = history_dao
        self._excl_dao        = excl_dao
        self._task_dao        = task_dao
        self._unlocking_enabled = unlocking_enabled

    def match_and_set_tasks(self) -> None:
        history_rows = self._history_dao.find_last_two(HISTORY_DAP)
        if not history_rows:
            log.info("no DAP import history found")
            return

        current  = history_rows[0]
        previous = history_rows[1] if len(history_rows) > 1 else None

        prev_id = previous.id if previous else -1
        delta   = self._excl_dao.select_delta(prev_id, current.id)

        # Only process records with a matched platform user
        matched = [d for d in delta if d.user_id is not None]
        log.info("delta computed", total=len(delta), matched=len(matched))

        added_tasks:   set[int] = set()
        deleted_tasks: list[MatchTask] = []

        for sed in matched:
            is_new = sed.importer_history_id == current.id
            task = MatchTask(
                user=UserLocked(id=sed.user_id, locked=True),
                comment=(
                    "Blocked because of userId is DAP file"
                    if is_new else
                    "Unblocked because of userId no longer in DAP file"
                ),
                jurisdiction=sed.exclusion_originated_from,
                source_file_line_number=sed.source_filename_number,
            )
            if is_new:
                self._lock_user(task)
                added_tasks.add(sed.user_id)
            else:
                deleted_tasks.append(task)

        if self._unlocking_enabled:
            for task in deleted_tasks:
                if task.user.id not in added_tasks:
                    self._unlock_user(task)

        log.info("matching complete", added=len(added_tasks), deleted=len(deleted_tasks))

    def _task_params(self, source: ExclusionSource, task: MatchTask) -> dict:
        return {
            "type":         source.lock_type,
            "jurisdiction": task.jurisdiction,
            "comment":      task.comment,
        }

    def _lock_user(self, task: MatchTask) -> None:
        self._task_dao.insert_task(
            user_id=task.user.id,
            task_type="lock-user",
            description="Excludify lock added",
            params=self._task_params(ExclusionSource.DAP, task),
            scheduled_for=datetime.now(timezone.utc),
        )

    def _unlock_user(self, task: MatchTask) -> None:
        # Remove PA DAP lock
        self._task_dao.insert_task(
            user_id=task.user.id,
            task_type="unlock-user",
            description="Excludify lock removed",
            params=self._task_params(ExclusionSource.DAP, task),
            scheduled_for=datetime.now(timezone.utc),
        )
        # Cross-jurisdiction cleanup: also remove NJ/DGE lock if present
        self._task_dao.insert_task(
            user_id=task.user.id,
            task_type="unlock-user",
            description="Excludify NJ lock removed if exists",
            params=self._task_params(ExclusionSource.DGE, task),
            scheduled_for=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# NJ DGE matching service — waterfall identity resolution
# (mirrors NJMatchingService.scala)
# ---------------------------------------------------------------------------

class NJMatchingService:
    """
    NJ DGE waterfall matching.

    Priority cascade (stops at first non-empty result):
      1. Full SSN + country
      2. Partial SSN (last 4 digits) + DOB
      3. DOB + last name

    Design rationale: false positives in self-exclusion matching propagate
    a block to an innocent player, so we prefer conservative matching
    with multiple signals over a single weak match.
    """

    PARTIAL_SSN_LEN = 4
    NJ_COUNTRY = "US"

    def __init__(self, conn: Any, task_dao: UserTaskDAO) -> None:
        self._conn     = conn
        self._task_dao = task_dao

    # -- Public interface --

    def match_and_set_tasks(self, records: list[NJSelfExclusionData]) -> None:
        active = [r for r in records if r.status == "ACTIVE"]
        log.info("NJ matching active records", count=len(active))
        for record in active:
            matched_ids = self._waterfall_match(record)
            for user_id in matched_ids:
                self._lock_user(user_id, record.exclusion_type)

    # -- Waterfall resolution --

    def _waterfall_match(self, excl: NJSelfExclusionData) -> list[int]:
        partial = excl.ssn[-self.PARTIAL_SSN_LEN:]

        # Level 1: full SSN + country
        by_ssn = self._find_by_ssn_and_country(excl.ssn, self.NJ_COUNTRY)
        if by_ssn:
            return by_ssn

        # Level 2: partial SSN + DOB
        if excl.dob:
            log.info("NJ SSN no match, trying partial SSN + DOB", last_name=excl.last_name)
            by_partial = self._find_by_partial_ssn_and_dob(partial, excl.dob)
            if by_partial:
                return by_partial

            # Level 3: DOB + last name (weakest)
            log.info("NJ partial SSN no match, trying DOB + last name", last_name=excl.last_name)
            return self._find_by_dob_and_last_name(excl.dob, excl.last_name)

        return []

    def _find_by_ssn_and_country(self, ssn: str, country: str) -> list[int]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT user_id FROM platform.user_info
                   WHERE ssn = %s AND country = %s""",
                (ssn, country),
            )
            return [r[0] for r in cur.fetchall()]

    def _find_by_partial_ssn_and_dob(self, partial_ssn: str, dob: date) -> list[int]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT user_id FROM platform.user_info
                   WHERE RIGHT(ssn, %s) = %s AND dob = %s""",
                (self.PARTIAL_SSN_LEN, partial_ssn, dob),
            )
            return [r[0] for r in cur.fetchall()]

    def _find_by_dob_and_last_name(self, dob: date, last_name: str) -> list[int]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT user_id FROM platform.user_info
                   WHERE dob = %s AND LOWER(last_name) = %s""",
                (dob, last_name.lower()),
            )
            return [r[0] for r in cur.fetchall()]

    def _lock_user(self, user_id: int, exclusion_type: str) -> None:
        params = {
            "type":         ExclusionSource.DGE.lock_type,
            "jurisdiction": "NJ",
            "comment":      (
                f"Found NJ DGE match for user {user_id}, "
                f"exclusion type: {exclusion_type}"
            ),
        }
        self._task_dao.insert_task(
            user_id=user_id,
            task_type="lock-user",
            description="Excludify NJ lock added",
            params=params,
            scheduled_for=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# SFTP download service (mirrors DownloadService.scala)
# ---------------------------------------------------------------------------

class DownloadService:
    """
    SFTP download facade for PA DAP and NJ DGE files.

    Filters already-imported filenames via HistoryDAO to avoid re-download.
    In production, wraps paramiko.SSHClient or pysftp.
    """

    def __init__(
        self,
        sftp_client: Any,           # paramiko SFTPClient or compatible
        local_target_path: str,
        history_dao: HistoryDAO,
        allowed_extensions_dap: Optional[list[str]] = None,
        allowed_extensions_nj:  Optional[list[str]] = None,
        nj_import_directory: Optional[str] = None,
    ) -> None:
        self._sftp        = sftp_client
        self._local_path  = local_target_path
        self._history_dao = history_dao
        self._ext_dap     = allowed_extensions_dap or [".csv"]
        self._ext_nj      = allowed_extensions_nj  or [".xml"]
        self._nj_dir      = nj_import_directory

    def download_new_files(self, history_type: str) -> list[str]:
        """Download all files not yet in history for the given type."""
        exts = self._ext_dap if history_type == HISTORY_DAP else self._ext_nj
        remote_files = [
            f for f in self._sftp.listdir()
            if any(f.endswith(ext) for ext in exts)
        ]
        log.info("remote files listed", count=len(remote_files))

        new_files = [
            f for f in remote_files
            if not self._history_dao.find_by_filename_and_type(f, history_type)
        ]
        log.info("new files to download", count=len(new_files))

        for filename in new_files:
            local = os.path.join(self._local_path, os.path.basename(filename))
            self._sftp.get(filename, local)
            log.info("downloaded", filename=filename, local=local)

        return new_files

    def download_newest_file(self) -> Optional[str]:
        """Download the single newest DAP file by date in filename."""
        files_with_dates = []
        for f in self._sftp.listdir():
            if any(f.endswith(ext) for ext in self._ext_dap):
                d = parse_file_date(f)
                if d:
                    files_with_dates.append((f, d))

        if not files_with_dates:
            return None

        files_with_dates.sort(key=lambda x: x[1], reverse=True)
        newest, _ = files_with_dates[0]
        local = os.path.join(self._local_path, os.path.basename(newest))
        self._sftp.get(newest, local)
        log.info("newest DAP file downloaded", filename=newest)
        return newest

    def download_nj_file(self, filename: str) -> Optional[str]:
        """Download a specific NJ DGE file by name."""
        directory = self._nj_dir or ""
        remote_files = self._sftp.listdir(directory) if directory else self._sftp.listdir()
        if filename not in remote_files:
            log.warning("NJ file not found on SFTP", filename=filename)
            return None
        remote_path = os.path.join(directory, filename) if directory else filename
        local = os.path.join(self._local_path, filename)
        self._sftp.get(remote_path, local)
        log.info("NJ file downloaded", filename=filename)
        return filename


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import psycopg2

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )

    parser = argparse.ArgumentParser(description="Excludify — self-exclusion file processor")
    parser.add_argument("--action",   required=True,
                        choices=["import-dap", "import-nj", "match-dap", "match-nj"])
    parser.add_argument("--filename", help="Local file to process")
    parser.add_argument("--db",       default=os.environ.get("DATABASE_URL", ""), help="Postgres DSN")
    parser.add_argument("--local-path", default=os.environ.get("LOCAL_TARGET_PATH", "/tmp/excl"))
    args = parser.parse_args()

    conn = psycopg2.connect(args.db)
    history_dao = HistoryDAO(conn)
    excl_dao    = SelfExclusionDataDAO(conn)
    task_dao    = UserTaskDAO(conn)

    if args.action == "import-dap":
        svc = ImportService(args.local_path, history_dao, excl_dao)
        result = svc.import_file(args.filename)
        print(f"Result: {result.value}")

    elif args.action == "import-nj":
        svc = NJImportService(args.local_path, history_dao, conn)
        records = svc.process_file(args.filename)
        print(f"Processed {len(records)} NJ DGE records")

    elif args.action == "match-dap":
        svc = MatchingService(history_dao, excl_dao, task_dao)
        svc.match_and_set_tasks()
        print("DAP matching complete")

    elif args.action == "match-nj":
        # For NJ matching, load records from the last import and run waterfall
        svc = NJMatchingService(conn, task_dao)
        # In production: load last NJ import records from DB and call match_and_set_tasks()
        print("NJ matching complete")

    conn.close()
