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
national_exclusion.py — National self-exclusion registry checker.

Mirrors GamstopProcessor.scala, SpelpausProcessor.scala,
NationalExclusionProcessor.scala, ExcludedUsersHandler.scala,
GamstopUserDAO.scala, SpelpausUserDAO.scala, NationalExclusionDAO.scala,
and Run.scala.

Two registries:
  - GAMSTOP (UK, UKGC): Sends PII (name/DOB/postcode) directly.
    Response: per-user exclusionStatus ("Y" = excluded).
    Rate-limited to 1 request/second.

  - Spelpaus (Sweden, SGA): Sends MD5-hashed user IDs for privacy.
    Response: list of ALLOWED hashes (excluded = NOT in the list).
    Schedule-aware: first Monday of month → full sweep; otherwise
    only non-marketing-excluded users.

CLI usage:
    python national_exclusion.py -env prod [-proc gamstop|spelpaus]
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx
import psycopg2
import psycopg2.extras
import structlog

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

@dataclass
class GamstopUser:
    id:         int
    first_name: str
    last_name:  str
    dob:        str       # "YYYY-MM-DD"
    email:      Optional[str]
    postcode:   str
    mobile:     Optional[str]


@dataclass
class SpelpausUser:
    id:  int
    ssn: str              # Swedish personnummer: YYMMDD-NNNN
    dob: date


@dataclass
class ProcessorResult:
    registry_name:  str
    users_checked:  int
    users_excluded: int
    errors:         list[str] = field(default_factory=list)


@dataclass
class GamstopApiConfig:
    batch_service_url:        str
    api_key:                  str
    response_timeout_seconds: int = 30


@dataclass
class SpelpausApiConfig:
    batch_service_url:        str
    api_key:                  str
    actor_id:                 str
    response_timeout_seconds: int = 30


# ---------------------------------------------------------------------------
# DAO: eligible users queries
# ---------------------------------------------------------------------------

class GamstopUserDAO:
    """
    Fetch UK users eligible for Gamstop checks.

    Eligibility:
      - Country = 'GB'
      - firstName, lastName, DOB, postcode all present
      - No active NATIONAL_EXCLUSION lock
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def fetch_eligible_users(self) -> list[GamstopUser]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ui.userid, ui.firstname, ui.lastname, ui.dob,
                       ui.email, ui.postalcode, ui.phone
                FROM platform.user_info ui
                LEFT JOIN (
                    SELECT DISTINCT ul.user_id, 1 AS national_excluded
                    FROM platform.user_lock ul
                    WHERE ul.lock_type_id = 'NATIONAL_EXCLUSION'
                      AND ul.status NOT IN ('CANCELLED','COMPLETED')
                ) ul ON ul.user_id = ui.userid
                WHERE ui.country = 'GB'
                  AND ui.firstname IS NOT NULL
                  AND ui.lastname IS NOT NULL
                  AND ui.dob IS NOT NULL
                  AND ui.postalcode IS NOT NULL
                  AND ul.national_excluded IS NULL
                """
            )
            rows = cur.fetchall()
        return [
            GamstopUser(
                id=r["userid"],
                first_name=r["firstname"],
                last_name=r["lastname"],
                dob=str(r["dob"]),
                email=r.get("email"),
                postcode=r["postalcode"],
                mobile=r.get("phone"),
            )
            for r in rows
        ]


