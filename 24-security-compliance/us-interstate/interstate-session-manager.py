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
Interstate Session Manager
===========================
Manages the full lifecycle of geo-verified game sessions across US state boundaries.

Core responsibilities:
  1. Issue a geo-lease on session start (calls GeoComply).
  2. Schedule periodic re-verification (geo-lease heartbeat).
  3. Detect state-crossing events (player moves from NJ to PA mid-session).
  4. Terminate or migrate sessions when a player leaves their licensed state.
  5. Emit compliance audit events for every state-transition decision.

This module is designed to run as a background service (asyncio event loop) alongside
your game server. In production, run it as a Kubernetes sidecar or a separate service
that communicates with your game engine via internal API.

Usage:
  python interstate-session-manager.py --demo

Architecture notes:
  - Each active session has an associated GeoLease stored in Redis (or any TTL-capable cache).
  - A background task polls GeoComply's lease status endpoint before each TTL expiry.
  - State-crossing events are published to an internal event bus for downstream handlers
    (game server suspension, wallet freeze, compliance alert).
  - MSIGA poker sessions are handled differently: player pool is shared across states,
    but the player's physical location must still be verified to be in a participating state.

References:
  NJ DGE N.J.A.C. 13:69O — Internet and Mobile Gaming
  PA 58 Pa. Code §§ 1200a — Internet Gaming
  MI Gaming Control and Revenue Act, 1996 PA 69 (amended 2019)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SessionState(str, Enum):
    ACTIVE = "ACTIVE"
    GEO_REVERIFYING = "GEO_REVERIFYING"    # Lease expired; re-check in progress
    GEO_SUSPENDED = "GEO_SUSPENDED"        # Player left licensed state; session suspended
    TERMINATED = "TERMINATED"              # Session ended (normal or forced)
    MIGRATING = "MIGRATING"                # Multi-state operator cross-state migration


class StateTransitionEvent(str, Enum):
    SESSION_STARTED = "SESSION_STARTED"
    GEO_LEASE_RENEWED = "GEO_LEASE_RENEWED"
    GEO_LEASE_EXPIRED = "GEO_LEASE_EXPIRED"
    PLAYER_LEFT_STATE = "PLAYER_LEFT_STATE"     # Player physically crossed state line
    PLAYER_ENTERED_STATE = "PLAYER_ENTERED_STATE"
    VPN_DETECTED = "VPN_DETECTED"
    SESSION_TERMINATED = "SESSION_TERMINATED"
    SESSION_MIGRATED = "SESSION_MIGRATED"       # Successfully migrated to new state instance
    REVERIFY_TIMEOUT = "REVERIFY_TIMEOUT"       # Grace window expired during re-verification


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class GeoLease:
    """Simplified geo-lease (see geocomply-integration.py for full version)."""
    lease_id: str
    player_id: str
    state_code: str
    expires_at: float
    confidence_score: float = 0.99

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def ttl_remaining(self) -> float:
        return max(0.0, self.expires_at - time.time())


