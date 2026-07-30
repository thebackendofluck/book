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
Network Traffic Encryption Monitor for iGaming Platforms.

Monitors network traffic for encryption compliance:
- TLS/SSL detection
- Unencrypted traffic alerting
- Protocol breakdown analysis
- Real-time dashboard
- Automated reporting

Designed for compliance with PCI DSS, GDPR encryption requirements.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class EncryptionProtocol(Enum):
    """Encryption protocols detected."""

    TLS_1_3 = "tls_1.3"
    TLS_1_2 = "tls_1.2"
    TLS_1_1 = "tls_1.1"  # Deprecated
    TLS_1_0 = "tls_1.0"  # Deprecated
    SSL_3_0 = "ssl_3.0"  # Insecure
    NONE = "none"  # Unencrypted
    UNKNOWN = "unknown"


class AlertType(Enum):
    """Alert types for encryption monitoring."""

    UNENCRYPTED_TRAFFIC = "unencrypted_traffic"
    DEPRECATED_PROTOCOL = "deprecated_protocol"
    LOW_ENCRYPTION_RATE = "low_encryption_rate"
    CERTIFICATE_EXPIRY = "certificate_expiry"
    DISK_SPACE_LOW = "disk_space_low"


@dataclass
class PacketAnalysisResult:
    """Result of packet analysis."""

    encrypted: bool
    protocol: EncryptionProtocol
    source_ip: str
    dest_ip: str
    source_port: int
    dest_port: int
    timestamp: datetime
    payload_size: int


@dataclass
class EncryptionStats:
    """Encryption statistics."""

    total_packets: int = 0
    encrypted_packets: int = 0
    unencrypted_packets: int = 0
    protocol_breakdown: dict[str, int] = field(default_factory=dict)
    hourly_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    alerts_sent: int = 0

    @property
    def encryption_rate(self) -> float:
        """Calculate encryption rate."""
        if self.total_packets == 0:
            return 0.0
        return (self.encrypted_packets / self.total_packets) * 100


@dataclass
class MonitorConfig:
    """Configuration for network monitor."""

    interface: str = "eth0"
    capture_filter: str = "tcp port 80 or tcp port 443"
    encryption_threshold: float = 95.0  # Alert if below
    alert_cooldown_minutes: int = 15
    log_retention_days: int = 30
    report_schedule: str = "daily"  # daily, hourly
    email_recipients: list[str] = field(default_factory=list)
    webhook_url: Optional[str] = None