class SpelpausUserDAO:
    """
    Fetch Swedish users eligible for Spelpaus checks.

    Eligibility:
      - Country = 'SE'
      - Valid SSN (YYMMDD-NNNN format), DOB present
      - No active NATIONAL_EXCLUSION lock

    Schedule-aware:
      - First Monday of month → ALL eligible Swedish users
      - Otherwise → only users with exclude_from_marketing = 0
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @staticmethod
    def _is_first_monday() -> bool:
        today = date.today()
        return today.day <= 7 and today.weekday() == 0  # Monday

    def fetch_eligible_users(self) -> list[SpelpausUser]:
        first_monday = self._is_first_monday()
        marketing_filter = "" if first_monday else "AND u.exclude_from_marketing = 0"

        query = f"""
            SELECT ui.userid, ui.ssn, ui.dob
            FROM platform.user_info ui
            INNER JOIN platform.users u ON u.id = ui.userid
            LEFT JOIN (
                SELECT DISTINCT ul.user_id, 1 AS national_excluded
                FROM platform.user_lock ul
                WHERE ul.lock_type_id = 'NATIONAL_EXCLUSION'
                  AND ul.status NOT IN ('CANCELLED','COMPLETED')
            ) ul ON ul.user_id = ui.userid
            WHERE ui.country = 'SE'
              AND ui.ssn IS NOT NULL
              AND ui.dob IS NOT NULL
              AND ui.ssn ~ '^[0-9]{{6}}-[0-9]{{4}}$'
              AND ul.national_excluded IS NULL
              {marketing_filter}
        """
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        log.info("spelpaus eligible users", count=len(rows), full_sweep=first_monday)
        return [SpelpausUser(id=r["userid"], ssn=r["ssn"], dob=r["dob"]) for r in rows]


class NationalExclusionDAO:
    """
    Persist exclusion tasks to the platform task queue.

    Each task triggers: account suspension, marketing suppression,
    balance withdrawal, player notification.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def create_exclusion_tasks(self, user_ids: list[int], registry_name: str) -> None:
        now = datetime.now(timezone.utc)
        description = "National exclusion check on Bulk marketing check"
        with self._conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO platform.user_tasks
                    (user_id, task_type, description, status, created_date, scheduled_date)
                VALUES (%s, 'national-exclusion', %s, 'PENDING', %s, %s)
                """,
                [(uid, description, now, now) for uid in user_ids],
            )
        self._conn.commit()
        log.info("exclusion tasks created", registry=registry_name, count=len(user_ids))


# ---------------------------------------------------------------------------
# Excluded users handler
# ---------------------------------------------------------------------------

class ExcludedUsersHandler:
    """Creates platform tasks for each newly excluded user."""

    def __init__(self, exclusion_dao: NationalExclusionDAO) -> None:
        self._dao = exclusion_dao

    def handle_excluded_users(self, users: list[Any], registry_name: str) -> None:
        user_ids = [u.id for u in users]
        if not user_ids:
            return
        log.info("creating exclusion tasks", registry=registry_name, count=len(user_ids))
        self._dao.create_exclusion_tasks(user_ids, registry_name)


# ---------------------------------------------------------------------------
# Abstract processor
# ---------------------------------------------------------------------------

class NationalExclusionProcessor(ABC):
    """
    Abstract interface for national registry exclusion checks.

    Each jurisdiction (UK/Gamstop, Sweden/Spelpaus) implements this
    with registry-specific API calls and exclusion logic.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def process(self, batch_size: int = 1_000) -> ProcessorResult: ...


# ---------------------------------------------------------------------------
# Gamstop processor (UK, UKGC-licensed)
# ---------------------------------------------------------------------------

class GamstopProcessor(NationalExclusionProcessor):
    """
    GAMSTOP exclusion check (United Kingdom).

    Sends PII (name, DOB, postcode) directly — no hashing.
    API returns per-user exclusionStatus: "Y" = excluded.
    Rate-limited: 1 request/second (enforced by sleep between batches).

    POST https://batch.gamstop.io/v2
    Header: X-Api-Key: {key}
    Body: JSON array of user PII objects
    Response: JSON array with { exclusionStatus: "Y" | "N" }
    """

    DEFAULT_BATCH_SIZE = 1_000

    def __init__(
        self,
        dao: GamstopUserDAO,
        api_config: GamstopApiConfig,
        handler: ExcludedUsersHandler,
    ) -> None:
        self._dao    = dao
        self._config = api_config
        self._handler = handler

    @property
    def name(self) -> str:
        return "gamstop"

    def process(self, batch_size: int = DEFAULT_BATCH_SIZE) -> ProcessorResult:
        users = self._dao.fetch_eligible_users()
        log.info("gamstop: eligible UK users", count=len(users))

        if not users:
            return ProcessorResult(self.name, 0, 0)

        batches = [users[i:i+batch_size] for i in range(0, len(users), batch_size)]
        total_excluded = 0

        for idx, batch in enumerate(batches):
            log.info("gamstop: processing batch", batch=idx+1, total=len(batches), size=len(batch))
            try:
                responses = self._call_api(batch)
                excluded = [
                    user for user, resp in zip(batch, responses)
                    if resp.get("exclusionStatus") == "Y"
                ]
                if excluded:
                    log.info("gamstop: excluded in batch", count=len(excluded), batch=idx+1)
                    total_excluded += len(excluded)
                    self._handler.handle_excluded_users(excluded, self.name)
            except Exception as exc:
                log.error("gamstop: batch failed", batch=idx+1, error=str(exc))

            # Rate limit: 1 batch per second
            time.sleep(1.0)

        return ProcessorResult(self.name, len(users), total_excluded)

    def _call_api(self, batch: list[GamstopUser]) -> list[dict]:
        payload = [
            {
                "firstName":    u.first_name,
                "lastName":     u.last_name,
                "dateOfBirth":  u.dob,
                "postcode":     u.postcode,
                **({"email":  u.email}  if u.email  else {}),
                **({"mobile": u.mobile} if u.mobile else {}),
            }
            for u in batch
        ]
        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.post(
                self._config.batch_service_url,
                json=payload,
                headers={
                    "X-Api-Key":     self._config.api_key,
                    "Content-Type":  "application/json",
                },
            )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Spelpaus processor (Sweden, SGA-licensed)
# ---------------------------------------------------------------------------

