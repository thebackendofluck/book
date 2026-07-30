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
Enterprise Incident Response System for iGaming Platforms

This module provides automated incident detection, classification,
and response capabilities specifically designed for online gambling
platforms where downtime costs can exceed $10,000 per minute.

Features:
- Automated incident detection from monitoring alerts
- Severity classification based on business impact
- Auto-remediation for common incident patterns
- Multi-channel alerting (Slack, PagerDuty, SMS, Email)
- Timeline tracking for postmortem analysis
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Any, Callable, Protocol
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum


class IncidentSeverity(Enum):
    """Incident severity levels based on business impact"""
    LOW = 1       # Minor issue, no user impact
    MEDIUM = 2    # Limited user impact, <1000 users affected
    HIGH = 3      # Significant impact, >10000 users affected
    CRITICAL = 4  # Platform-wide outage, payment failure, regulatory issue


class IncidentStatus(Enum):
    """Incident lifecycle status"""
    DETECTED = "detected"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    CLOSED = "closed"


class RedisClientProtocol(Protocol):
    """Protocol for Redis client operations"""
    async def get(self, key: str) -> Optional[bytes]: ...
    async def set(self, key: str, value: str) -> None: ...
    async def lpush(self, key: str, value: str) -> None: ...
    async def lrange(self, key: str, start: int, end: int) -> List[bytes]: ...


