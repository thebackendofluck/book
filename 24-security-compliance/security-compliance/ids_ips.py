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
Intrusion Detection/Prevention System for iGaming Platforms.

Implements gambling-specific threat detection:
- SQL injection and XSS attempts
- Bonus abuse patterns
- Account takeover attempts
- Money laundering indicators
- DDoS attack detection
- API abuse patterns

Features behavioral analysis and automated response.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ThreatCategory(Enum):
    """Threat categories for iGaming."""

    INJECTION = "injection"
    XSS = "xss"
    FILE_SYSTEM = "file_system"
    AUTHENTICATION = "authentication"
    GAMBLING_FRAUD = "gambling_fraud"
    FINANCIAL_CRIME = "financial_crime"
    DDOS = "ddos"
    API_ABUSE = "api_abuse"
    DATA_EXFILTRATION = "data_exfiltration"


class ThreatSeverity(Enum):
    """Threat severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ResponseAction(Enum):
    """Automated response actions."""

    BLOCK = "block"
    RATE_LIMIT = "rate_limit"
    CAPTCHA = "captcha"
    ALERT = "alert"
    LOG = "log"
    QUARANTINE = "quarantine"


@dataclass
class DetectionRule:
    """IDS detection rule."""

    rule_id: str
    name: str
    pattern: re.Pattern[str]
    severity: ThreatSeverity
    category: ThreatCategory
    description: str
    response_action: ResponseAction
    enabled: bool = True


@dataclass
class ThreatAlert:
    """Detected threat alert."""

    alert_id: str
    timestamp: datetime
    ip_address: str
    user_id: Optional[str]
    endpoint: str
    threats: list[dict[str, Any]]
    severity: ThreatSeverity
    action_taken: ResponseAction
    request_data: dict[str, Any]
    blocked: bool = False


class GamblingIDS:
    """
    Advanced IDS/IPS for gambling platforms.

    Features:
    - Pattern-based threat detection
    - Behavioral analysis
    - Gambling-specific fraud detection
    - Automated response actions
    - Redis-backed tracking
    """

    def __init__(self, redis_client: Any):
        self.redis = redis_client
        self.rules = self._initialize_detection_rules()
        self._alert_counter = 0

        # Alert thresholds
        self.alert_thresholds = {
            "requests_per_minute": 1000,
            "failed_logins_per_minute": 50,
            "suspicious_ips_per_hour": 10,
            "bonus_abuse_attempts_per_hour": 20,
            "large_transactions_per_hour": 5,
        }

        # Tracking data structures (in-memory for performance)
        self.ip_tracking: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=1000)
        )
        self.user_tracking: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=500)
        )
        self.endpoint_tracking: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=2000)
        )

    def _initialize_detection_rules(self) -> dict[str, DetectionRule]:
        """Initialize detection rules for gambling-specific threats."""
        return {
            "sql_injection": DetectionRule(
                rule_id="SQL-001",
                name="SQL Injection Attempt",
                pattern=re.compile(
                    r"(\'|(\-\-)|(\%27)|(\%3B)|(;)|(\bunion\b)|(\bselect\b)|(\binsert\b)|(\bdelete\b)|(\bdrop\b))",
                    re.IGNORECASE,
                ),
                severity=ThreatSeverity.HIGH,
                category=ThreatCategory.INJECTION,
                description="SQL injection attack pattern detected",
                response_action=ResponseAction.BLOCK,
            ),
            "xss_attempt": DetectionRule(
                rule_id="XSS-001",
                name="XSS Attempt",
                pattern=re.compile(
                    r"(<script|javascript:|on\w+\s*=|alert\(|document\.|window\.)",
                    re.IGNORECASE,
                ),
                severity=ThreatSeverity.HIGH,
                category=ThreatCategory.XSS,
                description="Cross-site scripting attack pattern detected",
                response_action=ResponseAction.BLOCK,
            ),
            "path_traversal": DetectionRule(
                rule_id="PATH-001",
                name="Path Traversal Attempt",
                pattern=re.compile(
                    r"(\.\./|\.\.\\|%2e%2e%2f|%2e%2e%5c|\.\.%c0%af)",
                    re.IGNORECASE,
                ),
                severity=ThreatSeverity.HIGH,
                category=ThreatCategory.FILE_SYSTEM,
                description="Directory traversal attack pattern detected",
                response_action=ResponseAction.BLOCK,
            ),
            "command_injection": DetectionRule(
                rule_id="CMD-001",
                name="Command Injection Attempt",
                pattern=re.compile(
                    r"(;|\||`|\$\(|&&|\|\|).*?(cat|ls|pwd|whoami|id|uname)",
                    re.IGNORECASE,
                ),
                severity=ThreatSeverity.CRITICAL,
                category=ThreatCategory.INJECTION,
                description="OS command injection pattern detected",
                response_action=ResponseAction.BLOCK,
            ),
            "bonus_abuse": DetectionRule(
                rule_id="FRAUD-001",
                name="Bonus Abuse Pattern",
                pattern=re.compile(
                    r"(multiple.*bonus|bonus.*farm|bonus.*exploit|duplicate.*claim)",
                    re.IGNORECASE,
                ),
                severity=ThreatSeverity.MEDIUM,
                category=ThreatCategory.GAMBLING_FRAUD,
                description="Potential bonus abuse activity detected",
                response_action=ResponseAction.ALERT,
            ),
            "account_takeover": DetectionRule(
                rule_id="AUTH-001",
                name="Account Takeover Pattern",
                pattern=re.compile(
                    r"(password.*reset.*multiple|login.*different.*ip|credential.*stuff)",
                    re.IGNORECASE,
                ),
                severity=ThreatSeverity.HIGH,
                category=ThreatCategory.AUTHENTICATION,
                description="Potential account takeover attempt",
                response_action=ResponseAction.RATE_LIMIT,
            ),
            "money_laundering": DetectionRule(
                rule_id="AML-001",
                name="Money Laundering Indicator",
                pattern=re.compile(
                    r"(large.*deposit.*withdraw|rapid.*transfer|structur.*transaction)",
                    re.IGNORECASE,
                ),
                severity=ThreatSeverity.CRITICAL,
                category=ThreatCategory.FINANCIAL_CRIME,
                description="Potential money laundering pattern detected",
                response_action=ResponseAction.QUARANTINE,
            ),
            "api_scraping": DetectionRule(
                rule_id="API-001",
                name="API Scraping Pattern",
                pattern=re.compile(
                    r"(bot|crawler|spider|scraper|curl|wget|python-requests)",
                    re.IGNORECASE,
                ),
                severity=ThreatSeverity.LOW,
                category=ThreatCategory.API_ABUSE,
                description="Potential automated scraping detected",
                response_action=ResponseAction.RATE_LIMIT,
            ),
        }

    async def analyze_request(self, request_data: dict[str, Any]) -> Optional[ThreatAlert]:
        """
        Analyze incoming request for security threats.

        Args:
            request_data: Dict containing ip_address, user_id, endpoint,
                         method, headers, body, query_params

        Returns:
            ThreatAlert if threats detected, None otherwise
        """
        try:
            ip = request_data.get("ip_address", "unknown")
            user_id = request_data.get("user_id")
            endpoint = request_data.get("endpoint", "/")
            method = request_data.get("method", "GET")
            headers = request_data.get("headers", {})
            body = request_data.get("body", "")
            query_params = request_data.get("query_params", {})

            # Track request
            current_time = time.time()
            self.ip_tracking[ip].append(current_time)
            self.endpoint_tracking[endpoint].append(current_time)
            if user_id:
                self.user_tracking[user_id].append(current_time)

            # Combine text for pattern analysis
            text_to_analyze = self._build_analysis_text(
                method, endpoint, body, query_params, headers
            )

            # Pattern-based detection
            threats_detected = self._detect_pattern_threats(text_to_analyze)

            # Behavioral analysis
            behavioral_threats = await self._analyze_behavioral_patterns(
                ip, user_id, endpoint, request_data
            )
            threats_detected.extend(behavioral_threats)

            if threats_detected:
                alert = self._create_alert(
                    ip, user_id, endpoint, method, headers, threats_detected
                )

                # Store alert
                await self._store_alert(alert)

                # Execute response
                await self._execute_response(alert)

                return alert

        except Exception as e:
            logger.error(f"Error analyzing request: {e}")

        return None

    def _build_analysis_text(
        self,
        method: str,
        endpoint: str,
        body: str,
        query_params: dict[str, Any],
        headers: dict[str, str],
    ) -> str:
        """Build combined text for pattern analysis."""
        parts = [
            method,
            endpoint,
            str(body) if body else "",
            json.dumps(query_params) if query_params else "",
            headers.get("user-agent", ""),
            headers.get("referer", ""),
        ]
        return " ".join(parts)

    def _detect_pattern_threats(self, text: str) -> list[dict[str, Any]]:
        """Detect threats using pattern matching."""
        threats: list[dict[str, Any]] = []

        for rule_name, rule in self.rules.items():
            if not rule.enabled:
                continue

            matches = rule.pattern.findall(text)
            if matches:
                threats.append({
                    "rule_id": rule.rule_id,
                    "rule_name": rule.name,
                    "severity": rule.severity.value,
                    "category": rule.category.value,
                    "description": rule.description,
                    "matched_patterns": matches[:5],  # First 5 matches
                    "response_action": rule.response_action.value,
                })

        return threats

    async def _analyze_behavioral_patterns(
        self,
        ip: str,
        user_id: Optional[str],
        endpoint: str,
        request_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Analyze behavioral patterns for anomalies."""
        threats: list[dict[str, Any]] = []
        current_time = time.time()
        one_minute_ago = current_time - 60
        one_hour_ago = current_time - 3600

        # Check request rate from IP
        recent_requests = sum(
            1 for t in self.ip_tracking[ip] if t > one_minute_ago
        )
        if recent_requests > self.alert_thresholds["requests_per_minute"]:
            threats.append({
                "rule_id": "RATE-001",
                "rule_name": "Rate Limit Exceeded",
                "severity": ThreatSeverity.MEDIUM.value,
                "category": ThreatCategory.DDOS.value,
                "description": f"IP {ip} exceeded rate limit: {recent_requests}/min",
                "response_action": ResponseAction.RATE_LIMIT.value,
            })

        # Check failed login attempts
        if "login" in endpoint.lower() and request_data.get("failed_attempt"):
            failed_logins = await self._get_failed_login_count(ip, one_minute_ago)
            if failed_logins > self.alert_thresholds["failed_logins_per_minute"]:
                threats.append({
                    "rule_id": "AUTH-002",
                    "rule_name": "Brute Force Attempt",
                    "severity": ThreatSeverity.HIGH.value,
                    "category": ThreatCategory.AUTHENTICATION.value,
                    "description": f"Multiple failed logins from {ip}",
                    "response_action": ResponseAction.BLOCK.value,
                })

        # Check for unusual transaction patterns
        if user_id and "transaction" in endpoint.lower():
            large_txns = await self._get_large_transaction_count(user_id, one_hour_ago)
            if large_txns > self.alert_thresholds["large_transactions_per_hour"]:
                threats.append({
                    "rule_id": "AML-002",
                    "rule_name": "Suspicious Transaction Volume",
                    "severity": ThreatSeverity.HIGH.value,
                    "category": ThreatCategory.FINANCIAL_CRIME.value,
                    "description": f"High transaction volume for user {user_id}",
                    "response_action": ResponseAction.ALERT.value,
                })

        return threats

    async def _get_failed_login_count(self, ip: str, since: float) -> int:
        """Get failed login count for IP."""
        try:
            key = f"failed_logins:{ip}"
            count = await self.redis.get(key)
            return int(count) if count else 0
        except Exception:
            return 0

    async def _get_large_transaction_count(self, user_id: str, since: float) -> int:
        """Get large transaction count for user."""
        try:
            key = f"large_txns:{user_id}"
            count = await self.redis.get(key)
            return int(count) if count else 0
        except Exception:
            return 0

    def _create_alert(
        self,
        ip: str,
        user_id: Optional[str],
        endpoint: str,
        method: str,
        headers: dict[str, str],
        threats: list[dict[str, Any]],
    ) -> ThreatAlert:
        """Create threat alert."""
        self._alert_counter += 1
        timestamp = datetime.now(timezone.utc)

        # Determine highest severity
        severity_order = ["critical", "high", "medium", "low", "info"]
        max_severity = ThreatSeverity.INFO
        for threat in threats:
            threat_sev = threat.get("severity", "info")
            if severity_order.index(threat_sev) < severity_order.index(max_severity.value):
                max_severity = ThreatSeverity(threat_sev)

        # Determine action
        action = self._determine_action(threats)

        return ThreatAlert(
            alert_id=f"ALERT-{int(timestamp.timestamp())}-{self._alert_counter:06d}",
            timestamp=timestamp,
            ip_address=ip,
            user_id=user_id,
            endpoint=endpoint,
            threats=threats,
            severity=max_severity,
            action_taken=action,
            request_data={
                "method": method,
                "user_agent": headers.get("user-agent"),
                "referer": headers.get("referer"),
            },
            blocked=action == ResponseAction.BLOCK,
        )

    def _determine_action(self, threats: list[dict[str, Any]]) -> ResponseAction:
        """Determine response action based on threats."""
        action_priority = [
            ResponseAction.BLOCK,
            ResponseAction.QUARANTINE,
            ResponseAction.RATE_LIMIT,
            ResponseAction.CAPTCHA,
            ResponseAction.ALERT,
            ResponseAction.LOG,
        ]

        for action in action_priority:
            for threat in threats:
                if threat.get("response_action") == action.value:
                    return action

        return ResponseAction.LOG

    async def _store_alert(self, alert: ThreatAlert) -> None:
        """Store alert in Redis."""
        try:
            alert_data = {
                "alert_id": alert.alert_id,
                "timestamp": alert.timestamp.isoformat(),
                "ip_address": alert.ip_address,
                "user_id": alert.user_id,
                "endpoint": alert.endpoint,
                "threats": alert.threats,
                "severity": alert.severity.value,
                "action_taken": alert.action_taken.value,
                "blocked": alert.blocked,
            }

            # Store in Redis with 7-day TTL
            key = f"alert:{alert.alert_id}"
            await self.redis.setex(key, 604800, json.dumps(alert_data))

            # Add to alerts list
            await self.redis.lpush("alerts:recent", alert.alert_id)
            await self.redis.ltrim("alerts:recent", 0, 9999)

            # Update counters
            await self.redis.incr(f"alerts:count:{alert.severity.value}")

            logger.info(f"Stored alert {alert.alert_id}")

        except Exception as e:
            logger.error(f"Failed to store alert: {e}")

    async def _execute_response(self, alert: ThreatAlert) -> None:
        """Execute automated response to threat."""
        try:
            if alert.action_taken == ResponseAction.BLOCK:
                await self._block_ip(alert.ip_address)
                logger.warning(f"Blocked IP {alert.ip_address}")

            elif alert.action_taken == ResponseAction.RATE_LIMIT:
                await self._rate_limit_ip(alert.ip_address)
                logger.info(f"Rate limited IP {alert.ip_address}")

            elif alert.action_taken == ResponseAction.QUARANTINE:
                if alert.user_id:
                    await self._quarantine_user(alert.user_id)
                    logger.warning(f"Quarantined user {alert.user_id}")

            elif alert.action_taken == ResponseAction.ALERT:
                await self._send_alert_notification(alert)

        except Exception as e:
            logger.error(f"Failed to execute response: {e}")

    async def _block_ip(self, ip: str, duration_hours: int = 24) -> None:
        """Block IP address."""
        key = f"blocked_ips:{ip}"
        await self.redis.setex(key, duration_hours * 3600, "blocked")

    async def _rate_limit_ip(self, ip: str) -> None:
        """Apply rate limiting to IP."""
        key = f"rate_limited:{ip}"
        await self.redis.setex(key, 3600, "limited")

    async def _quarantine_user(self, user_id: str) -> None:
        """Quarantine user account."""
        key = f"quarantined_users:{user_id}"
        await self.redis.setex(key, 86400, "quarantined")

    async def _send_alert_notification(self, alert: ThreatAlert) -> None:
        """Send alert notification."""
        # Placeholder - would integrate with alerting system
        logger.info(f"Alert notification: {alert.alert_id} - {alert.severity.value}")

    async def is_ip_blocked(self, ip: str) -> bool:
        """Check if IP is blocked."""
        try:
            result = await self.redis.get(f"blocked_ips:{ip}")
            return result is not None
        except Exception:
            return False

    async def get_recent_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent alerts."""
        try:
            alert_ids = await self.redis.lrange("alerts:recent", 0, limit - 1)
            alerts = []

            for alert_id in alert_ids:
                alert_id_str = alert_id.decode() if isinstance(alert_id, bytes) else alert_id
                alert_data = await self.redis.get(f"alert:{alert_id_str}")
                if alert_data:
                    data = alert_data.decode() if isinstance(alert_data, bytes) else alert_data
                    alerts.append(json.loads(data))

            return alerts

        except Exception as e:
            logger.error(f"Failed to get recent alerts: {e}")
            return []

    def get_stats(self) -> dict[str, Any]:
        """Get IDS statistics."""
        return {
            "rules_enabled": sum(1 for r in self.rules.values() if r.enabled),
            "total_rules": len(self.rules),
            "tracked_ips": len(self.ip_tracking),
            "tracked_users": len(self.user_tracking),
            "thresholds": self.alert_thresholds,
        }
