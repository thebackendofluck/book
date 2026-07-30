# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Proactive Maintenance Management for iGaming Platforms

This module provides maintenance window scheduling, conflict detection,
and communication management for planned maintenance activities.

Features:
- Maintenance window scheduling with conflict detection
- Business event calendar integration
- Automated stakeholder notifications
- Pre/post maintenance checklists
- Rollback plan management
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Any, Protocol
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum


class MaintenanceType(Enum):
    """Types of maintenance activities"""
    PREVENTIVE = "preventive"    # Scheduled routine maintenance
    CORRECTIVE = "corrective"    # Fix known issues
    PREDICTIVE = "predictive"    # Based on monitoring predictions
    EMERGENCY = "emergency"      # Urgent unplanned maintenance


class MaintenanceStatus(Enum):
    """Maintenance window status"""
    SCHEDULED = "scheduled"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RedisClientProtocol(Protocol):
    """Protocol for Redis client operations"""
    async def get(self, key: str) -> Optional[bytes]: ...
    async def set(self, key: str, value: str) -> None: ...
    async def lpush(self, key: str, value: str) -> None: ...
    async def lrange(self, key: str, start: int, end: int) -> List[bytes]: ...


@dataclass
class MaintenanceWindow:
    """Maintenance window data model"""
    id: str
    title: str
    description: str
    maintenance_type: MaintenanceType
    affected_services: List[str]
    scheduled_start: datetime
    scheduled_end: datetime
    estimated_duration_hours: float
    status: MaintenanceStatus
    requested_by: str
    approved_by: Optional[str] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    rollback_plan: str = ""
    communication_plan: str = ""
    risk_assessment: Dict[str, Any] = field(default_factory=dict)
    pre_checks: List[str] = field(default_factory=list)
    post_checks: List[str] = field(default_factory=list)
    incidents_during: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "maintenance_type": self.maintenance_type.value,
            "affected_services": self.affected_services,
            "scheduled_start": self.scheduled_start.isoformat(),
            "scheduled_end": self.scheduled_end.isoformat(),
            "estimated_duration_hours": self.estimated_duration_hours,
            "status": self.status.value,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "actual_start": self.actual_start.isoformat() if self.actual_start else None,
            "actual_end": self.actual_end.isoformat() if self.actual_end else None,
            "rollback_plan": self.rollback_plan,
            "communication_plan": self.communication_plan,
            "risk_assessment": self.risk_assessment,
            "pre_checks": self.pre_checks,
            "post_checks": self.post_checks,
            "incidents_during": self.incidents_during,
        }


