# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
session_guard — Session Lifecycle Manager
==========================================

``SessionGuard`` is the single authority over player sessions.  It:

* **Issues** cryptographically random session tokens and stores them with a TTL.
* **Validates** tokens, returning a live ``AuthContext`` or raising on failure.
* **Invalidates** tokens on logout or suspicious events.
* **Enforces** per-player concurrent-session limits (configurable, default 3).
* **Tracks** a reality-check timer so the front-end can prompt players at
  configured intervals (e.g. every 30 minutes of play).

Implementation notes
--------------------
This module uses an in-memory ``dict`` as the session store, which is
appropriate for a single-process service or tests.  In production you would
replace ``_store`` with a Redis-backed adapter that preserves TTL semantics.

The session token is an opaque 256-bit hex string (32 bytes from
``secrets.token_bytes``).  It is **not** a JWT — the session state lives
server-side so that invalidation is instant and irrevocable.

Thread safety
~~~~~~~~~~~~~
``SessionGuard`` acquires a ``threading.Lock`` on every mutating operation.
This is correct for a multi-threaded WSGI server but irrelevant for asyncio;
in an async context wrap the store with ``asyncio.Lock`` instead.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from acmetocasino.security.auth_context import AuthContext


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------


class SessionStatus(str, Enum):
    """Health state of a session token.

    ACTIVE
        Token is valid and not yet expired.
    EXPIRED
        Token existed but its TTL has passed.
    INVALID
        Token was never issued or has been invalidated.
    REALITY_CHECK_DUE
        Token is active but the player's reality-check interval has elapsed.
    """

    ACTIVE = "active"
    EXPIRED = "expired"
    INVALID = "invalid"
    REALITY_CHECK_DUE = "reality_check_due"


@dataclass
class _SessionRecord:
    """Internal store entry — not exposed to callers."""

    context: AuthContext
    token: str
    created_at: datetime
    last_reality_check: datetime
    reality_check_interval_minutes: int


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SessionError(Exception):
    """Base class for session-related errors."""


class SessionExpiredError(SessionError):
    """Raised when the session token exists but has passed its TTL."""


class SessionInvalidError(SessionError):
    """Raised when the session token is not found in the store."""


class TooManySessionsError(SessionError):
    """Raised when the player already has the maximum number of active sessions."""


# ---------------------------------------------------------------------------
# SessionGuard
# ---------------------------------------------------------------------------


