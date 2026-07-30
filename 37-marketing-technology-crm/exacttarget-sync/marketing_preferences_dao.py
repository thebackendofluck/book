# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Database access layer for marketing preference updates.

Python port of MarketingPreferencesDAO.scala referenced in chapter 37.
The DAO mediates every write to the `marketing_preferences` table --
the source of truth for whether a given player's email address may
be used for marketing campaigns. Three logical events flow through
this class:

1. **Unsubscribe** -- the player clicked the unsubscribe link inside
   an ExactTarget email. ExactTarget writes this into its daily unsub
   file, the import task downloads it, and this DAO persists the
   change so that the platform's own compliance checks honour it
   across every channel.
2. **Hard bounce** -- the remote MX rejected the delivery with a
   permanent failure. Same treatment as unsubscribe plus a `reason`
   column that distinguishes the two for audit purposes.
3. **Manual override** -- a compliance operator restores marketing
   consent after investigating a bounce that turned out to be a
   transient infrastructure issue.

The DAO uses an idempotent **check-before-insert** pattern: when the
import task retries after a network blip, the second run must not
create duplicate rows. The `ensure_preference` method therefore
either updates an existing row or inserts a new one, both in a
single round-trip via the standard `INSERT ... ON CONFLICT` pattern.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class MarketingReason(enum.Enum):
    """Why a marketing preference was set to its current value."""

    UNSUBSCRIBE = "unsubscribe"
    HARD_BOUNCE = "hard_bounce"
    MANUAL_OVERRIDE = "manual_override"
    INITIAL_OPT_IN = "initial_opt_in"


@dataclass(frozen=True)
class MarketingPreference:
    """A row from the `marketing_preferences` table."""

    user_id: int
    email_enabled: bool
    reason: MarketingReason
    updated_at: datetime
    updated_by: str  # system user or compliance operator username


class DatabaseCursor(Protocol):
    """Minimal DB-API 2.0 cursor interface used by this DAO.

    Production code passes a psycopg2 / psycopg cursor; tests inject
    an in-memory stub that records the SQL and parameters issued so
    assertions can be made without touching a real database.
    """

    def execute(self, sql: str, params: "tuple[object, ...] | list[object] | None" = None) -> None: ...
    def fetchone(self) -> "tuple[object, ...] | None": ...
    def fetchall(self) -> "list[tuple[object, ...]]": ...


class MarketingPreferencesDao:
    """Idempotent DAO for marketing preference writes."""

    def __init__(self, cursor_factory: "object") -> None:
        # The factory is a callable returning a fresh cursor for each
        # transaction; this way the DAO does not hold a long-lived
        # reference to the DB connection pool and callers remain in
        # control of commit/rollback timing.
        self._cursor_factory = cursor_factory

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record_unsubscribe(self, user_id: int, *, source: str = "exacttarget") -> None:
        """Persist an ExactTarget-originated unsubscribe event."""
        self.ensure_preference(
            user_id=user_id,
            email_enabled=False,
            reason=MarketingReason.UNSUBSCRIBE,
            updated_by=source,
        )

    def record_hard_bounce(self, user_id: int, *, source: str = "exacttarget") -> None:
        """Persist a hard bounce event. Disables email delivery and
        sets the reason column so the compliance team can distinguish
        bounces from voluntary opt-outs when a player later asks why
        they stopped receiving emails.
        """
        self.ensure_preference(
            user_id=user_id,
            email_enabled=False,
            reason=MarketingReason.HARD_BOUNCE,
            updated_by=source,
        )

    def ensure_preference(
        self,
        *,
        user_id: int,
        email_enabled: bool,
        reason: MarketingReason,
        updated_by: str,
    ) -> None:
        """Idempotent upsert.

        Running this twice with the same inputs leaves the row in the
        same state as running it once -- no history duplication, no
        accidentally-reactivated consent.
        """
        sql = (
            "INSERT INTO marketing_preferences "
            "(user_id, email_enabled, reason, updated_at, updated_by) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "    email_enabled = EXCLUDED.email_enabled, "
            "    reason = EXCLUDED.reason, "
            "    updated_at = EXCLUDED.updated_at, "
            "    updated_by = EXCLUDED.updated_by "
            "WHERE marketing_preferences.email_enabled IS DISTINCT FROM EXCLUDED.email_enabled "
            "   OR marketing_preferences.reason IS DISTINCT FROM EXCLUDED.reason"
        )
        params = (
            user_id,
            email_enabled,
            reason.value,
            datetime.now(timezone.utc),
            updated_by,
        )
        with self._open_cursor() as cur:
            cur.execute(sql, params)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_preference(self, user_id: int) -> MarketingPreference | None:
        sql = (
            "SELECT user_id, email_enabled, reason, updated_at, updated_by "
            "FROM marketing_preferences WHERE user_id = %s"
        )
        with self._open_cursor() as cur:
            cur.execute(sql, (user_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_preference(row)

    def list_disabled_reasons(self, user_ids: list[int]) -> dict[int, MarketingReason]:
        """Return the reason each user has marketing disabled.

        Users whose `email_enabled` flag is True or who have no row
        are omitted from the result. Used by the import task's
        post-run verification step.
        """
        if not user_ids:
            return {}
        placeholders = ",".join(["%s"] * len(user_ids))
        sql = (
            "SELECT user_id, reason FROM marketing_preferences "
            f"WHERE email_enabled = false AND user_id IN ({placeholders})"
        )
        with self._open_cursor() as cur:
            cur.execute(sql, tuple(user_ids))
            rows = cur.fetchall()
        result: dict[int, MarketingReason] = {}
        for row in rows:
            uid = int(row[0])  # type: ignore[arg-type]
            reason_str = str(row[1])
            try:
                result[uid] = MarketingReason(reason_str)
            except ValueError:
                # Unknown reason in the DB -- skip rather than crash
                # so an adhoc reason introduced by an ops script does
                # not break the daily import job.
                pass
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open_cursor(self) -> "DatabaseCursorContext":
        return DatabaseCursorContext(self._cursor_factory)

    @staticmethod
    def _row_to_preference(row: "tuple[object, ...]") -> MarketingPreference:
        user_id = int(row[0])  # type: ignore[arg-type]
        email_enabled = bool(row[1])
        reason = MarketingReason(str(row[2]))
        updated_at_raw = row[3]
        if isinstance(updated_at_raw, datetime):
            updated_at = updated_at_raw
        else:
            updated_at = datetime.fromisoformat(str(updated_at_raw))
        updated_by = str(row[4])
        return MarketingPreference(
            user_id=user_id,
            email_enabled=email_enabled,
            reason=reason,
            updated_at=updated_at,
            updated_by=updated_by,
        )


class DatabaseCursorContext:
    """Minimal context manager wrapper around a cursor factory.

    Tests pass a factory that returns an in-memory stub cursor; this
    wrapper provides the `with ... as cur:` affordance the DAO uses.
    """

    def __init__(self, factory: "object") -> None:
        self._factory = factory
        self._cursor: DatabaseCursor | None = None

    def __enter__(self) -> DatabaseCursor:
        self._cursor = self._factory()  # type: ignore[operator]
        assert self._cursor is not None
        return self._cursor

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        cur = self._cursor
        if cur is not None and hasattr(cur, "close"):
            try:
                cur.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._cursor = None