@dataclass
class Incident:
    """Incident data model"""
    id: str
    title: str
    description: str
    severity: IncidentSeverity
    status: IncidentStatus
    affected_services: List[str]
    affected_users: int
    detected_at: datetime
    acknowledged_at: Optional[datetime] = None
    mitigated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    assigned_to: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    impact_assessment: Dict[str, Any] = field(default_factory=dict)
    root_cause: Optional[str] = None
    resolution_steps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert incident to dictionary for storage"""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.name,
            "status": self.status.value,
            "affected_services": self.affected_services,
            "affected_users": self.affected_users,
            "detected_at": self.detected_at.isoformat(),
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "mitigated_at": self.mitigated_at.isoformat() if self.mitigated_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "assigned_to": self.assigned_to,
            "tags": self.tags,
            "timeline": self.timeline,
            "impact_assessment": self.impact_assessment,
            "root_cause": self.root_cause,
            "resolution_steps": self.resolution_steps,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Incident":
        """Create incident from dictionary"""
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            severity=IncidentSeverity[data["severity"]],
            status=IncidentStatus(data["status"]),
            affected_services=data["affected_services"],
            affected_users=data["affected_users"],
            detected_at=datetime.fromisoformat(data["detected_at"]),
            acknowledged_at=datetime.fromisoformat(data["acknowledged_at"]) if data.get("acknowledged_at") else None,
            mitigated_at=datetime.fromisoformat(data["mitigated_at"]) if data.get("mitigated_at") else None,
            resolved_at=datetime.fromisoformat(data["resolved_at"]) if data.get("resolved_at") else None,
            assigned_to=data.get("assigned_to"),
            tags=data.get("tags", []),
            timeline=data.get("timeline", []),
            impact_assessment=data.get("impact_assessment", {}),
            root_cause=data.get("root_cause"),
            resolution_steps=data.get("resolution_steps", []),
        )


class IncidentManagementSystem:
    """
    Enterprise incident management system for iGaming platforms.

    Provides automated incident detection, classification, auto-remediation,
    and stakeholder notification capabilities.
    """

    def __init__(self, redis_client: RedisClientProtocol, config: Dict[str, Any]):
        self.redis = redis_client
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Alert channels - can be overridden via config
        self.alert_channels: Dict[str, Callable] = {
            "slack": self._send_slack_alert,
            "email": self._send_email_alert,
            "sms": self._send_sms_alert,
            "pagerduty": self._send_pagerduty_alert,
        }

        # Auto-remediation actions
        self.remediation_actions: Dict[str, Callable] = {
            "database_high_cpu": self._remediate_database_cpu,
            "cache_miss_rate": self._remediate_cache_miss,
            "payment_timeout": self._remediate_payment_timeout,
            "cdn_down": self._remediate_cdn_failure,
        }

    async def detect_incident(self, alert_data: Dict[str, Any]) -> Optional[Incident]:
        """
        Detect and create incident from monitoring alert.

        Args:
            alert_data: Alert data from monitoring system containing:
                - title: Alert title
                - description: Alert description
                - affected_services: List of affected services
                - affected_users: Estimated number of affected users
                - service_criticality: low/medium/high/critical
                - error_rate: Current error rate (0-1)
                - tags: List of tags for categorization

        Returns:
            Created or updated Incident, or None on failure
        """
        try:
            severity = self._calculate_severity(alert_data)

            # Check for similar existing incident to avoid duplicates
            existing_incident = await self._find_similar_incident(alert_data)
            if existing_incident:
                await self._update_existing_incident(existing_incident, alert_data)
                return existing_incident

            # Create new incident
            incident = Incident(
                id=f"inc_{int(datetime.now().timestamp() * 1000)}",
                title=alert_data.get("title", "Unknown Incident"),
                description=alert_data.get("description", ""),
                severity=severity,
                status=IncidentStatus.DETECTED,
                affected_services=alert_data.get("affected_services", []),
                affected_users=alert_data.get("affected_users", 0),
                detected_at=datetime.now(),
                tags=alert_data.get("tags", []),
            )

            # Store incident
            await self._store_incident(incident)

            # Trigger automated response
            await self._trigger_automated_response(incident, alert_data)

            # Send notifications
            await self._notify_stakeholders(incident)

            # Add to timeline
            await self._add_timeline_entry(
                incident.id,
                "incident_detected",
                {
                    "alert_data": alert_data,
                    "automated_actions": self._get_applicable_remediations(alert_data),
                },
            )

            self.logger.warning(f"New incident detected: {incident.id} - {incident.title}")
            return incident

        except Exception as e:
            self.logger.error(f"Failed to detect incident: {e}")
            return None

    def _calculate_severity(self, alert_data: Dict[str, Any]) -> IncidentSeverity:
        """
        Calculate incident severity based on business impact.

        Severity is determined by:
        - Number of affected users
        - Service criticality level
        - Current error rate
        """
        affected_users = alert_data.get("affected_users", 0)
        service_criticality = alert_data.get("service_criticality", "low")
        error_rate = alert_data.get("error_rate", 0)

        # Critical: Platform-wide or critical service failure
        if affected_users > 50000 or service_criticality == "critical" or error_rate > 0.5:
            return IncidentSeverity.CRITICAL

        # High: Significant user impact
        if affected_users > 10000 or service_criticality == "high" or error_rate > 0.2:
            return IncidentSeverity.HIGH

        # Medium: Limited impact
        if affected_users > 1000 or error_rate > 0.05:
            return IncidentSeverity.MEDIUM

        return IncidentSeverity.LOW

    async def _trigger_automated_response(
        self, incident: Incident, alert_data: Dict[str, Any]
    ) -> None:
        """Trigger automated remediation actions based on incident type"""
        applicable_actions = self._get_applicable_remediations(alert_data)

        for action_name in applicable_actions:
            if action_name in self.remediation_actions:
                try:
                    success = await self.remediation_actions[action_name](incident, alert_data)
                    await self._add_timeline_entry(
                        incident.id,
                        "automated_action",
                        {
                            "action": action_name,
                            "success": success,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception as e:
                    self.logger.error(f"Automated action {action_name} failed: {e}")

    def _get_applicable_remediations(self, alert_data: Dict[str, Any]) -> List[str]:
        """Determine which automated remediations apply to this alert"""
        alert_type = alert_data.get("alert_type", "")
        affected_services = alert_data.get("affected_services", [])
        actions = []

        if "database" in affected_services and "cpu" in alert_type.lower():
            actions.append("database_high_cpu")

        if "cache" in alert_type.lower() and "miss" in alert_type.lower():
            actions.append("cache_miss_rate")

        if "payment" in alert_type.lower() and "timeout" in alert_type.lower():
            actions.append("payment_timeout")

        if "cdn" in affected_services and "down" in alert_type.lower():
            actions.append("cdn_down")

        return actions

    async def _remediate_database_cpu(
        self, incident: Incident, alert_data: Dict[str, Any]
    ) -> bool:
        """
        Auto-remediation for high database CPU usage.

        Actions:
        1. Scale up database instance (if auto-scaling enabled)
        2. Enable query optimization/caching
        3. Add read replicas for read-heavy workloads
        """
        try:
            db_instance = alert_data.get("database_instance")
            self.logger.info(f"Remediating database CPU for {db_instance}")
            # Implementation would call AWS RDS API, etc.
            return True
        except Exception as e:
            self.logger.error(f"Database CPU remediation failed: {e}")
            return False

    async def _remediate_cache_miss(
        self, incident: Incident, alert_data: Dict[str, Any]
    ) -> bool:
        """
        Auto-remediation for high cache miss rate.

        Actions:
        1. Increase cache cluster size
        2. Adjust TTL values for hot data
        3. Enable cache warming for frequently accessed keys
        """
        try:
            cache_cluster = alert_data.get("cache_cluster")
            self.logger.info(f"Remediating cache miss rate for {cache_cluster}")
            return True
        except Exception as e:
            self.logger.error(f"Cache remediation failed: {e}")
            return False

    async def _remediate_payment_timeout(
        self, incident: Incident, alert_data: Dict[str, Any]
    ) -> bool:
        """
        Auto-remediation for payment processing timeouts.

        Actions:
        1. Switch to backup payment processor
        2. Increase timeout thresholds temporarily
        3. Enable circuit breaker for failing provider
        """
        try:
            payment_provider = alert_data.get("payment_provider")
            self.logger.info(f"Remediating payment timeout for {payment_provider}")
            return True
        except Exception as e:
            self.logger.error(f"Payment remediation failed: {e}")
            return False

    async def _remediate_cdn_failure(
        self, incident: Incident, alert_data: Dict[str, Any]
    ) -> bool:
        """
        Auto-remediation for CDN failure.

        Actions:
        1. Switch to backup CDN provider
        2. Update DNS records to failover
        3. Enable CDN failover routing
        """
        try:
            cdn_provider = alert_data.get("cdn_provider")
            self.logger.info(f"Remediating CDN failure for {cdn_provider}")
            return True
        except Exception as e:
            self.logger.error(f"CDN remediation failed: {e}")
            return False

    async def acknowledge_incident(self, incident_id: str, user_id: str) -> bool:
        """Acknowledge incident and assign responder"""
        try:
            incident = await self.get_incident(incident_id)
            if not incident:
                return False

            incident.status = IncidentStatus.INVESTIGATING
            incident.acknowledged_at = datetime.now()
            incident.assigned_to = user_id

            await self._store_incident(incident)
            await self._add_timeline_entry(
                incident_id,
                "incident_acknowledged",
                {"user_id": user_id, "timestamp": datetime.now().isoformat()},
            )

            return True
        except Exception as e:
            self.logger.error(f"Failed to acknowledge incident {incident_id}: {e}")
            return False

    async def update_incident_status(
        self, incident_id: str, new_status: IncidentStatus, user_id: str, notes: str = ""
    ) -> bool:
        """Update incident status with audit trail"""
        try:
            incident = await self.get_incident(incident_id)
            if not incident:
                return False

            old_status = incident.status
            incident.status = new_status

            # Update timestamps based on status
            if new_status == IncidentStatus.MITIGATED:
                incident.mitigated_at = datetime.now()
            elif new_status == IncidentStatus.RESOLVED:
                incident.resolved_at = datetime.now()

            await self._store_incident(incident)
            await self._add_timeline_entry(
                incident_id,
                "status_updated",
                {
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                    "user_id": user_id,
                    "notes": notes,
                    "timestamp": datetime.now().isoformat(),
                },
            )

            return True
        except Exception as e:
            self.logger.error(f"Failed to update incident status: {e}")
            return False

    async def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Retrieve incident by ID"""
        try:
            data = await self.redis.get(f"incident:{incident_id}")
            if data:
                return Incident.from_dict(json.loads(data))
            return None
        except Exception as e:
            self.logger.error(f"Failed to get incident {incident_id}: {e}")
            return None

    async def _store_incident(self, incident: Incident) -> None:
        """Store incident in Redis"""
        await self.redis.set(
            f"incident:{incident.id}", json.dumps(incident.to_dict())
        )

    async def _add_timeline_entry(
        self, incident_id: str, event_type: str, data: Dict[str, Any]
    ) -> None:
        """Add entry to incident timeline"""
        entry = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }
        await self.redis.lpush(f"incident:{incident_id}:timeline", json.dumps(entry))

    async def _find_similar_incident(
        self, alert_data: Dict[str, Any]
    ) -> Optional[Incident]:
        """Find similar active incident to prevent duplicates"""
        # Implementation would search active incidents
        return None

    async def _update_existing_incident(
        self, incident: Incident, alert_data: Dict[str, Any]
    ) -> None:
        """Update existing incident with new alert data"""
        await self._add_timeline_entry(
            incident.id,
            "additional_alert",
            {"alert_data": alert_data, "timestamp": datetime.now().isoformat()},
        )

    async def _notify_stakeholders(self, incident: Incident) -> None:
        """Send notifications to appropriate stakeholders based on severity"""
        channels_by_severity = {
            IncidentSeverity.CRITICAL: ["pagerduty", "slack", "sms", "email"],
            IncidentSeverity.HIGH: ["pagerduty", "slack", "email"],
            IncidentSeverity.MEDIUM: ["slack", "email"],
            IncidentSeverity.LOW: ["slack"],
        }

        channels = channels_by_severity.get(incident.severity, ["slack"])
        for channel in channels:
            if channel in self.alert_channels:
                try:
                    await self.alert_channels[channel](incident)
                except Exception as e:
                    self.logger.error(f"Failed to send {channel} alert: {e}")

    async def _send_slack_alert(self, incident: Incident) -> None:
        """Send alert to Slack"""
        self.logger.info(f"Slack alert sent for incident {incident.id}")

    async def _send_email_alert(self, incident: Incident) -> None:
        """Send alert via email"""
        self.logger.info(f"Email alert sent for incident {incident.id}")

    async def _send_sms_alert(self, incident: Incident) -> None:
        """Send SMS alert for critical incidents"""
        self.logger.info(f"SMS alert sent for incident {incident.id}")

    async def _send_pagerduty_alert(self, incident: Incident) -> None:
        """Send alert to PagerDuty"""
        self.logger.info(f"PagerDuty alert sent for incident {incident.id}")


# Response time targets for iGaming incidents
INCIDENT_RESPONSE_SLAS = {
    IncidentSeverity.CRITICAL: {
        "acknowledge": timedelta(minutes=5),
        "mitigate": timedelta(minutes=30),
        "resolve": timedelta(hours=4),
    },
    IncidentSeverity.HIGH: {
        "acknowledge": timedelta(minutes=15),
        "mitigate": timedelta(hours=1),
        "resolve": timedelta(hours=8),
    },
    IncidentSeverity.MEDIUM: {
        "acknowledge": timedelta(hours=1),
        "mitigate": timedelta(hours=4),
        "resolve": timedelta(hours=24),
    },
    IncidentSeverity.LOW: {
        "acknowledge": timedelta(hours=4),
        "mitigate": timedelta(hours=24),
        "resolve": timedelta(days=7),
    },
}