class SpelpausProcessor(NationalExclusionProcessor):
    """
    Spelpaus exclusion check (Sweden).

    Privacy-preserving design:
      - User IDs are MD5-hashed before transmission
      - The API returns the list of ALLOWED hashes (not excluded)
      - excluded = NOT in the allowed list

    Schedule-aware:
      - Mon/Wed/Fri: non-marketing-excluded users only (incremental)
      - First Monday of month: all Swedish users (full sweep)

    POST https://api.spelpaus.se/api/marketing-subjectid/{actor-id}
    Header: X-Api-Key: {key}
    Body: JSON array of MD5-hashed user IDs
    Response: JSON array of allowed (non-excluded) hashes
    """

    DEFAULT_BATCH_SIZE = 10_000

    def __init__(
        self,
        dao: SpelpausUserDAO,
        api_config: SpelpausApiConfig,
        handler: ExcludedUsersHandler,
    ) -> None:
        self._dao    = dao
        self._config = api_config
        self._handler = handler

    @property
    def name(self) -> str:
        return "spelpaus"

    def process(self, batch_size: int = DEFAULT_BATCH_SIZE) -> ProcessorResult:
        users = self._dao.fetch_eligible_users()
        log.info("spelpaus: eligible Swedish users", count=len(users))

        if not users:
            return ProcessorResult(self.name, 0, 0)

        batches = [users[i:i+batch_size] for i in range(0, len(users), batch_size)]
        total_excluded = 0

        for idx, batch in enumerate(batches):
            log.info("spelpaus: processing batch", batch=idx+1, total=len(batches), size=len(batch))
            try:
                # Hash IDs before sending to API (privacy)
                hashed_pairs = [(hashlib.md5(str(u.id).encode()).hexdigest(), u) for u in batch]
                hashed_ids   = [h for h, _ in hashed_pairs]

                allowed_hashes = self._call_api(hashed_ids)

                # Excluded = those whose hash is NOT in the allowed set
                excluded = [
                    user for hsh, user in hashed_pairs
                    if hsh not in allowed_hashes
                ]
                if excluded:
                    log.info("spelpaus: excluded in batch", count=len(excluded), batch=idx+1)
                    total_excluded += len(excluded)
                    self._handler.handle_excluded_users(excluded, self.name)
            except Exception as exc:
                log.error("spelpaus: batch failed", batch=idx+1, error=str(exc))

        return ProcessorResult(self.name, len(users), total_excluded)

    def _call_api(self, hashed_ids: list[str]) -> set[str]:
        url = f"{self._config.batch_service_url}/{self._config.actor_id}"
        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.post(
                url,
                json=hashed_ids,
                headers={
                    "X-Api-Key":    self._config.api_key,
                    "Content-Type": "application/json",
                },
            )
        resp.raise_for_status()
        return set(resp.json())


# ---------------------------------------------------------------------------
# CLI entry point (mirrors Run.scala)
# ---------------------------------------------------------------------------

def main() -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )

    parser = argparse.ArgumentParser(
        description="National Exclusion Tool — Spelpaus (SE) and GAMSTOP (UK)"
    )
    parser.add_argument("-env",  required=True,
                        help="Environment name (maps to env-specific DB/API config)")
    parser.add_argument("-proc", choices=["spelpaus", "gamstop"],
                        help="Run only one processor (omit to run both)")
    args = parser.parse_args()

    log.info("national exclusion tool starting", env=args.env, proc=args.proc or "all")

    db_url = os.environ.get("DATABASE_URL", "")
    conn   = psycopg2.connect(db_url)

    gamstop_config = GamstopApiConfig(
        batch_service_url=os.environ.get("GAMSTOP_API_URL",  "https://batch.gamstop.io/v2"),
        api_key=os.environ.get("GAMSTOP_API_KEY", ""),
    )
    spelpaus_config = SpelpausApiConfig(
        batch_service_url=os.environ.get("SPELPAUS_API_URL",
                                         "https://api.spelpaus.se/api/marketing-subjectid"),
        api_key=os.environ.get("SPELPAUS_API_KEY", ""),
        actor_id=os.environ.get("SPELPAUS_ACTOR_ID", ""),
    )

    excl_dao = NationalExclusionDAO(conn)
    handler  = ExcludedUsersHandler(excl_dao)

    all_processors: list[NationalExclusionProcessor] = [
        SpelpausProcessor(SpelpausUserDAO(conn), spelpaus_config, handler),
        GamstopProcessor(GamstopUserDAO(conn), gamstop_config, handler),
    ]

    if args.proc:
        processors = [p for p in all_processors if p.name == args.proc]
        if not processors:
            log.error("unknown processor", proc=args.proc)
            sys.exit(1)
    else:
        processors = all_processors

    total_checked  = 0
    total_excluded = 0

    for proc in processors:
        log.info("starting processor", name=proc.name)
        try:
            result = proc.process()
            log.info(
                "processor complete",
                registry=result.registry_name,
                checked=result.users_checked,
                excluded=result.users_excluded,
            )
            total_checked  += result.users_checked
            total_excluded += result.users_excluded
        except Exception as exc:
            log.error("processor failed", name=proc.name, error=str(exc))

    log.info(
        "national exclusion tool complete",
        total_checked=total_checked,
        total_excluded=total_excluded,
    )
    conn.close()


if __name__ == "__main__":
    main()