class NetworkEncryptionMonitor:
    """
    Network traffic encryption monitor.

    Features:
    - Real-time packet capture and analysis
    - TLS version detection
    - Encryption rate monitoring
    - Automated alerting
    - Dashboard and reporting
    """

    # Known TLS/SSL ports
    ENCRYPTED_PORTS = {443, 993, 995, 8443, 465}

    # TLS record content type
    TLS_HANDSHAKE = 0x16
    TLS_ALERT = 0x15
    TLS_APPLICATION_DATA = 0x17

    def __init__(self, config: Optional[MonitorConfig] = None):
        self.config = config or MonitorConfig()
        self.stats = EncryptionStats()
        self.last_alert_time = datetime.now(timezone.utc)
        self._running = False

        # Per-source tracking
        self.source_stats: dict[str, EncryptionStats] = defaultdict(EncryptionStats)

    def analyze_packet(self, packet_data: bytes, metadata: dict[str, Any]) -> PacketAnalysisResult:
        """
        Analyze a network packet for encryption.

        Args:
            packet_data: Raw packet bytes
            metadata: Packet metadata (IPs, ports, etc.)

        Returns:
            PacketAnalysisResult with encryption status
        """
        source_ip = metadata.get("source_ip", "unknown")
        dest_ip = metadata.get("dest_ip", "unknown")
        source_port = metadata.get("source_port", 0)
        dest_port = metadata.get("dest_port", 0)
        timestamp = datetime.now(timezone.utc)

        # Determine if packet is encrypted
        encrypted, protocol = self._detect_encryption(
            packet_data, dest_port, source_port
        )

        result = PacketAnalysisResult(
            encrypted=encrypted,
            protocol=protocol,
            source_ip=source_ip,
            dest_ip=dest_ip,
            source_port=source_port,
            dest_port=dest_port,
            timestamp=timestamp,
            payload_size=len(packet_data),
        )

        # Update statistics
        self._update_stats(result)

        return result

    def _detect_encryption(
        self, packet_data: bytes, dest_port: int, source_port: int
    ) -> tuple[bool, EncryptionProtocol]:
        """Detect if packet is encrypted and identify protocol."""
        # Check for TLS/SSL by port
        if dest_port in self.ENCRYPTED_PORTS or source_port in self.ENCRYPTED_PORTS:
            if len(packet_data) > 5:
                # Check TLS record header
                if packet_data[0] == self.TLS_HANDSHAKE:
                    # Parse TLS version
                    if len(packet_data) >= 3:
                        major = packet_data[1]
                        minor = packet_data[2]
                        protocol = self._parse_tls_version(major, minor)
                        return True, protocol
                elif packet_data[0] == self.TLS_APPLICATION_DATA:
                    return True, EncryptionProtocol.TLS_1_2  # Assume TLS 1.2+

            return True, EncryptionProtocol.UNKNOWN

        # Check for HTTP (unencrypted)
        if dest_port == 80 or source_port == 80:
            if packet_data.startswith(b"GET ") or packet_data.startswith(b"POST "):
                return False, EncryptionProtocol.NONE
            if packet_data.startswith(b"HTTP/"):
                return False, EncryptionProtocol.NONE

        # Unknown traffic
        return False, EncryptionProtocol.UNKNOWN

    def _parse_tls_version(self, major: int, minor: int) -> EncryptionProtocol:
        """Parse TLS version from record header."""
        if major == 3:
            if minor == 4:
                return EncryptionProtocol.TLS_1_3
            elif minor == 3:
                return EncryptionProtocol.TLS_1_2
            elif minor == 2:
                return EncryptionProtocol.TLS_1_1
            elif minor == 1:
                return EncryptionProtocol.TLS_1_0
            elif minor == 0:
                return EncryptionProtocol.SSL_3_0

        return EncryptionProtocol.UNKNOWN

    def _update_stats(self, result: PacketAnalysisResult) -> None:
        """Update statistics with packet result."""
        self.stats.total_packets += 1
        current_hour = result.timestamp.strftime("%Y-%m-%d %H")

        if result.encrypted:
            self.stats.encrypted_packets += 1
            if current_hour not in self.stats.hourly_stats:
                self.stats.hourly_stats[current_hour] = {"encrypted": 0, "unencrypted": 0}
            self.stats.hourly_stats[current_hour]["encrypted"] += 1
        else:
            self.stats.unencrypted_packets += 1
            if current_hour not in self.stats.hourly_stats:
                self.stats.hourly_stats[current_hour] = {"encrypted": 0, "unencrypted": 0}
            self.stats.hourly_stats[current_hour]["unencrypted"] += 1

        # Update protocol breakdown
        protocol_name = result.protocol.value
        self.stats.protocol_breakdown[protocol_name] = (
            self.stats.protocol_breakdown.get(protocol_name, 0) + 1
        )

        # Update per-source stats
        source_stats = self.source_stats[result.source_ip]
        source_stats.total_packets += 1
        if result.encrypted:
            source_stats.encrypted_packets += 1
        else:
            source_stats.unencrypted_packets += 1

    async def check_alerts(self) -> list[dict[str, Any]]:
        """Check for alert conditions."""
        alerts: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        # Check cooldown
        cooldown = timedelta(minutes=self.config.alert_cooldown_minutes)
        if now - self.last_alert_time < cooldown:
            return alerts

        # Check encryption rate
        if self.stats.total_packets > 100:  # Minimum sample size
            if self.stats.encryption_rate < self.config.encryption_threshold:
                alerts.append({
                    "type": AlertType.LOW_ENCRYPTION_RATE.value,
                    "severity": "high",
                    "message": f"Encryption rate ({self.stats.encryption_rate:.1f}%) below threshold ({self.config.encryption_threshold}%)",
                    "timestamp": now.isoformat(),
                    "details": {
                        "total_packets": self.stats.total_packets,
                        "encrypted": self.stats.encrypted_packets,
                        "unencrypted": self.stats.unencrypted_packets,
                    },
                })

        # Check for deprecated protocols
        deprecated = ["tls_1.0", "tls_1.1", "ssl_3.0"]
        for proto in deprecated:
            count = self.stats.protocol_breakdown.get(proto, 0)
            if count > 0:
                alerts.append({
                    "type": AlertType.DEPRECATED_PROTOCOL.value,
                    "severity": "medium",
                    "message": f"Deprecated protocol {proto} detected ({count} packets)",
                    "timestamp": now.isoformat(),
                })

        if alerts:
            self.last_alert_time = now
            self.stats.alerts_sent += len(alerts)

        return alerts

    def get_current_stats(self) -> dict[str, Any]:
        """Get current monitoring statistics."""
        return {
            "total_packets": self.stats.total_packets,
            "encrypted_packets": self.stats.encrypted_packets,
            "unencrypted_packets": self.stats.unencrypted_packets,
            "encryption_rate_percent": round(self.stats.encryption_rate, 2),
            "protocol_breakdown": dict(self.stats.protocol_breakdown),
            "alerts_sent": self.stats.alerts_sent,
            "monitoring_interface": self.config.interface,
            "threshold_percent": self.config.encryption_threshold,
        }

    def get_hourly_trends(self, hours: int = 24) -> list[dict[str, Any]]:
        """Get hourly encryption trends."""
        trends = []
        sorted_hours = sorted(self.stats.hourly_stats.keys())[-hours:]

        for hour in sorted_hours:
            data = self.stats.hourly_stats[hour]
            total = data["encrypted"] + data["unencrypted"]
            rate = (data["encrypted"] / total * 100) if total > 0 else 0

            trends.append({
                "hour": hour,
                "encrypted": data["encrypted"],
                "unencrypted": data["unencrypted"],
                "total": total,
                "encryption_rate": round(rate, 2),
            })

        return trends

    def get_top_unencrypted_sources(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get sources with most unencrypted traffic."""
        sources = []

        for ip, stats in self.source_stats.items():
            if stats.unencrypted_packets > 0:
                sources.append({
                    "ip": ip,
                    "unencrypted_packets": stats.unencrypted_packets,
                    "total_packets": stats.total_packets,
                    "encryption_rate": round(
                        stats.encrypted_packets / stats.total_packets * 100
                        if stats.total_packets > 0
                        else 0,
                        2,
                    ),
                })

        # Sort by unencrypted count
        sources.sort(key=lambda x: x["unencrypted_packets"], reverse=True)

        return sources[:limit]

    def reset_stats(self) -> None:
        """Reset monitoring statistics."""
        self.stats = EncryptionStats()
        self.source_stats.clear()
        logger.info("Statistics reset")

    async def start(self) -> None:
        """Start the monitoring service."""
        self._running = True
        logger.info(f"Starting network encryption monitor on {self.config.interface}")

        # In real implementation, would start packet capture here
        # For now, this is a placeholder for the capture loop
        while self._running:
            await asyncio.sleep(1)
            alerts = await self.check_alerts()
            for alert in alerts:
                logger.warning(f"Alert: {alert['message']}")

    def stop(self) -> None:
        """Stop the monitoring service."""
        self._running = False
        logger.info("Stopping network encryption monitor")


class EncryptionComplianceChecker:
    """
    Check encryption compliance against standards.

    Supports:
    - PCI DSS requirements
    - GDPR encryption requirements
    - Industry best practices
    """

    def __init__(self, monitor: NetworkEncryptionMonitor):
        self.monitor = monitor

    def check_pci_dss_compliance(self) -> dict[str, Any]:
        """
        Check PCI DSS encryption requirements.

        Relevant requirements:
        - 4.1: Use strong cryptography for transmission
        - 4.2: Never send unprotected PANs
        """
        stats = self.monitor.get_current_stats()
        compliant = True
        checks: list[dict[str, str]] = []

        # Check encryption rate (should be 100% for cardholder data)
        enc_rate = stats["encryption_rate_percent"]
        if enc_rate < 100:
            compliant = False
            checks.append({
                "requirement": "4.1",
                "status": "FAIL",
                "message": f"Encryption rate {enc_rate}% - all cardholder data must be encrypted",
            })
        else:
            checks.append({
                "requirement": "4.1",
                "status": "PASS",
                "message": "All traffic encrypted",
            })

        # Check for deprecated protocols
        protocols = stats["protocol_breakdown"]
        deprecated = ["tls_1.0", "tls_1.1", "ssl_3.0"]
        for proto in deprecated:
            if protocols.get(proto, 0) > 0:
                compliant = False
                checks.append({
                    "requirement": "4.1",
                    "status": "FAIL",
                    "message": f"Deprecated protocol {proto} in use - only TLS 1.2+ allowed",
                })

        return {"compliant": compliant, "checks": checks}

    def check_gdpr_compliance(self) -> dict[str, Any]:
        """
        Check GDPR encryption requirements.

        Article 32: Security of processing - appropriate encryption
        """
        stats = self.monitor.get_current_stats()
        compliant = True
        checks: list[dict[str, str]] = []

        # GDPR requires "appropriate" encryption
        enc_rate = stats["encryption_rate_percent"]
        if enc_rate < 95:  # Industry standard threshold
            compliant = False
            checks.append({
                "article": "32",
                "status": "WARNING",
                "message": f"Encryption rate {enc_rate}% may not meet 'appropriate' standard",
            })
        else:
            checks.append({
                "article": "32",
                "status": "PASS",
                "message": "Encryption rate meets industry standards",
            })

        return {"compliant": compliant, "checks": checks}

    def generate_compliance_report(self) -> dict[str, Any]:
        """Generate comprehensive compliance report."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pci_dss": self.check_pci_dss_compliance(),
            "gdpr": self.check_gdpr_compliance(),
            "statistics": self.monitor.get_current_stats(),
            "recommendations": self._generate_recommendations(),
        }

    def _generate_recommendations(self) -> list[str]:
        """Generate compliance recommendations."""
        recommendations = []
        stats = self.monitor.get_current_stats()

        if stats["encryption_rate_percent"] < 100:
            recommendations.append(
                "Identify and encrypt all unencrypted traffic sources"
            )

        protocols = stats["protocol_breakdown"]
        if any(protocols.get(p, 0) > 0 for p in ["tls_1.0", "tls_1.1"]):
            recommendations.append(
                "Upgrade all services to TLS 1.2 or higher"
            )

        if protocols.get("ssl_3.0", 0) > 0:
            recommendations.append(
                "CRITICAL: Disable SSL 3.0 immediately - known vulnerabilities"
            )

        if not recommendations:
            recommendations.append(
                "Maintain current encryption standards and continue monitoring"
            )

        return recommendations