@dataclass
class GameSession:
    """
    Represents an active player game session with geo-lease tracking.
    """
    session_id: str
    player_id: str
    operator_id: str               # Multi-state operators have one ID per state instance
    product_type: str              # CASINO | SPORTS | POKER
    licensed_state: str            # State this session was started in
    current_state: Optional[str]   # Current verified state (may differ during migration)
    state: SessionState
    geo_lease: Optional[GeoLease]
    started_at: float = field(default_factory=time.time)
    last_geo_check: float = field(default_factory=time.time)
    reverify_attempts: int = 0
    is_msiga_session: bool = False  # MSIGA shared-pool poker session

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass
class AuditEvent:
    """Compliance audit event emitted for every geo state transition."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: str = ""
    session_id: str = ""
    player_id: str = ""
    operator_id: str = ""
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    reason: str = ""
    lease_id: Optional[str] = None
    confidence_score: Optional[float] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# ---------------------------------------------------------------------------
# State-crossing handler registry
# ---------------------------------------------------------------------------

# Multi-state operator mapping: for each licensed state, which operator instance
# handles that state. In production, load from configuration store.
OPERATOR_STATE_INSTANCES: dict[str, dict[str, str]] = {
    "betmgm": {
        "NJ": "betmgm-nj",
        "PA": "betmgm-pa",
        "MI": "betmgm-mi",
        "WV": "betmgm-wv",
    },
    "draftkings": {
        "NJ": "draftkings-nj",
        "PA": "draftkings-pa",
        "MI": "draftkings-mi",
        "CT": "draftkings-ct",
    },
    "fanduel": {
        "NJ": "fanduel-nj",
        "PA": "fanduel-pa",
        "MI": "fanduel-mi",
        "WV": "fanduel-wv",
        "CT": "fanduel-ct",
    },
}

# GEO_LEASE_TTL in seconds by state (must match geocomply-integration.py)
GEO_LEASE_TTL: dict[str, int] = {
    "NJ": 660,
    "PA": 900,
    "MI": 1800,
    "WV": 900,
    "CT": 900,
    "DE": 900,
    "RI": 900,
}

# Grace window (seconds) allowed for re-verification before suspending session.
# Most state regulations allow 30–60 seconds. Check your regulator's SIC template.
REVERIFY_GRACE_SECONDS = 45
MAX_REVERIFY_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Audit logger (replace with your SIEM/structured log pipeline in production)
# ---------------------------------------------------------------------------

class ComplianceAuditLogger:
    """Writes audit events to stdout (replace with SIEM sink in production)."""

    def log(self, event: AuditEvent) -> None:
        logger.info("AUDIT %s", event.to_json())

    def log_session_start(self, session: GameSession) -> None:
        self.log(AuditEvent(
            event_type=StateTransitionEvent.SESSION_STARTED.value,
            session_id=session.session_id,
            player_id=session.player_id,
            operator_id=session.operator_id,
            to_state=session.licensed_state,
            reason="Session started with valid geo-lease",
            lease_id=session.geo_lease.lease_id if session.geo_lease else None,
            confidence_score=session.geo_lease.confidence_score if session.geo_lease else None,
        ))

    def log_state_crossing(
        self,
        session: GameSession,
        from_state: str,
        to_state: Optional[str],
        reason: str,
    ) -> None:
        self.log(AuditEvent(
            event_type=StateTransitionEvent.PLAYER_LEFT_STATE.value,
            session_id=session.session_id,
            player_id=session.player_id,
            operator_id=session.operator_id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
        ))

    def log_termination(self, session: GameSession, reason: str) -> None:
        self.log(AuditEvent(
            event_type=StateTransitionEvent.SESSION_TERMINATED.value,
            session_id=session.session_id,
            player_id=session.player_id,
            operator_id=session.operator_id,
            from_state=session.current_state,
            reason=reason,
        ))


# ---------------------------------------------------------------------------
# GeoComply verifier (stub — integrate with geocomply-integration.py)
# ---------------------------------------------------------------------------

class GeoVerifier:
    """
    Stub geo-verifier. Replace verify_session_lease() with real GeoComply API calls.
    In production, import GeoComplyClient from geocomply-integration.py.
    """

    async def verify_session_lease(
        self, session: GameSession
    ) -> tuple[bool, Optional[GeoLease], Optional[str]]:
        """
        Re-verify a player's location.
        Returns: (is_approved, new_lease, current_state_code)

        In production: call GeoComply /verify endpoint with fresh GeoPacket from client.
        The client SDK must be polled for a new GeoPacket before each re-verification.
        """
        # Simulate a 95% success rate for demo
        import random
        await asyncio.sleep(0.1)  # Simulate API latency

        if random.random() < 0.05:
            # 5% chance: player left licensed state
            return False, None, "TX"

        state = session.licensed_state
        ttl = GEO_LEASE_TTL.get(state, 900)
        lease = GeoLease(
            lease_id=str(uuid.uuid4()),
            player_id=session.player_id,
            state_code=state,
            expires_at=time.time() + ttl,
            confidence_score=0.98,
        )
        return True, lease, state


# ---------------------------------------------------------------------------
# Interstate Session Manager
# ---------------------------------------------------------------------------

class InterstateSessionManager:
    """
    Manages geo-verified game sessions across US state lines.

    Lifecycle:
      create_session() → heartbeat loop → handle_state_crossing() → terminate()

    The heartbeat loop runs every (lease_ttl - 60) seconds, requesting a fresh
    GeoPacket from the client SDK and re-verifying with GeoComply before the
    current lease expires. This ensures there is no gap in geo-verification coverage.
    """

    def __init__(
        self,
        verifier: Optional[GeoVerifier] = None,
        audit_logger: Optional[ComplianceAuditLogger] = None,
        state_crossing_callback: Optional[Callable[[GameSession, str, Optional[str]], None]] = None,
    ) -> None:
        self._verifier = verifier or GeoVerifier()
        self._audit = audit_logger or ComplianceAuditLogger()
        self._state_crossing_callback = state_crossing_callback
        self._sessions: dict[str, GameSession] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_session(
        self,
        player_id: str,
        operator_id: str,
        product_type: str,
        initial_geo_lease: GeoLease,
    ) -> GameSession:
        """
        Create a new geo-verified game session.
        The initial_geo_lease must be freshly obtained at login.
        """
        session_id = str(uuid.uuid4())
        session = GameSession(
            session_id=session_id,
            player_id=player_id,
            operator_id=operator_id,
            product_type=product_type,
            licensed_state=initial_geo_lease.state_code,
            current_state=initial_geo_lease.state_code,
            state=SessionState.ACTIVE,
            geo_lease=initial_geo_lease,
            is_msiga_session=product_type == "POKER",
        )
        self._sessions[session_id] = session
        self._audit.log_session_start(session)

        # Start heartbeat
        task = asyncio.create_task(
            self._heartbeat_loop(session_id),
            name=f"geo-heartbeat-{session_id}",
        )
        self._heartbeat_tasks[session_id] = task

        logger.info(
            "Session created session_id=%s player=%s state=%s product=%s",
            session_id, player_id, initial_geo_lease.state_code, product_type,
        )
        return session

    async def terminate_session(self, session_id: str, reason: str = "NORMAL") -> None:
        """Terminate a session and cancel its heartbeat task."""
        session = self._sessions.get(session_id)
        if not session:
            return

        session.state = SessionState.TERMINATED
        self._audit.log_termination(session, reason)

        # Cancel heartbeat
        task = self._heartbeat_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

        del self._sessions[session_id]
        logger.info("Session terminated session_id=%s reason=%s", session_id, reason)

    def get_session(self, session_id: str) -> Optional[GameSession]:
        return self._sessions.get(session_id)

    def list_active_sessions(self) -> list[GameSession]:
        return [s for s in self._sessions.values() if s.state != SessionState.TERMINATED]

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self, session_id: str) -> None:
        """
        Background task that re-verifies the player's location before each geo-lease expires.
        Runs continuously until the session is terminated.
        """
        while True:
            session = self._sessions.get(session_id)
            if not session or session.state == SessionState.TERMINATED:
                return

            lease = session.geo_lease
            if lease is None:
                await self._handle_missing_lease(session)
                return

            # Sleep until (expiry - 60 seconds) to re-verify before lease dies
            sleep_seconds = max(1.0, lease.ttl_remaining - 60)
            logger.debug(
                "Heartbeat sleeping %.1fs session=%s state=%s",
                sleep_seconds, session_id, session.licensed_state,
            )
            await asyncio.sleep(sleep_seconds)

            # Re-fetch session (may have been terminated during sleep)
            session = self._sessions.get(session_id)
            if not session or session.state == SessionState.TERMINATED:
                return

            await self._reverify_location(session)

    async def _reverify_location(self, session: GameSession) -> None:
        """Re-verify player location. Called before each geo-lease expiry."""
        session.state = SessionState.GEO_REVERIFYING
        session.last_geo_check = time.time()
        session.reverify_attempts += 1

        logger.info(
            "Re-verifying location session=%s player=%s attempt=%d",
            session.session_id, session.player_id, session.reverify_attempts,
        )

        try:
            is_approved, new_lease, current_state = (
                await asyncio.wait_for(
                    self._verifier.verify_session_lease(session),
                    timeout=REVERIFY_GRACE_SECONDS,
                )
            )
        except asyncio.TimeoutError:
            await self._handle_reverify_timeout(session)
            return

        if is_approved and new_lease:
            await self._handle_lease_renewal(session, new_lease, current_state)
        else:
            await self._handle_geo_decline(session, current_state)

    async def _handle_lease_renewal(
        self,
        session: GameSession,
        new_lease: GeoLease,
        current_state: Optional[str],
    ) -> None:
        """Handle a successful geo-lease renewal."""
        previous_state = session.current_state

        if current_state and current_state != session.licensed_state:
            # Player has physically crossed into a different state
            await self._handle_state_crossing(session, previous_state, current_state, new_lease)
        else:
            # Normal renewal — player still in licensed state
            session.geo_lease = new_lease
            session.current_state = new_lease.state_code
            session.state = SessionState.ACTIVE
            session.reverify_attempts = 0

            self._audit.log(AuditEvent(
                event_type=StateTransitionEvent.GEO_LEASE_RENEWED.value,
                session_id=session.session_id,
                player_id=session.player_id,
                operator_id=session.operator_id,
                to_state=session.current_state,
                reason="Lease renewed",
                lease_id=new_lease.lease_id,
                confidence_score=new_lease.confidence_score,
            ))
            logger.info(
                "Geo-lease renewed session=%s state=%s ttl=%ds",
                session.session_id, session.current_state, int(new_lease.ttl_remaining),
            )

    async def _handle_state_crossing(
        self,
        session: GameSession,
        from_state: Optional[str],
        to_state: str,
        new_lease: GeoLease,
    ) -> None:
        """
        Player has physically crossed a state line.

        Scenarios:
        A. Player moves from licensed state (NJ) to another licensed state (PA):
           - Suspend current session.
           - Notify player they can switch to the PA-licensed operator instance.
           - If operator has PA license: offer migration.
           - Balance remains intact; only active session is terminated.

        B. Player moves from licensed state to unlicensed state (NJ → TX):
           - Terminate session immediately.
           - Funds already wagered are settled; remaining balance is untouched.
           - Cannot resume until player returns to a licensed state.

        C. MSIGA poker session: player moves to another MSIGA state:
           - Poker hand must complete (no mid-hand termination per MSIGA rules).
           - After hand completion, session migrates to new state's instance.
        """
        from_state_code = from_state or session.licensed_state
        self._audit.log_state_crossing(session, from_state_code, to_state, "Player crossed state line")

        logger.warning(
            "State crossing detected session=%s player=%s %s -> %s",
            session.session_id, session.player_id, from_state_code, to_state,
        )

        if self._state_crossing_callback:
            self._state_crossing_callback(session, from_state_code, to_state)

        # Check if new state is licensed for this operator
        operator_instances = OPERATOR_STATE_INSTANCES.get(session.operator_id, {})
        new_instance = operator_instances.get(to_state)

        if new_instance:
            # Operator has a license in the new state — offer migration
            logger.info(
                "Operator %s has license in %s — migrating session to %s",
                session.operator_id, to_state, new_instance,
            )
            await self._migrate_session(session, to_state, new_lease, new_instance)
        else:
            # No license in new state — terminate
            logger.warning(
                "No license for operator %s in state %s — terminating session",
                session.operator_id, to_state,
            )
            await self.terminate_session(
                session.session_id,
                reason=f"PLAYER_LEFT_LICENSED_STATE: {from_state_code} -> {to_state}",
            )

    async def _migrate_session(
        self,
        session: GameSession,
        new_state: str,
        new_lease: GeoLease,
        new_operator_instance: str,
    ) -> None:
        """
        Migrate an active session to a new state-licensed operator instance.
        The player's balance and game history are preserved; only the session context changes.
        """
        session.state = SessionState.MIGRATING
        old_state = session.licensed_state

        # In production: call internal API to migrate session to new_operator_instance
        # This involves: suspending game round, transferring session token, updating wallet
        await asyncio.sleep(0.5)  # Simulate migration API call

        session.licensed_state = new_state
        session.current_state = new_state
        session.operator_id = new_operator_instance
        session.geo_lease = new_lease
        session.state = SessionState.ACTIVE

        self._audit.log(AuditEvent(
            event_type=StateTransitionEvent.SESSION_MIGRATED.value,
            session_id=session.session_id,
            player_id=session.player_id,
            operator_id=new_operator_instance,
            from_state=old_state,
            to_state=new_state,
            reason=f"Session migrated to {new_operator_instance}",
            lease_id=new_lease.lease_id,
        ))
        logger.info(
            "Session migrated session=%s %s -> %s (instance=%s)",
            session.session_id, old_state, new_state, new_operator_instance,
        )

    async def _handle_geo_decline(
        self, session: GameSession, detected_state: Optional[str]
    ) -> None:
        """Handle a geo-verification decline (player outside licensed state or VPN detected)."""
        reason = (
            f"PLAYER_OUTSIDE_LICENSED_STATE: detected={detected_state}"
            if detected_state
            else "GEO_VERIFICATION_FAILED"
        )
        session.state = SessionState.GEO_SUSPENDED
        logger.warning(
            "Geo declined session=%s player=%s reason=%s",
            session.session_id, session.player_id, reason,
        )

        # Allow one retry within grace window
        if session.reverify_attempts < MAX_REVERIFY_ATTEMPTS:
            logger.info(
                "Allowing retry %d/%d session=%s",
                session.reverify_attempts, MAX_REVERIFY_ATTEMPTS, session.session_id,
            )
            await asyncio.sleep(15)
            await self._reverify_location(session)
        else:
            await self.terminate_session(session.session_id, reason=reason)

    async def _handle_reverify_timeout(self, session: GameSession) -> None:
        """Handle geo re-verification timeout (GeoComply API unresponsive)."""
        self._audit.log(AuditEvent(
            event_type=StateTransitionEvent.REVERIFY_TIMEOUT.value,
            session_id=session.session_id,
            player_id=session.player_id,
            operator_id=session.operator_id,
            reason="GeoComply API timeout during re-verification",
        ))

        if session.geo_lease and not session.geo_lease.is_expired:
            # Lease still valid locally — keep session alive, log the timeout
            session.state = SessionState.ACTIVE
            logger.warning(
                "Reverify timeout session=%s — using cached lease (expires in %.0fs)",
                session.session_id, session.geo_lease.ttl_remaining,
            )
        else:
            # Lease expired and cannot re-verify — must terminate
            await self.terminate_session(
                session.session_id, reason="REVERIFY_TIMEOUT_LEASE_EXPIRED"
            )

    async def _handle_missing_lease(self, session: GameSession) -> None:
        """Session has no geo-lease — terminate immediately."""
        await self.terminate_session(session.session_id, reason="NO_GEO_LEASE")


# ---------------------------------------------------------------------------
# Real-world scenario demonstrations
# ---------------------------------------------------------------------------

async def demo_scenario_nj_to_pa(manager: InterstateSessionManager) -> None:
    """
    Scenario: Player starts in NJ, drives to PA.
    BetMGM has licenses in both states.
    Expected outcome: Session migrates from betmgm-nj to betmgm-pa.
    """
    print("\n--- Scenario 1: NJ player drives to PA ---")
    lease = GeoLease(
        lease_id=str(uuid.uuid4()),
        player_id="player-nj-001",
        state_code="NJ",
        expires_at=time.time() + 660,  # 11 min NJ lease
        confidence_score=0.99,
    )
    session = await manager.create_session(
        player_id="player-nj-001",
        operator_id="betmgm",
        product_type="CASINO",
        initial_geo_lease=lease,
    )
    print(f"Session started in NJ: {session.session_id}")

    # Simulate player crossing into PA
    await asyncio.sleep(1)
    new_lease = GeoLease(
        lease_id=str(uuid.uuid4()),
        player_id="player-nj-001",
        state_code="PA",
        expires_at=time.time() + 900,
        confidence_score=0.97,
    )
    await manager._handle_state_crossing(session, "NJ", "PA", new_lease)

    current = manager.get_session(session.session_id)
    if current:
        print(f"After crossing: state={current.licensed_state}, operator={current.operator_id}")
    else:
        print("Session terminated (operator has no PA license)")


async def demo_scenario_nj_to_texas(manager: InterstateSessionManager) -> None:
    """
    Scenario: Player using VPN detected as being in Texas.
    Expected outcome: Session terminated immediately.
    """
    print("\n--- Scenario 2: NJ player detected in Texas (VPN/travel) ---")
    lease = GeoLease(
        lease_id=str(uuid.uuid4()),
        player_id="player-nj-002",
        state_code="NJ",
        expires_at=time.time() + 660,
        confidence_score=0.99,
    )
    session = await manager.create_session(
        player_id="player-nj-002",
        operator_id="betmgm",
        product_type="CASINO",
        initial_geo_lease=lease,
    )
    print(f"Session started in NJ: {session.session_id}")

    # Simulate location change to Texas (unlicensed state)
    await asyncio.sleep(0.5)
    new_lease_tx = GeoLease(
        lease_id=str(uuid.uuid4()),
        player_id="player-nj-002",
        state_code="TX",
        expires_at=time.time() + 900,
        confidence_score=0.94,
    )
    await manager._handle_state_crossing(session, "NJ", "TX", new_lease_tx)

    active = manager.get_session(session.session_id)
    if active is None:
        print("Session correctly terminated: player in unlicensed state TX")
    else:
        print(f"Session still active (unexpected): state={active.state.value}")


async def demo_scenario_border_zone(manager: InterstateSessionManager) -> None:
    """
    Scenario: Player near NY/NJ border — GeoComply returns BORDER_ZONE.
    Expected outcome: Session suspended pending secondary verification; terminated after max retries.
    """
    print("\n--- Scenario 3: Player near NY/NJ border ---")
    lease = GeoLease(
        lease_id=str(uuid.uuid4()),
        player_id="player-border-001",
        state_code="NJ",
        expires_at=time.time() + 660,
        confidence_score=0.72,  # Low confidence — border zone
    )
    session = await manager.create_session(
        player_id="player-border-001",
        operator_id="draftkings",
        product_type="CASINO",
        initial_geo_lease=lease,
    )
    print(f"Session started: {session.session_id} (confidence=72% — border zone)")
    print(f"Session state: {session.state.value}")

    # In production, low confidence triggers immediate secondary verification
    # via GeoComply's enhanced GPS + WiFi scan
    if lease.confidence_score < 0.80:
        print("Low confidence detected — triggering secondary geo-verification")
        await manager._handle_geo_decline(session, "NJ")

    active = manager.get_session(session.session_id)
    if active:
        print(f"Session state after border check: {active.state.value}")
    else:
        print("Session terminated after failed border zone verification")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_demo() -> None:
    """Run all scenario demonstrations."""
    print("=" * 60)
    print("  Interstate Session Manager — Scenario Demonstrations")
    print("=" * 60)

    def state_crossing_handler(session: GameSession, from_state: str, to_state: Optional[str]) -> None:
        """Notify game server of state crossing so it can suspend the current game round."""
        print(f"[GAME SERVER] State crossing: {from_state} -> {to_state} (session={session.session_id})")
        print(f"[GAME SERVER] Suspending current game round...")

    manager = InterstateSessionManager(
        state_crossing_callback=state_crossing_handler,
    )

    await demo_scenario_nj_to_pa(manager)
    await demo_scenario_nj_to_texas(manager)
    await demo_scenario_border_zone(manager)

    remaining = manager.list_active_sessions()
    print(f"\nActive sessions remaining: {len(remaining)}")
    for s in remaining:
        await manager.terminate_session(s.session_id, reason="DEMO_COMPLETE")

    print("\nAll sessions closed.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Interstate session manager")
    parser.add_argument("--demo", action="store_true", help="Run scenario demonstrations")
    args = parser.parse_args()

    if args.demo:
        asyncio.run(run_demo())
    else:
        print("Run with --demo to see scenario demonstrations.")
        print("In production, import InterstateSessionManager and call create_session().")
