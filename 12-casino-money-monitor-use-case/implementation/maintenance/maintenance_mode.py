#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Casino Money Monitor - Automated Maintenance Mode
====================================================
Chapter 5 Implementation: Checklist Item #5

Implements automated maintenance mode with:
- Graceful player session handling (finish active games, block new bets)
- Multi-channel player communication (on-site banner, email, SMS, push)
- Partial maintenance (deposit-only, withdrawal-only, full)
- Scheduled maintenance windows with countdown
- Automatic activation on emergency financial alerts
- Regulatory compliance: player notification requirements per jurisdiction

Jurisdiction Requirements:
- UKGC: 24h notice for planned maintenance; immediate for emergencies
- MGA: "reasonable notice" to players; maintain player fund access
- Sweden (SGA): Must notify Spelinspektionen before planned downtime
- Denmark (DGA): Real-time status page required

PCI DSS Compliance Notes:
- Requirement 6.4: Change management for maintenance windows
- Requirement 12.10: Incident response for emergency maintenance
- Player notification data does NOT include card details (Req 3.4)

Dependencies:
    pip install pydantic redis jinja2 httpx
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Callable, Awaitable
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger("maintenance_mode")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class MaintenanceScope(str, Enum):
    """What operations are blocked during maintenance."""
    FULL = "full"                        # everything offline
    DEPOSITS_ONLY = "deposits_only"      # block deposits, allow withdrawals
    WITHDRAWALS_ONLY = "withdrawals_only"  # block withdrawals, allow deposits
    BETTING_ONLY = "betting_only"        # block new bets, allow cashier
    CASINO_ONLY = "casino_only"          # casino games offline, sports ok
    SPORTS_ONLY = "sports_only"          # sports offline, casino ok
    CASHIER_ONLY = "cashier_only"        # cashier offline, games playable


class MaintenanceReason(str, Enum):
    SCHEDULED = "scheduled"
    EMERGENCY_FINANCIAL = "emergency_financial"
    EMERGENCY_TECHNICAL = "emergency_technical"
    REGULATORY_ORDER = "regulatory_order"
    SECURITY_INCIDENT = "security_incident"
    PAYMENT_PROVIDER_OUTAGE = "payment_provider_outage"
    BANK_MAINTENANCE = "bank_maintenance"


class MaintenanceStatus(str, Enum):
    SCHEDULED = "scheduled"
    COUNTDOWN = "countdown"              # < 30 min to start
    DRAINING = "draining"                # gracefully closing sessions
    ACTIVE = "active"                    # maintenance in progress
    COMPLETING = "completing"            # bringing services back
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Jurisdiction(str, Enum):
    UK = "uk"
    MALTA = "malta"
    CURACAO = "curacao"
    SWEDEN = "sweden"
    DENMARK = "denmark"
    GIBRALTAR = "gibraltar"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class MaintenanceWindow(BaseModel):
    """Represents a maintenance event."""
    maintenance_id: str = Field(default_factory=lambda: str(uuid4()))
    scope: MaintenanceScope
    reason: MaintenanceReason
    status: MaintenanceStatus = MaintenanceStatus.SCHEDULED

    # Timing
    scheduled_start: datetime
    scheduled_end: datetime
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    drain_duration_minutes: int = 10      # time to gracefully close sessions

    # Details
    title: str = "Scheduled Maintenance"
    internal_description: str = ""
    player_message: dict[str, str] = {}   # locale -> message
    affected_jurisdictions: list[Jurisdiction] = []

    # Tracking
    created_by: str = "system"
    approved_by: Optional[str] = None
    players_notified: int = 0
    sessions_drained: int = 0
    bets_settled_early: int = 0

    # Automation
    auto_triggered: bool = False          # triggered by alert system
    trigger_alert_id: Optional[str] = None


class PlayerNotification(BaseModel):
    """Notification sent to a player about maintenance."""
    notification_id: str = Field(default_factory=lambda: str(uuid4()))
    maintenance_id: str
    player_id: str
    channel: str               # banner, email, sms, push, popup
    locale: str = "en"
    message: str
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    delivered: bool = False