class SessionGuard:
    """Manages the full lifecycle of player sessions.

    Parameters
    ----------
    session_ttl_minutes:
        How long a session remains valid after issuance.  Default: 480 (8 h).
    max_concurrent_sessions:
        Maximum number of simultaneous active sessions per player.
        Default: 3.
    reality_check_interval_minutes:
        How often (in minutes of wall-clock time) the guard considers a
        reality-check prompt due.  Default: 30.

    Examples
    --------
    >>> guard = SessionGuard()
    >>> token = guard.create_session("player-1", "brand-a", "1.2.3.4", "MT")
    >>> ctx = guard.validate_session(token)
    >>> guard.invalidate_session(token)
    """

    def __init__(
        self,
        *,
        session_ttl_minutes: int = 480,
        max_concurrent_sessions: int = 3,
        reality_check_interval_minutes: int = 30,
    ) -> None:
        self._ttl = timedelta(minutes=session_ttl_minutes)
        self._max_sessions = max_concurrent_sessions
        self._reality_check_interval = reality_check_interval_minutes

        # token → _SessionRecord
        self._store: dict[str, _SessionRecord] = {}
        # player_id → set of active tokens
        self._player_tokens: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(
        self,
        player_id: str,
        brand_id: str,
        ip_address: str,
        jurisdiction: str,
        roles: list[str] | None = None,
    ) -> str:
        """Issue a new session token for a player.

        Parameters
        ----------
        player_id:
            Platform player identifier.
        brand_id:
            White-label brand that owns the player.
        ip_address:
            Current IP address of the player.
        jurisdiction:
            ISO 3166-1 alpha-2 jurisdiction code.
        roles:
            Optional list of entitlement roles.  Defaults to ``["player"]``.

        Returns
        -------
        str
            Opaque 64-character hex session token.

        Raises
        ------
        TooManySessionsError
            If the player already has ``max_concurrent_sessions`` active sessions.
        """
        roles = roles or ["player"]
        now = datetime.now(timezone.utc)

        with self._lock:
            self._evict_expired_for_player(player_id, now)

            active = self._player_tokens.get(player_id, set())
            if len(active) >= self._max_sessions:
                raise TooManySessionsError(
                    f"Player {player_id!r} already has {len(active)} active sessions "
                    f"(limit: {self._max_sessions})"
                )

            token = secrets.token_hex(32)  # 256 bits
            session_id = str(uuid.uuid4())

            context = AuthContext(
                player_id=player_id,
                brand_id=brand_id,
                session_id=session_id,
                issued_at=now,
                expires_at=now + self._ttl,
                ip_address=ip_address,
                jurisdiction=jurisdiction,
                roles=roles,
            )

            record = _SessionRecord(
                context=context,
                token=token,
                created_at=now,
                last_reality_check=now,
                reality_check_interval_minutes=self._reality_check_interval,
            )

            self._store[token] = record
            self._player_tokens.setdefault(player_id, set()).add(token)

        return token

    def validate_session(self, session_token: str) -> AuthContext:
        """Validate *session_token* and return the associated ``AuthContext``.

        Parameters
        ----------
        session_token:
            Token previously returned by :meth:`create_session`.

        Returns
        -------
        AuthContext
            The live identity context for the session.

        Raises
        ------
        SessionInvalidError
            Token is not in the store (never issued or already invalidated).
        SessionExpiredError
            Token was issued but its TTL has elapsed.
        """
        now = datetime.now(timezone.utc)

        with self._lock:
            record = self._store.get(session_token)
            if record is None:
                raise SessionInvalidError("Session token not found")

            if record.context.is_expired(now=now):
                self._remove_token(session_token, record.context.player_id)
                raise SessionExpiredError("Session token has expired")

        return record.context

    def invalidate_session(self, session_token: str) -> None:
        """Immediately revoke *session_token*.

        Silently no-ops if the token is not in the store (already expired or
        never existed), so callers do not need defensive checks before calling.

        Parameters
        ----------
        session_token:
            Token to revoke.
        """
        with self._lock:
            record = self._store.get(session_token)
            if record is not None:
                self._remove_token(session_token, record.context.player_id)

    def check_session_health(self, session_token: str) -> SessionStatus:
        """Return the current health status of *session_token*.

        This method never raises; it translates all error conditions into the
        appropriate ``SessionStatus`` value.

        Parameters
        ----------
        session_token:
            Token to inspect.

        Returns
        -------
        SessionStatus
            The current state of the token.
        """
        now = datetime.now(timezone.utc)

        with self._lock:
            record = self._store.get(session_token)
            if record is None:
                return SessionStatus.INVALID

            if record.context.is_expired(now=now):
                self._remove_token(session_token, record.context.player_id)
                return SessionStatus.EXPIRED

            elapsed = (now - record.last_reality_check).total_seconds() / 60
            if elapsed >= record.reality_check_interval_minutes:
                return SessionStatus.REALITY_CHECK_DUE

        return SessionStatus.ACTIVE

    def acknowledge_reality_check(self, session_token: str) -> None:
        """Reset the reality-check timer for *session_token*.

        Called when the player acknowledges the reality-check prompt in the UI.

        Parameters
        ----------
        session_token:
            Token whose reality-check timer should be reset.

        Raises
        ------
        SessionInvalidError
            If the token is not found or already expired.
        """
        now = datetime.now(timezone.utc)

        with self._lock:
            record = self._store.get(session_token)
            if record is None or record.context.is_expired(now=now):
                raise SessionInvalidError(
                    "Cannot acknowledge reality check: session not active"
                )
            record.last_reality_check = now

    def active_session_count(self, player_id: str) -> int:
        """Return the number of active sessions for *player_id*.

        Expired sessions are evicted before counting.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            self._evict_expired_for_player(player_id, now)
            return len(self._player_tokens.get(player_id, set()))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _remove_token(self, token: str, player_id: str) -> None:
        """Remove *token* from both indexes.  Must be called under lock."""
        self._store.pop(token, None)
        tokens = self._player_tokens.get(player_id, set())
        tokens.discard(token)
        if not tokens:
            self._player_tokens.pop(player_id, None)

    def _evict_expired_for_player(self, player_id: str, now: datetime) -> None:
        """Remove all expired tokens for *player_id*.  Must be called under lock."""
        tokens = self._player_tokens.get(player_id, set())
        expired = {
            t
            for t in tokens
            if t in self._store and self._store[t].context.is_expired(now=now)
        }
        for t in expired:
            self._remove_token(t, player_id)
