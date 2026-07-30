# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Retention bonus engine services.

Provides:
- RetentionBonusType: protocol (abstract base) every bonus type must implement
- RetentionBonusCalculator: orchestrates bonus calculation for a given date
- BonusAllocationQueue: processes pending allocations in configurable chunks
- PlatformDatabase: database access for the casino_core schema

Algorithm (from the Scala original):
  Step 1: Run the bonus type's targeting SQL; INSERT eligible players into
          DAILY_BONUS_ALLOCATION with BONUS_ALLOCATED=false.
          NOT EXISTS guard makes this idempotent on re-run.

  Step 2: Process unallocated rows in chunks of 100 (default).
          For each row: create bonus account, then mark as allocated.
          Chunked tail-recursive approach:
            - Memory safe (never loads full allocation set)
            - Crash-recoverable (only unprocessed rows picked up on restart)

The BONUS_ALLOCATED flag is the key to idempotency. If the process crashes
after creating a bonus account but before marking the row, the bonus account
creation logic must also be idempotent (checked via existing account lookup).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import date
from typing import Callable

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .models import BonusQueueItem, RetentionBonusSchedule

log = structlog.get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/platform")


# ---------------------------------------------------------------------------
# Platform database
# ---------------------------------------------------------------------------

class PlatformDatabase:
    """Synchronous database access for the casino_core schema."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def execute(self, sql: str, params: dict | None = None) -> list[dict]:
        with self._engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return [dict(r._mapping) for r in result]

    def execute_insert(self, sql: str, params: dict | None = None) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return result.rowcount


# ---------------------------------------------------------------------------
# RetentionBonusType protocol
# ---------------------------------------------------------------------------

class RetentionBonusType(ABC):
    """
    Strategy interface for retention bonus mechanisms.

    Each implementation defines how to target eligible players (via SQL),
    what bonus parameters to use, and how to record the allocation.

    The Strategy pattern lets the same calculator engine handle daily reward
    bonuses, birthday bonuses, and any future types without modification.
    """

    @property
    @abstractmethod
    def brand_id(self) -> int: ...

    @property
    @abstractmethod
    def reason(self) -> str: ...

    @property
    @abstractmethod
    def formula_used(self) -> int | None: ...

    @property
    @abstractmethod
    def bonus_group(self) -> int: ...

    @property
    @abstractmethod
    def output_columns(self) -> list[str]: ...

    @abstractmethod
    def is_data_available(self, calc_date: date) -> bool: ...

    @abstractmethod
    def generate_sql(self, calc_date: date) -> str:
        """Return SQL that selects eligible players with their bonus amounts."""
        ...

    def log_players(self, calc_date: date) -> None:
        """Log eligible players without applying bonuses (preview mode)."""
        sql = self.generate_sql(calc_date)
        log.info("retention.preview_mode", reason=self.reason, sql=sql[:200])


# ---------------------------------------------------------------------------
# Bonus allocation queue
# ---------------------------------------------------------------------------

class BonusAllocationQueue:
    """
    Queue-based processor for pending bonus allocations.

    Processes DAILY_BONUS_ALLOCATION rows where BONUS_ALLOCATED = false in
    configurable chunks. Each row is processed then immediately marked as
    allocated before fetching the next chunk.
    """

    CHUNK_SQL = """
        SELECT a.user_id, a.bonus
        FROM casino_core.daily_bonus_allocation a
        WHERE a.brand_id = :brand_id
          AND a.reason = :reason
          AND a.calc_date = :calc_date
          AND a.bonus_allocated = false
        LIMIT :chunk_size
    """

    MARK_ALLOCATED_SQL = """
        UPDATE casino_core.daily_bonus_allocation
        SET bonus_allocated = true
        WHERE calc_date = :calc_date
          AND user_id = :user_id
          AND reason = :reason
    """

    def __init__(
        self,
        db: PlatformDatabase,
        calc_date: date,
        brand_id: int,
        reason: str,
    ) -> None:
        self._db = db
        self._calc_date = calc_date
        self._brand_id = brand_id
        self._reason = reason

    def process_queue(
        self,
        proc: Callable[[BonusQueueItem], None],
        chunk_size: int = 100,
    ) -> int:
        """
        Process all pending allocations in chunks.

        Returns the total number of bonuses processed.
        """
        total = 0
        while True:
            rows = self._db.execute(
                self.CHUNK_SQL,
                {
                    "brand_id": self._brand_id,
                    "reason": self._reason,
                    "calc_date": self._calc_date,
                    "chunk_size": chunk_size,
                },
            )
            if not rows:
                break
            for row in rows:
                item = BonusQueueItem(
                    reason=self._reason,
                    brand_id=self._brand_id,
                    user_id=row["user_id"],
                    bonus=row["bonus"],
                )
                proc(item)
                self._db.execute_insert(
                    self.MARK_ALLOCATED_SQL,
                    {
                        "calc_date": self._calc_date,
                        "user_id": item.user_id,
                        "reason": self._reason,
                    },
                )
                total += 1
            if len(rows) < chunk_size:
                break
        return total


# ---------------------------------------------------------------------------
# RetentionBonusCalculator
# ---------------------------------------------------------------------------

class RetentionBonusCalculator:
    """
    Core retention bonus calculation engine.

    Template Method pattern:
      - This class defines the algorithm skeleton
      - Concrete bonus types supply the targeting SQL and bonus parameters
    """

    def __init__(
        self,
        db: PlatformDatabase,
        bonus_type: RetentionBonusType,
    ) -> None:
        self._db = db
        self._bonus_type = bonus_type

    def determine_bonuses(
        self,
        calc_date: date,
        read_only: bool = False,
        apply_bonus: Callable[[BonusQueueItem], None] | None = None,
    ) -> tuple[int, int]:
        """
        Calculate and apply bonuses for a given date.

        Args:
            calc_date:  Date to calculate bonuses for (typically yesterday)
            read_only:  If True, log eligible players without creating bonuses
            apply_bonus: Callback to create the actual bonus account for each item

        Returns:
            (players_found, bonuses_applied)
        """
        if read_only:
            log.info("retention.read_only_mode")
            self._bonus_type.log_players(calc_date)
            return 0, 0

        # Step 1: Insert eligible players into allocation table (idempotent)
        select_sql = self._bonus_type.generate_sql(calc_date)
        insert_sql = self._build_insert_sql(calc_date, select_sql)
        n_players = self._db.execute_insert(insert_sql, {"calc_date": str(calc_date)})
        log.debug(
            "retention.players_inserted",
            count=n_players,
            reason=self._bonus_type.reason,
        )

        # Step 2: Process allocation queue in chunks
        queue = BonusAllocationQueue(
            self._db,
            calc_date,
            self._bonus_type.brand_id,
            self._bonus_type.reason,
        )
        handler = apply_bonus or self._noop_handler
        n_bonuses = queue.process_queue(handler)
        log.debug(
            "retention.bonuses_applied",
            count=n_bonuses,
            reason=self._bonus_type.reason,
        )
        return n_players, n_bonuses

    def _build_insert_sql(self, calc_date: date, select_sql: str) -> str:
        formula_id = str(self._bonus_type.formula_used or "")
        extra_cols = ", ".join(self._bonus_type.output_columns)
        extra_vals = ", ".join(f"s.{c}" for c in self._bonus_type.output_columns)
        reason = self._bonus_type.reason

        return f"""
            INSERT INTO casino_core.daily_bonus_allocation
                (calc_date, formula_id, reason, {extra_cols})
            SELECT '{calc_date}' calc_date,
                   '{formula_id}' formula_id,
                   '{reason}' reason,
                   {extra_vals}
              FROM ({select_sql}) s
             WHERE NOT EXISTS (
                SELECT 1 FROM casino_core.daily_bonus_allocation a
                WHERE a.calc_date = '{calc_date}'
                  AND s.user_id = a.user_id
                  AND a.reason = '{reason}'
             )
        """

    @staticmethod
    def _noop_handler(item: BonusQueueItem) -> None:
        log.info("retention.bonus_allocated", user_id=item.user_id, bonus=item.bonus)