class ActiveSession(BaseModel):
    """Represents an active player session that needs draining."""
    session_id: str
    player_id: str
    game_type: str             # slot, table, live_dealer, sports
    game_id: str
    started_at: datetime
    current_balance: str
    has_active_round: bool     # in middle of a game round
    jurisdiction: Jurisdiction


# ---------------------------------------------------------------------------
# Player Message Templates
# ---------------------------------------------------------------------------

MAINTENANCE_MESSAGES = {
    "en": {
        MaintenanceReason.SCHEDULED: {
            "banner": "Scheduled maintenance from {start} to {end} ({timezone}). {scope_desc}. Your funds are safe.",
            "email_subject": "Scheduled Maintenance Notice - {casino_name}",
            "email_body": """Dear {player_name},

We will be performing scheduled maintenance on {date} from {start} to {end} ({timezone}).

During this time, {scope_desc_long}.

Your account balance and funds are completely safe and will be available once maintenance is complete.

{compensation_note}

We apologize for any inconvenience.

Best regards,
{casino_name} Team""",
            "sms": "{casino_name}: Maintenance {date} {start}-{end}. {scope_desc}. Funds are safe.",
            "push": "Maintenance scheduled: {start}-{end}. {scope_desc}.",
        },
        MaintenanceReason.EMERGENCY_FINANCIAL: {
            "banner": "We are currently performing emergency maintenance. Your funds are safe and secure. We will be back shortly.",
            "popup": "Emergency maintenance in progress. All active game rounds will be completed and credited. Your balance is safe.",
            "email_subject": "Service Interruption Notice - {casino_name}",
            "email_body": """Dear {player_name},

We are currently experiencing a temporary service interruption and our team is working to resolve it as quickly as possible.

Please be assured that:
- Your account balance is safe and secure
- Any active game rounds have been completed and credited
- Pending withdrawals will be processed once service resumes

We expect to be back online within {eta}. We apologize for the inconvenience.

Best regards,
{casino_name} Team""",
        },
    },
    "sv": {
        MaintenanceReason.SCHEDULED: {
            "banner": "Planerat underhall fran {start} till {end} ({timezone}). {scope_desc}. Dina medel ar sakra.",
        },
    },
    "pt-BR": {
        MaintenanceReason.SCHEDULED: {
            "banner": "Manutencao programada de {start} a {end} ({timezone}). {scope_desc}. Seus fundos estao seguros.",
        },
    },
}

SCOPE_DESCRIPTIONS = {
    MaintenanceScope.FULL: {
        "short": "All services will be temporarily unavailable",
        "long": "all gaming and cashier services will be temporarily unavailable",
    },
    MaintenanceScope.DEPOSITS_ONLY: {
        "short": "Deposits temporarily unavailable; withdrawals OK",
        "long": "deposit services will be temporarily unavailable. You can still place bets and request withdrawals",
    },
    MaintenanceScope.WITHDRAWALS_ONLY: {
        "short": "Withdrawals temporarily paused; deposits and gaming OK",
        "long": "withdrawal processing will be temporarily paused. You can still deposit and play. Withdrawal requests will be queued",
    },
    MaintenanceScope.BETTING_ONLY: {
        "short": "New bets paused; cashier services available",
        "long": "new bet placement will be temporarily paused. Cashier services (deposits/withdrawals) remain available",
    },
    MaintenanceScope.CASINO_ONLY: {
        "short": "Casino games unavailable; sportsbook open",
        "long": "casino games will be temporarily unavailable. Sportsbook and cashier services remain operational",
    },
    MaintenanceScope.SPORTS_ONLY: {
        "short": "Sportsbook unavailable; casino open",
        "long": "sportsbook will be temporarily unavailable. Casino games and cashier services remain operational",
    },
    MaintenanceScope.CASHIER_ONLY: {
        "short": "Cashier unavailable; games playable",
        "long": "deposit and withdrawal services will be temporarily unavailable. You can continue playing with your existing balance",
    },
}


# ---------------------------------------------------------------------------
# Session Drainer
# ---------------------------------------------------------------------------

