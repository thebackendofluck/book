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
Enterprise Change Management System for iGaming Platforms

This module provides comprehensive change control for regulated
iGaming environments where changes must be documented, approved,
and reported to regulatory bodies.

Features:
- Change request workflow (draft -> review -> approve -> deploy)
- Risk-based change classification (Level 1-3)
- Regulatory notification automation
- Critical asset tracking with digital signatures
- Rollback and validation tracking
"""

import asyncio
import json
import logging
import hashlib
from typing import Dict, List, Optional, Any, Protocol
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum


class ChangeLevel(Enum):
    """Change impact levels for iGaming regulatory compliance"""
    LEVEL_1 = 1  # No Impact - cosmetic changes, documentation
    LEVEL_2 = 2  # Low Impact - non-critical systems, requires notification
    LEVEL_3 = 3  # High Impact - critical systems, requires approval + notification


class ChangeStatus(Enum):
    """Change request lifecycle status"""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    TESTING = "testing"
    DEPLOYED = "deployed"
    VALIDATED = "validated"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class ChangeType(Enum):
    """Types of changes in iGaming infrastructure"""
    SOFTWARE_UPDATE = "software_update"
    HARDWARE_CHANGE = "hardware_change"
    CONFIGURATION_CHANGE = "configuration_change"
    SECURITY_PATCH = "security_patch"
    EMERGENCY_FIX = "emergency_fix"
    INFRASTRUCTURE_CHANGE = "infrastructure_change"


class RedisClientProtocol(Protocol):
    """Protocol for Redis client operations"""
    async def get(self, key: str) -> Optional[bytes]: ...
    async def set(self, key: str, value: str) -> None: ...
    async def lpush(self, key: str, value: str) -> None: ...


@dataclass
class ChangeRequest:
    """Change request data model"""
    id: str
    title: str
    description: str
    change_type: ChangeType
    level: ChangeLevel
    status: ChangeStatus
    requested_by: str
    business_justification: str
    technical_details: Dict[str, Any]
    risk_assessment: Dict[str, Any]
    impact_analysis: Dict[str, Any]
    test_plan: Dict[str, Any]
    rollback_plan: str
    affected_components: List[str]
    affected_services: List[str]
    scheduled_deployment: Optional[datetime] = None
    actual_deployment: Optional[datetime] = None
    validation_deadline: Optional[datetime] = None
    approvers: List[str] = field(default_factory=list)
    reviewers: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    test_results: Dict[str, Any] = field(default_factory=dict)
    validation_results: Dict[str, Any] = field(default_factory=dict)
    regulatory_notifications: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "change_type": self.change_type.value,
            "level": self.level.value,
            "status": self.status.value,
            "requested_by": self.requested_by,
            "business_justification": self.business_justification,
            "technical_details": self.technical_details,
            "risk_assessment": self.risk_assessment,
            "impact_analysis": self.impact_analysis,
            "test_plan": self.test_plan,
            "rollback_plan": self.rollback_plan,
            "affected_components": self.affected_components,
            "affected_services": self.affected_services,
            "scheduled_deployment": (
                self.scheduled_deployment.isoformat() if self.scheduled_deployment else None
            ),
            "actual_deployment": (
                self.actual_deployment.isoformat() if self.actual_deployment else None
            ),
            "approvers": self.approvers,
            "reviewers": self.reviewers,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "timeline": self.timeline,
            "regulatory_notifications": self.regulatory_notifications,
        }


@dataclass
class CriticalAsset:
    """
    Critical asset tracking for regulatory compliance.

    iGaming regulators require tracking of critical system components
    with version control and integrity verification.
    """
    id: str
    name: str
    component_type: str
    version: str
    owner: str
    location: str
    relevance_scores: Dict[str, int]  # CIA-A scores (0-10)
    digital_signature: str
    last_updated: datetime
    dependencies: List[str] = field(default_factory=list)

    def calculate_criticality(self) -> int:
        """Calculate overall criticality score (0-40)"""
        return sum(self.relevance_scores.values())

    def verify_integrity(self, content: bytes) -> bool:
        """Verify asset integrity using digital signature"""
        calculated_hash = hashlib.sha256(content).hexdigest()
        return calculated_hash == self.digital_signature


class ChangeManagementSystem:
    """
    Enterprise change management for regulated iGaming environments.

    Implements change control processes required by gambling regulators
    including UKGC, MGA, and other jurisdictions.
    """

    def __init__(self, redis_client: RedisClientProtocol, config: Dict[str, Any]):
        self.redis = redis_client
        self.config = config
        self.logger = logging.getLogger(__name__)

        self.notification_channels = {
            "regulatory": self._notify_regulatory_bodies,
            "stakeholders": self._notify_stakeholders,
            "team": self._notify_team,
        }

    async def submit_change_request(
        self, change_data: Dict[str, Any]
    ) -> Optional[ChangeRequest]:
        """
        Submit a new change request.

        Validates the request, classifies the change level,
        and routes for appropriate approval workflow.
        """
        try:
            # Validate change request
            validation = await self._validate_change_request(change_data)
            if not validation["valid"]:
                self.logger.error(f"Invalid change request: {validation['errors']}")
                return None

            # Classify change level based on impact
            level = await self._classify_change_level(change_data)

            # Create change request
            change_request = ChangeRequest(
                id=f"chg_{int(datetime.now().timestamp() * 1000)}",
                title=change_data["title"],
                description=change_data["description"],
                change_type=ChangeType(change_data["change_type"]),
                level=level,
                status=ChangeStatus.SUBMITTED,
                requested_by=change_data["requested_by"],
                business_justification=change_data["business_justification"],
                technical_details=change_data["technical_details"],
                risk_assessment=change_data["risk_assessment"],
                impact_analysis=change_data["impact_analysis"],
                test_plan=change_data["test_plan"],
                rollback_plan=change_data["rollback_plan"],
                affected_components=change_data["affected_components"],
                affected_services=change_data["affected_services"],
            )

            # Store change request
            await self._store_change_request(change_request)

            # Route for approval based on level
            await self._route_for_approval(change_request)

            # Add timeline entry
            await self._add_timeline_entry(
                change_request.id,
                "change_submitted",
                {"validation_results": validation, "classified_level": level.name},
            )

            self.logger.info(
                f"Change request submitted: {change_request.id} - {change_request.title}"
            )
            return change_request

        except Exception as e:
            self.logger.error(f"Failed to submit change request: {e}")
            return None

    async def _classify_change_level(self, change_data: Dict[str, Any]) -> ChangeLevel:
        """
        Classify change based on regulatory impact.

        Level 3 (High Impact): Payment systems, gaming logic, player data
        Level 2 (Low Impact): Monitoring, logging, non-critical config
        Level 1 (No Impact): Documentation, cosmetic changes
        """
        affected_services = change_data.get("affected_services", [])
        change_type = change_data.get("change_type", "")

        # Level 3 criteria - requires regulatory notification
        level_3_services = [
            "payment",
            "gaming_logic",
            "player_data",
            "regulatory_reporting",
            "rng",  # Random Number Generator
            "authentication",
        ]
        level_3_types = ["emergency_fix", "infrastructure_change"]

        if any(s in affected_services for s in level_3_services) or change_type in level_3_types:
            return ChangeLevel.LEVEL_3

        # Level 2 criteria - notification to stakeholders
        level_2_services = ["monitoring", "logging", "backup", "network_config"]
        level_2_types = ["configuration_change", "security_patch"]

        if any(s in affected_services for s in level_2_services) or change_type in level_2_types:
            return ChangeLevel.LEVEL_2

        # Level 1 - minimal impact
        return ChangeLevel.LEVEL_1

    async def _validate_change_request(
        self, request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate change request completeness"""
        errors = []

        required_fields = [
            "title",
            "description",
            "change_type",
            "requested_by",
            "business_justification",
            "technical_details",
            "risk_assessment",
            "impact_analysis",
            "test_plan",
            "rollback_plan",
            "affected_components",
            "affected_services",
        ]

        for field_name in required_fields:
            if field_name not in request or not request[field_name]:
                errors.append(f"Missing required field: {field_name}")

        # Validate change type
        valid_types = [t.value for t in ChangeType]
        if request.get("change_type") not in valid_types:
            errors.append(f"Invalid change type. Must be one of: {valid_types}")

        # Validate risk assessment has required fields
        risk = request.get("risk_assessment", {})
        required_risk_fields = ["probability", "impact", "mitigation"]
        for field_name in required_risk_fields:
            if field_name not in risk:
                errors.append(f"Risk assessment missing: {field_name}")

        return {"valid": len(errors) == 0, "errors": errors}

    async def approve_change(
        self, change_id: str, approver: str, comments: str = ""
    ) -> bool:
        """Approve a change request"""
        try:
            change = await self.get_change_request(change_id)
            if not change or change.status != ChangeStatus.REVIEWED:
                return False

            change.status = ChangeStatus.APPROVED
            change.approvers.append(approver)
            change.updated_at = datetime.now()

            await self._store_change_request(change)

            # Schedule deployment for Level 2/3 changes
            if change.level in [ChangeLevel.LEVEL_2, ChangeLevel.LEVEL_3]:
                await self._schedule_deployment(change)

            # Send regulatory notifications for Level 2/3
            if change.level in [ChangeLevel.LEVEL_2, ChangeLevel.LEVEL_3]:
                await self._send_regulatory_notification(change)

            await self._add_timeline_entry(
                change_id,
                "change_approved",
                {"approver": approver, "comments": comments},
            )

            return True
        except Exception as e:
            self.logger.error(f"Failed to approve change: {e}")
            return False

    async def deploy_change(self, change_id: str) -> bool:
        """Deploy an approved change"""
        try:
            change = await self.get_change_request(change_id)
            if not change or change.status != ChangeStatus.APPROVED:
                return False

            change.status = ChangeStatus.DEPLOYED
            change.actual_deployment = datetime.now()
            change.validation_deadline = datetime.now() + timedelta(hours=24)

            await self._store_change_request(change)
            await self._add_timeline_entry(change_id, "change_deployed", {})

            return True
        except Exception as e:
            self.logger.error(f"Failed to deploy change: {e}")
            return False

    async def validate_change(
        self, change_id: str, validation_results: Dict[str, Any]
    ) -> bool:
        """Validate deployed change"""
        try:
            change = await self.get_change_request(change_id)
            if not change or change.status != ChangeStatus.DEPLOYED:
                return False

            change.validation_results = validation_results
            all_passed = all(v.get("passed", False) for v in validation_results.values())

            change.status = (
                ChangeStatus.VALIDATED if all_passed else ChangeStatus.ROLLED_BACK
            )
            change.updated_at = datetime.now()

            await self._store_change_request(change)

            if not all_passed:
                await self._execute_rollback(change)

            return all_passed
        except Exception as e:
            self.logger.error(f"Failed to validate change: {e}")
            return False

    async def get_change_request(self, change_id: str) -> Optional[ChangeRequest]:
        """Retrieve change request by ID"""
        try:
            data = await self.redis.get(f"change:{change_id}")
            if data:
                change_dict = json.loads(data)
                return ChangeRequest(
                    id=change_dict["id"],
                    title=change_dict["title"],
                    description=change_dict["description"],
                    change_type=ChangeType(change_dict["change_type"]),
                    level=ChangeLevel(change_dict["level"]),
                    status=ChangeStatus(change_dict["status"]),
                    requested_by=change_dict["requested_by"],
                    business_justification=change_dict["business_justification"],
                    technical_details=change_dict["technical_details"],
                    risk_assessment=change_dict["risk_assessment"],
                    impact_analysis=change_dict["impact_analysis"],
                    test_plan=change_dict["test_plan"],
                    rollback_plan=change_dict["rollback_plan"],
                    affected_components=change_dict["affected_components"],
                    affected_services=change_dict["affected_services"],
                )
            return None
        except Exception as e:
            self.logger.error(f"Failed to get change request: {e}")
            return None

    async def _store_change_request(self, change: ChangeRequest) -> None:
        """Store change request"""
        await self.redis.set(f"change:{change.id}", json.dumps(change.to_dict()))

    async def _route_for_approval(self, change: ChangeRequest) -> None:
        """Route change for appropriate approval workflow"""
        # Level 3: Requires CAB (Change Advisory Board) approval
        # Level 2: Requires team lead approval
        # Level 1: Auto-approved
        if change.level == ChangeLevel.LEVEL_1:
            change.status = ChangeStatus.APPROVED
            await self._store_change_request(change)
        else:
            await self._notify_team(change)

    async def _add_timeline_entry(
        self, change_id: str, event_type: str, data: Dict[str, Any]
    ) -> None:
        """Add timeline entry to change request"""
        entry = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }
        await self.redis.lpush(f"change:{change_id}:timeline", json.dumps(entry))

    async def _schedule_deployment(self, change: ChangeRequest) -> None:
        """Schedule change deployment"""
        self.logger.info(f"Deployment scheduled for {change.id}")

    async def _send_regulatory_notification(self, change: ChangeRequest) -> None:
        """Send notification to regulatory bodies for Level 2/3 changes"""
        notification = {
            "change_id": change.id,
            "level": change.level.value,
            "description": change.description,
            "affected_services": change.affected_services,
            "sent_at": datetime.now().isoformat(),
        }
        change.regulatory_notifications.append(notification)
        self.logger.info(f"Regulatory notification sent for {change.id}")

    async def _execute_rollback(self, change: ChangeRequest) -> None:
        """Execute rollback plan for failed change"""
        self.logger.warning(f"Executing rollback for {change.id}")
        await self._add_timeline_entry(
            change.id,
            "rollback_executed",
            {"rollback_plan": change.rollback_plan},
        )

    async def _notify_regulatory_bodies(self, change: ChangeRequest) -> None:
        """Notify regulatory bodies of change"""
        pass

    async def _notify_stakeholders(self, change: ChangeRequest) -> None:
        """Notify business stakeholders"""
        pass

    async def _notify_team(self, change: ChangeRequest) -> None:
        """Notify technical team"""
        pass


# Regulatory notification requirements by jurisdiction
REGULATORY_REQUIREMENTS = {
    "UKGC": {
        "notification_window_days": 14,
        "level_2_required": True,
        "level_3_required": True,
        "requires_approval": True,
    },
    "MGA": {
        "notification_window_days": 7,
        "level_2_required": False,
        "level_3_required": True,
        "requires_approval": True,
    },
    "Gibraltar": {
        "notification_window_days": 14,
        "level_2_required": True,
        "level_3_required": True,
        "requires_approval": True,
    },
}