class MaintenanceManagementSystem:
    """
    Proactive maintenance management for iGaming platforms.

    Handles scheduling, conflict detection, approvals, and
    automated notifications for planned maintenance activities.
    """

    def __init__(self, redis_client: RedisClientProtocol, config: Dict[str, Any]):
        self.redis = redis_client
        self.config = config
        self.logger = logging.getLogger(__name__)

    async def schedule_maintenance(
        self, maintenance_request: Dict[str, Any]
    ) -> Optional[MaintenanceWindow]:
        """
        Schedule a maintenance window.

        Validates the request, checks for conflicts with other
        maintenance windows and business events, then creates
        the maintenance window.
        """
        try:
            # Validate request
            validation = await self._validate_maintenance_request(maintenance_request)
            if not validation["valid"]:
                self.logger.error(f"Invalid maintenance request: {validation['errors']}")
                return None

            # Check for conflicts
            conflicts = await self._check_schedule_conflicts(maintenance_request)
            if conflicts:
                self.logger.error(f"Schedule conflicts detected: {conflicts}")
                return None

            # Create maintenance window
            maintenance = MaintenanceWindow(
                id=f"mnt_{int(datetime.now().timestamp() * 1000)}",
                title=maintenance_request["title"],
                description=maintenance_request["description"],
                maintenance_type=MaintenanceType(maintenance_request["type"]),
                affected_services=maintenance_request["affected_services"],
                scheduled_start=datetime.fromisoformat(maintenance_request["start_time"]),
                scheduled_end=datetime.fromisoformat(maintenance_request["end_time"]),
                estimated_duration_hours=maintenance_request["estimated_duration"],
                status=MaintenanceStatus.SCHEDULED,
                requested_by=maintenance_request["requested_by"],
                rollback_plan=maintenance_request.get("rollback_plan", ""),
                communication_plan=maintenance_request.get("communication_plan", ""),
                risk_assessment=maintenance_request.get("risk_assessment", {}),
                pre_checks=maintenance_request.get("pre_checks", []),
                post_checks=maintenance_request.get("post_checks", []),
            )

            # Store maintenance window
            await self._store_maintenance_window(maintenance)

            # Schedule notifications
            await self._schedule_notifications(maintenance)

            self.logger.info(f"Maintenance scheduled: {maintenance.id} - {maintenance.title}")
            return maintenance

        except Exception as e:
            self.logger.error(f"Failed to schedule maintenance: {e}")
            return None

    async def _validate_maintenance_request(
        self, request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate maintenance request"""
        errors = []

        required_fields = [
            "title", "description", "type", "affected_services",
            "start_time", "end_time", "estimated_duration", "requested_by",
        ]

        for field_name in required_fields:
            if field_name not in request:
                errors.append(f"Missing required field: {field_name}")

        # Validate time format and logic
        try:
            start_time = datetime.fromisoformat(request["start_time"])
            end_time = datetime.fromisoformat(request["end_time"])

            if start_time >= end_time:
                errors.append("End time must be after start time")

            if start_time < datetime.now():
                errors.append("Start time cannot be in the past")

        except (ValueError, KeyError):
            errors.append("Invalid datetime format")

        # Validate maintenance type
        valid_types = [t.value for t in MaintenanceType]
        if request.get("type") not in valid_types:
            errors.append(f"Invalid maintenance type. Must be one of: {valid_types}")

        # Business hours check for critical services
        affected_services = request.get("affected_services", [])
        critical_services = ["payment", "database", "authentication", "gaming_logic"]

        if any(service in affected_services for service in critical_services):
            try:
                start_time = datetime.fromisoformat(request["start_time"])
                # Check if during business hours (weekday 8am-10pm)
                if start_time.weekday() < 5 and 8 <= start_time.hour < 22:
                    if not request.get("business_justification"):
                        errors.append(
                            "Business justification required for critical service "
                            "maintenance during business hours"
                        )
            except (ValueError, KeyError):
                pass

        return {"valid": len(errors) == 0, "errors": errors}

    async def _check_schedule_conflicts(
        self, request: Dict[str, Any]
    ) -> List[str]:
        """Check for scheduling conflicts"""
        conflicts = []

        start_time = datetime.fromisoformat(request["start_time"])
        end_time = datetime.fromisoformat(request["end_time"])
        affected_services = request["affected_services"]

        # Check existing maintenance windows
        existing_maintenance = await self._get_overlapping_maintenance(start_time, end_time)
        for maintenance in existing_maintenance:
            overlapping_services = set(affected_services) & set(
                maintenance.get("affected_services", [])
            )
            if overlapping_services:
                conflicts.append(
                    f"Conflict with maintenance {maintenance['id']} "
                    f"on services: {', '.join(overlapping_services)}"
                )

        # Check business events (tournaments, promotions, etc.)
        business_events = await self._get_business_events(start_time, end_time)
        for event in business_events:
            event_services = event.get("critical_services", [])
            if set(affected_services) & set(event_services):
                conflicts.append(
                    f"Conflict with business event: {event['name']} ({event['impact']})"
                )

        return conflicts

    async def approve_maintenance(self, maintenance_id: str, approver: str) -> bool:
        """Approve a maintenance window"""
        try:
            maintenance = await self.get_maintenance_window(maintenance_id)
            if not maintenance:
                return False

            maintenance.status = MaintenanceStatus.APPROVED
            maintenance.approved_by = approver

            await self._store_maintenance_window(maintenance)
            await self._send_approval_notifications(maintenance)
            await self._schedule_reminders(maintenance)

            return True
        except Exception as e:
            self.logger.error(f"Failed to approve maintenance: {e}")
            return False

    async def start_maintenance(self, maintenance_id: str) -> bool:
        """Start a maintenance window"""
        try:
            maintenance = await self.get_maintenance_window(maintenance_id)
            if not maintenance or maintenance.status != MaintenanceStatus.APPROVED:
                return False

            # Run pre-checks
            pre_check_results = await self._run_pre_checks(maintenance)
            if not all(pre_check_results.values()):
                self.logger.error(f"Pre-checks failed: {pre_check_results}")
                return False

            maintenance.status = MaintenanceStatus.IN_PROGRESS
            maintenance.actual_start = datetime.now()

            await self._store_maintenance_window(maintenance)
            await self._send_start_notifications(maintenance)

            return True
        except Exception as e:
            self.logger.error(f"Failed to start maintenance: {e}")
            return False

    async def complete_maintenance(
        self, maintenance_id: str, success: bool = True
    ) -> bool:
        """Complete a maintenance window"""
        try:
            maintenance = await self.get_maintenance_window(maintenance_id)
            if not maintenance:
                return False

            # Run post-checks
            post_check_results = await self._run_post_checks(maintenance)

            maintenance.status = (
                MaintenanceStatus.COMPLETED if success else MaintenanceStatus.FAILED
            )
            maintenance.actual_end = datetime.now()

            await self._store_maintenance_window(maintenance)
            await self._send_completion_notifications(maintenance)

            return True
        except Exception as e:
            self.logger.error(f"Failed to complete maintenance: {e}")
            return False

    async def get_maintenance_window(
        self, maintenance_id: str
    ) -> Optional[MaintenanceWindow]:
        """Retrieve maintenance window by ID"""
        try:
            data = await self.redis.get(f"maintenance:{maintenance_id}")
            if data:
                maint_dict = json.loads(data)
                return MaintenanceWindow(
                    id=maint_dict["id"],
                    title=maint_dict["title"],
                    description=maint_dict["description"],
                    maintenance_type=MaintenanceType(maint_dict["maintenance_type"]),
                    affected_services=maint_dict["affected_services"],
                    scheduled_start=datetime.fromisoformat(maint_dict["scheduled_start"]),
                    scheduled_end=datetime.fromisoformat(maint_dict["scheduled_end"]),
                    estimated_duration_hours=maint_dict["estimated_duration_hours"],
                    status=MaintenanceStatus(maint_dict["status"]),
                    requested_by=maint_dict["requested_by"],
                    approved_by=maint_dict.get("approved_by"),
                )
            return None
        except Exception as e:
            self.logger.error(f"Failed to get maintenance window: {e}")
            return None

    async def _store_maintenance_window(self, maintenance: MaintenanceWindow) -> None:
        """Store maintenance window"""
        await self.redis.set(
            f"maintenance:{maintenance.id}",
            json.dumps(maintenance.to_dict()),
        )

    async def _get_overlapping_maintenance(
        self, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        """Get maintenance windows overlapping with time range"""
        # Implementation would query Redis for overlapping windows
        return []

    async def _get_business_events(
        self, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        """Get business events in time range (tournaments, promotions, etc.)"""
        # Implementation would query business event calendar
        return []

    async def _schedule_notifications(self, maintenance: MaintenanceWindow) -> None:
        """Schedule automated notifications"""
        self.logger.info(f"Notifications scheduled for {maintenance.id}")

    async def _send_approval_notifications(self, maintenance: MaintenanceWindow) -> None:
        """Send approval notifications"""
        self.logger.info(f"Approval notification sent for {maintenance.id}")

    async def _schedule_reminders(self, maintenance: MaintenanceWindow) -> None:
        """Schedule reminder notifications"""
        self.logger.info(f"Reminders scheduled for {maintenance.id}")

    async def _run_pre_checks(
        self, maintenance: MaintenanceWindow
    ) -> Dict[str, bool]:
        """Run pre-maintenance checks"""
        results = {}
        for check in maintenance.pre_checks:
            results[check] = True  # Implementation would run actual checks
        return results

    async def _run_post_checks(
        self, maintenance: MaintenanceWindow
    ) -> Dict[str, bool]:
        """Run post-maintenance checks"""
        results = {}
        for check in maintenance.post_checks:
            results[check] = True  # Implementation would run actual checks
        return results

    async def _send_start_notifications(self, maintenance: MaintenanceWindow) -> None:
        """Send maintenance start notifications"""
        self.logger.info(f"Start notification sent for {maintenance.id}")

    async def _send_completion_notifications(
        self, maintenance: MaintenanceWindow
    ) -> None:
        """Send maintenance completion notifications"""
        self.logger.info(f"Completion notification sent for {maintenance.id}")


# Standard pre-checks for iGaming maintenance
STANDARD_PRE_CHECKS = [
    "backup_completed",
    "rollback_plan_documented",
    "stakeholders_notified",
    "monitoring_enhanced",
    "support_team_available",
    "no_active_incidents",
    "no_high_traffic_events",
]

# Standard post-checks for iGaming maintenance
STANDARD_POST_CHECKS = [
    "services_responding",
    "no_error_spike",
    "database_healthy",
    "payments_processing",
    "player_logins_working",
    "game_launches_working",
    "monitoring_normal",
]