class SessionDrainer:
    """
    Gracefully drains active player sessions before maintenance.

    Process:
    1. Block new logins and new game starts
    2. Let current game rounds complete (slots: current spin, tables: current hand)
    3. Cash out players from tournament seating
    4. Save all game states
    5. Notify players their session is ending
    """

    async def drain_sessions(
        self,
        sessions: list[ActiveSession],
        timeout_minutes: int = 10,
    ) -> dict:
        """
        Drain active sessions with a timeout.
        Returns summary of drained sessions.
        """
        summary = {
            "total_sessions": len(sessions),
            "completed_normally": 0,
            "force_saved": 0,
            "rounds_completed": 0,
            "errors": 0,
        }

        logger.info(f"Draining {len(sessions)} active sessions (timeout: {timeout_minutes}min)")

        for session in sessions:
            try:
                if session.has_active_round:
                    # Wait for current round to complete
                    # In production: check game server via internal API
                    logger.info(f"Waiting for round to complete: {session.session_id} ({session.game_type})")
                    summary["rounds_completed"] += 1

                # Save game state
                # In production: call game server save endpoint
                logger.info(f"Session saved: {session.session_id}")
                summary["completed_normally"] += 1

            except Exception as e:
                logger.error(f"Error draining session {session.session_id}: {e}")
                # Force-save session state
                summary["force_saved"] += 1
                summary["errors"] += 1

        return summary


# ---------------------------------------------------------------------------
# Maintenance Mode Controller
# ---------------------------------------------------------------------------

class MaintenanceModeController:
    """
    Controls the full lifecycle of a maintenance window.

    Lifecycle:
    1. SCHEDULED -> Window created and notifications queued
    2. COUNTDOWN -> 30 min before start, show banners
    3. DRAINING -> Block new sessions, drain existing ones
    4. ACTIVE -> Maintenance in progress
    5. COMPLETING -> Bringing services back, health checks
    6. COMPLETED -> All clear

    For emergency maintenance (auto-triggered by alert system):
    -> DRAINING immediately (skip SCHEDULED/COUNTDOWN)
    """

    def __init__(self):
        self._current_maintenance: Optional[MaintenanceWindow] = None
        self._maintenance_history: list[MaintenanceWindow] = []
        self._drainer = SessionDrainer()

        # Service control callbacks - in production, these call service mesh / feature flags
        self._service_controls: dict[str, Callable[[bool], Awaitable[None]]] = {}

    def register_service_control(self, service_name: str, toggle_fn: Callable[[bool], Awaitable[None]]):
        """Register a callback to enable/disable a service."""
        self._service_controls[service_name] = toggle_fn

    # ---- Schedule Maintenance ----

    async def schedule_maintenance(
        self,
        scope: MaintenanceScope,
        reason: MaintenanceReason,
        start: datetime,
        end: datetime,
        title: str = "Scheduled Maintenance",
        description: str = "",
        jurisdictions: Optional[list[Jurisdiction]] = None,
        created_by: str = "system",
    ) -> MaintenanceWindow:
        """Schedule a new maintenance window."""
        if self._current_maintenance and self._current_maintenance.status in (
            MaintenanceStatus.ACTIVE, MaintenanceStatus.DRAINING
        ):
            raise RuntimeError("Cannot schedule while another maintenance is active")

        window = MaintenanceWindow(
            scope=scope,
            reason=reason,
            scheduled_start=start,
            scheduled_end=end,
            title=title,
            internal_description=description,
            affected_jurisdictions=jurisdictions or list(Jurisdiction),
            created_by=created_by,
            player_message=self._build_player_messages(reason, scope, start, end),  # ty:ignore[invalid-argument-type]
        )

        self._current_maintenance = window
        logger.info(f"Maintenance scheduled: {window.maintenance_id} ({scope.value}) {start} -> {end}")

        return window

    async def trigger_emergency_maintenance(
        self,
        scope: MaintenanceScope = MaintenanceScope.FULL,
        reason: MaintenanceReason = MaintenanceReason.EMERGENCY_FINANCIAL,
        duration_minutes: int = 60,
        alert_id: Optional[str] = None,
    ) -> MaintenanceWindow:
        """
        Immediately trigger emergency maintenance.
        Called by the alert system when emergency thresholds are breached.
        """
        now = datetime.now(timezone.utc)

        window = MaintenanceWindow(
            scope=scope,
            reason=reason,
            scheduled_start=now,
            scheduled_end=now + timedelta(minutes=duration_minutes),
            title="Emergency Maintenance",
            internal_description=f"Auto-triggered by alert {alert_id}",
            affected_jurisdictions=list(Jurisdiction),
            auto_triggered=True,
            trigger_alert_id=alert_id,
            drain_duration_minutes=5,  # shorter drain for emergencies
        )

        self._current_maintenance = window
        logger.critical(f"EMERGENCY maintenance triggered: {window.maintenance_id}")

        # Go directly to draining
        await self._start_draining(window)
        return window

    # ---- Lifecycle Management ----

    async def _start_draining(self, window: MaintenanceWindow):
        """Begin draining active sessions."""
        window.status = MaintenanceStatus.DRAINING
        window.actual_start = datetime.now(timezone.utc)

        # Show maintenance banners to all players
        await self._broadcast_banner(window)

        # Block new logins/bets based on scope
        await self._apply_service_blocks(window.scope, enabled=False)

        # Drain active sessions
        demo_sessions = [
            ActiveSession(session_id="S001", player_id="P-1001", game_type="slot",
                          game_id="starburst", started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
                          current_balance="145.50", has_active_round=True,
                          jurisdiction=Jurisdiction.UK),
            ActiveSession(session_id="S002", player_id="P-2001", game_type="live_dealer",
                          game_id="blackjack-vip", started_at=datetime.now(timezone.utc) - timedelta(minutes=15),
                          current_balance="2340.00", has_active_round=True,
                          jurisdiction=Jurisdiction.MALTA),
            ActiveSession(session_id="S003", player_id="P-1050", game_type="sports",
                          game_id="prematch", started_at=datetime.now(timezone.utc) - timedelta(hours=1),
                          current_balance="500.00", has_active_round=False,
                          jurisdiction=Jurisdiction.UK),
        ]

        drain_result = await self._drainer.drain_sessions(
            demo_sessions, timeout_minutes=window.drain_duration_minutes
        )
        window.sessions_drained = drain_result["total_sessions"]

        # Transition to active maintenance
        window.status = MaintenanceStatus.ACTIVE
        logger.info(f"Maintenance ACTIVE: {window.maintenance_id} (drained {window.sessions_drained} sessions)")

    async def complete_maintenance(self):
        """End the current maintenance window and restore services."""
        if not self._current_maintenance:
            raise RuntimeError("No active maintenance to complete")

        window = self._current_maintenance
        window.status = MaintenanceStatus.COMPLETING

        # Restore services
        await self._apply_service_blocks(window.scope, enabled=True)

        # Health check (in production: verify all services are responding)
        logger.info("Running post-maintenance health checks...")

        window.status = MaintenanceStatus.COMPLETED
        window.actual_end = datetime.now(timezone.utc)

        # Remove banners
        await self._remove_banners()

        # Send "we're back" notification
        await self._send_restoration_notice(window)

        self._maintenance_history.append(window)
        self._current_maintenance = None

        duration = (window.actual_end - (window.actual_start or window.scheduled_start)).total_seconds() / 60
        logger.info(f"Maintenance COMPLETED: {window.maintenance_id} (duration: {duration:.0f} min)")

    async def cancel_maintenance(self, reason: str = ""):
        """Cancel a scheduled (not yet active) maintenance."""
        if not self._current_maintenance:
            return
        if self._current_maintenance.status in (MaintenanceStatus.ACTIVE, MaintenanceStatus.DRAINING):
            raise RuntimeError("Cannot cancel active maintenance; use complete_maintenance()")

        self._current_maintenance.status = MaintenanceStatus.CANCELLED
        self._maintenance_history.append(self._current_maintenance)
        self._current_maintenance = None
        logger.info(f"Maintenance cancelled: {reason}")

    # ---- Internal Helpers ----

    async def _apply_service_blocks(self, scope: MaintenanceScope, enabled: bool):
        """Enable or disable services based on maintenance scope."""
        action = "Enabling" if enabled else "Disabling"
        scope_services = {
            MaintenanceScope.FULL: ["deposits", "withdrawals", "sports", "casino", "live_casino", "poker"],
            MaintenanceScope.DEPOSITS_ONLY: ["deposits"],
            MaintenanceScope.WITHDRAWALS_ONLY: ["withdrawals"],
            MaintenanceScope.BETTING_ONLY: ["sports", "casino", "live_casino"],
            MaintenanceScope.CASINO_ONLY: ["casino", "live_casino"],
            MaintenanceScope.SPORTS_ONLY: ["sports"],
            MaintenanceScope.CASHIER_ONLY: ["deposits", "withdrawals"],
        }

        services = scope_services.get(scope, [])
        for svc in services:
            logger.info(f"{action} service: {svc}")
            if svc in self._service_controls:
                await self._service_controls[svc](enabled)

    async def _broadcast_banner(self, window: MaintenanceWindow):
        """Show maintenance banner to all connected players."""
        message = window.player_message.get("en", {}).get("banner", "Maintenance in progress")  # ty:ignore[possibly-missing-attribute]
        logger.info(f"Broadcasting banner: {message[:80]}...")
        # In production: push via WebSocket to all connected clients

    async def _remove_banners(self):
        """Remove maintenance banners."""
        logger.info("Removing maintenance banners")

    async def _send_restoration_notice(self, window: MaintenanceWindow):
        """Notify players that services are restored."""
        logger.info("Sending service restoration notifications")

    def _build_player_messages(
        self,
        reason: MaintenanceReason,
        scope: MaintenanceScope,
        start: datetime,
        end: datetime,
    ) -> dict[str, dict]:
        """Build localized player messages."""
        scope_info = SCOPE_DESCRIPTIONS.get(scope, {"short": "Service maintenance", "long": "services will be unavailable"})
        messages = {}

        for locale, templates in MAINTENANCE_MESSAGES.items():
            reason_templates = templates.get(reason, templates.get(MaintenanceReason.SCHEDULED, {}))
            locale_msgs = {}
            for channel, template in reason_templates.items():
                locale_msgs[channel] = template.format(
                    start=start.strftime("%H:%M"),
                    end=end.strftime("%H:%M"),
                    date=start.strftime("%Y-%m-%d"),
                    timezone="UTC",
                    scope_desc=scope_info["short"],
                    scope_desc_long=scope_info.get("long", scope_info["short"]),
                    casino_name="AcmetoCasino",
                    player_name="{player_name}",  # filled per-player
                    eta="1 hour",
                    compensation_note="",
                )
            messages[locale] = locale_msgs

        return messages

    @property
    def is_active(self) -> bool:
        return self._current_maintenance is not None and self._current_maintenance.status in (
            MaintenanceStatus.ACTIVE, MaintenanceStatus.DRAINING
        )

    @property
    def current(self) -> Optional[MaintenanceWindow]:
        return self._current_maintenance


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

async def main():
    controller = MaintenanceModeController()

    # Scenario 1: Scheduled maintenance
    print("=== Scenario 1: Scheduled Maintenance ===")
    now = datetime.now(timezone.utc)
    window = await controller.schedule_maintenance(
        scope=MaintenanceScope.CASHIER_ONLY,
        reason=MaintenanceReason.BANK_MAINTENANCE,
        start=now + timedelta(hours=6),
        end=now + timedelta(hours=8),
        title="Bank Settlement System Upgrade",
        description="Barclays upgrading their clearing system",
        jurisdictions=[Jurisdiction.UK],
        created_by="treasury-ops",
    )
    print(f"Maintenance ID: {window.maintenance_id}")
    print(f"Status: {window.status.value}")
    print(f"Scope: {window.scope.value}")
    print(f"Player message (EN): {window.player_message.get('en', {}).get('banner', 'N/A')}")  # ty:ignore[possibly-missing-attribute]

    # Scenario 2: Emergency maintenance (triggered by alert system)
    print("\n=== Scenario 2: Emergency Financial Maintenance ===")
    emergency = await controller.trigger_emergency_maintenance(
        scope=MaintenanceScope.FULL,
        reason=MaintenanceReason.EMERGENCY_FINANCIAL,
        duration_minutes=60,
        alert_id="alert-LCR-001",
    )
    print(f"Emergency ID: {emergency.maintenance_id}")
    print(f"Status: {emergency.status.value}")
    print(f"Sessions drained: {emergency.sessions_drained}")
    print(f"Is active: {controller.is_active}")

    # Complete maintenance
    await controller.complete_maintenance()
    print(f"\nMaintenance completed. Is active: {controller.is_active}")


if __name__ == "__main__":
    asyncio.run(main())
