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
Security Reporting Module for iGaming Platforms.

Generates comprehensive security reports:
- Daily/weekly security summaries
- Encryption compliance reports
- Incident summaries
- Trend analysis with visualizations
- Automated email delivery

Designed for regulatory compliance and executive reporting.
"""

import json
import logging
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ReportType(Enum):
    """Types of security reports."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    INCIDENT = "incident"
    COMPLIANCE = "compliance"
    EXECUTIVE = "executive"


class ReportFormat(Enum):
    """Report output formats."""

    JSON = "json"
    HTML = "html"
    PDF = "pdf"


@dataclass
class SecurityReport:
    """Security report data structure."""

    report_id: str
    report_type: ReportType
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    summary: dict[str, Any]
    details: dict[str, Any]
    recommendations: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComplianceReport:
    """Compliance-focused report."""

    report_id: str
    frameworks: list[str]  # PCI DSS, GDPR, etc.
    generated_at: datetime
    overall_status: str  # compliant, non_compliant, partial
    findings: list[dict[str, Any]]
    evidence: dict[str, Any]
    remediation_plan: list[dict[str, Any]]


@dataclass
class EmailConfig:
    """Email configuration."""

    smtp_server: str
    smtp_port: int
    username: str
    password: str
    from_address: str
    use_tls: bool = True


class ReportGenerator:
    """
    Generate security reports with visualizations.

    Features:
    - Multiple report types (daily, weekly, compliance)
    - Trend analysis
    - Chart generation
    - Email delivery
    - Multi-format export
    """

    def __init__(
        self,
        output_dir: str = "/app/data/reports",
        email_config: Optional[EmailConfig] = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.email_config = email_config
        self._report_counter = 0

    def _generate_report_id(self, report_type: ReportType) -> str:
        """Generate unique report ID."""
        self._report_counter += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"RPT-{report_type.value.upper()}-{timestamp}-{self._report_counter:04d}"

    def generate_daily_report(self, stats_data: dict[str, Any]) -> SecurityReport:
        """
        Generate comprehensive daily security report.

        Args:
            stats_data: Dictionary containing encryption stats, alerts, etc.

        Returns:
            SecurityReport with daily summary
        """
        report_date = datetime.now(timezone.utc)
        period_start = report_date.replace(hour=0, minute=0, second=0, microsecond=0)
        period_end = report_date

        report = SecurityReport(
            report_id=self._generate_report_id(ReportType.DAILY),
            report_type=ReportType.DAILY,
            generated_at=report_date,
            period_start=period_start,
            period_end=period_end,
            summary=self._generate_summary(stats_data),
            details={
                "trends": self._analyze_trends(stats_data),
                "alerts": self._summarize_alerts(stats_data),
                "top_sources": stats_data.get("top_unencrypted_sources", []),
                "protocol_breakdown": stats_data.get("protocol_breakdown", {}),
            },
            recommendations=self._generate_recommendations(stats_data),
            metadata={
                "report_version": "1.0",
                "generator": "SecurityReportGenerator",
            },
        )

        # Save report
        self._save_report(report)

        return report

    def _generate_summary(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Generate report summary statistics."""
        total_packets = stats.get("total_packets", 0)
        encrypted = stats.get("encrypted_packets", 0)
        unencrypted = stats.get("unencrypted_packets", 0)

        encryption_rate = (encrypted / total_packets * 100) if total_packets > 0 else 0

        return {
            "total_packets_analyzed": total_packets,
            "encrypted_packets": encrypted,
            "unencrypted_packets": unencrypted,
            "encryption_rate_percent": round(encryption_rate, 2),
            "alerts_triggered": stats.get("alerts_sent", 0),
            "compliance_status": "PASS" if encryption_rate >= 95 else "FAIL",
            "risk_level": self._calculate_risk_level(stats),
        }

    def _calculate_risk_level(self, stats: dict[str, Any]) -> str:
        """Calculate overall risk level."""
        total_packets = stats.get("total_packets", 0)
        if total_packets == 0:
            return "UNKNOWN"

        encrypted = stats.get("encrypted_packets", 0)
        encryption_rate = (encrypted / total_packets * 100)

        if encryption_rate >= 99:
            return "LOW"
        elif encryption_rate >= 95:
            return "MEDIUM"
        elif encryption_rate >= 80:
            return "HIGH"
        else:
            return "CRITICAL"

    def _analyze_trends(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Analyze traffic encryption trends."""
        hourly_stats = stats.get("hourly_stats", {})

        peak_hours: list[str] = []
        encryption_trends: list[dict[str, Any]] = []
        unencrypted_spikes: list[dict[str, Any]] = []
        average_encryption_rate = 0.0

        rates: list[float] = []
        for hour, data in hourly_stats.items():
            encrypted = data.get("encrypted", 0)
            unencrypted = data.get("unencrypted", 0)
            total = encrypted + unencrypted

            if total > 0:
                enc_rate = (encrypted / total) * 100
                rates.append(enc_rate)

                encryption_trends.append({
                    "hour": hour,
                    "rate": round(enc_rate, 1),
                    "total_packets": total,
                })

                if enc_rate < 80:
                    unencrypted_spikes.append({
                        "hour": hour,
                        "rate": round(enc_rate, 1),
                        "total_packets": total,
                    })

        if rates:
            average_encryption_rate = round(sum(rates) / len(rates), 2)

        return {
            "peak_hours": peak_hours,
            "encryption_trends": encryption_trends,
            "unencrypted_spikes": unencrypted_spikes,
            "average_encryption_rate": average_encryption_rate,
        }

    def _summarize_alerts(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Summarize security alerts."""
        alerts = stats.get("alerts", [])

        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}

        for alert in alerts:
            alert_type = alert.get("type", "unknown")
            severity = alert.get("severity", "unknown")

            by_type[alert_type] = by_type.get(alert_type, 0) + 1
            by_severity[severity] = by_severity.get(severity, 0) + 1

        return {
            "total_alerts": len(alerts),
            "by_type": by_type,
            "by_severity": by_severity,
            "most_common_type": max(by_type.keys(), key=lambda k: by_type[k])
            if by_type
            else None,
        }

    def _generate_recommendations(self, stats: dict[str, Any]) -> list[str]:
        """Generate security recommendations based on data."""
        recommendations = []
        summary = self._generate_summary(stats)

        encryption_rate = summary["encryption_rate_percent"]

        if encryption_rate < 95:
            recommendations.append(
                "PRIORITY: Encryption rate is below 95%. Identify and encrypt unencrypted services immediately."
            )

        if encryption_rate < 80:
            recommendations.append(
                "CRITICAL: Encryption rate below 80% poses significant compliance risk. "
                "Conduct immediate audit of all network traffic sources."
            )

        # Check for deprecated protocols
        protocols = stats.get("protocol_breakdown", {})
        if any(protocols.get(p, 0) > 0 for p in ["tls_1.0", "tls_1.1", "ssl_3.0"]):
            recommendations.append(
                "Upgrade all services to TLS 1.2 or higher. "
                "Deprecated protocols are vulnerable to known attacks."
            )

        # Check disk usage
        disk_usage = stats.get("disk_usage", {}).get("usage_percent", 0)
        if disk_usage > 80:
            recommendations.append(
                "Disk usage is high. Consider increasing retention period or adding storage capacity."
            )

        # Check alert volume
        if stats.get("alerts_sent", 0) > 50:
            recommendations.append(
                "High alert volume detected. Review alert thresholds to reduce false positives "
                "or investigate underlying security issues."
            )

        if not recommendations:
            recommendations.append(
                "Security posture is good. Continue regular monitoring and maintain current standards."
            )

        return recommendations

    def _save_report(
        self, report: SecurityReport, format: ReportFormat = ReportFormat.JSON
    ) -> Path:
        """Save report to file."""
        date_str = report.generated_at.strftime("%Y-%m-%d")
        filename = f"{report.report_type.value}_report_{date_str}.{format.value}"
        filepath = self.output_dir / filename

        report_dict = {
            "report_id": report.report_id,
            "report_type": report.report_type.value,
            "generated_at": report.generated_at.isoformat(),
            "period_start": report.period_start.isoformat(),
            "period_end": report.period_end.isoformat(),
            "summary": report.summary,
            "details": report.details,
            "recommendations": report.recommendations,
            "metadata": report.metadata,
        }

        with open(filepath, "w") as f:
            json.dump(report_dict, f, indent=2)

        logger.info(f"Report saved to {filepath}")
        return filepath

    def generate_html_report(self, report: SecurityReport) -> str:
        """Generate HTML version of report."""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Security Report - {report.generated_at.strftime('%Y-%m-%d')}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; border-bottom: 2px solid #007bff; padding-bottom: 10px; }}
        h2 {{ color: #555; margin-top: 30px; }}
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 20px 0; }}
        .metric {{ background: #f8f9fa; padding: 20px; border-radius: 8px; text-align: center; }}
        .metric-value {{ font-size: 2em; font-weight: bold; color: #007bff; }}
        .metric-label {{ color: #666; margin-top: 5px; }}
        .status-pass {{ color: #28a745; }}
        .status-fail {{ color: #dc3545; }}
        .recommendations {{ background: #fff3cd; padding: 20px; border-radius: 8px; margin-top: 20px; }}
        .recommendations ul {{ margin: 10px 0; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #f8f9fa; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Network Security Report</h1>
        <p><strong>Report ID:</strong> {report.report_id}</p>
        <p><strong>Period:</strong> {report.period_start.strftime('%Y-%m-%d %H:%M')} to {report.period_end.strftime('%Y-%m-%d %H:%M')}</p>

        <h2>Executive Summary</h2>
        <div class="summary">
            <div class="metric">
                <div class="metric-value">{report.summary.get('total_packets_analyzed', 0):,}</div>
                <div class="metric-label">Total Packets</div>
            </div>
            <div class="metric">
                <div class="metric-value">{report.summary.get('encryption_rate_percent', 0)}%</div>
                <div class="metric-label">Encryption Rate</div>
            </div>
            <div class="metric">
                <div class="metric-value">{report.summary.get('alerts_triggered', 0)}</div>
                <div class="metric-label">Alerts</div>
            </div>
            <div class="metric">
                <div class="metric-value class="{'status-pass' if report.summary.get('compliance_status') == 'PASS' else 'status-fail'}">{report.summary.get('compliance_status', 'UNKNOWN')}</div>
                <div class="metric-label">Compliance</div>
            </div>
        </div>

        <h2>Recommendations</h2>
        <div class="recommendations">
            <ul>
                {''.join(f'<li>{rec}</li>' for rec in report.recommendations)}
            </ul>
        </div>

        <p style="margin-top: 40px; color: #666; font-size: 12px;">
            Generated by iGaming Security Monitor | {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}
        </p>
    </div>
</body>
</html>
"""
        return html

    def send_report_email(
        self,
        report: SecurityReport,
        recipients: list[str],
        attach_json: bool = True,
    ) -> bool:
        """
        Send report via email.

        Args:
            report: SecurityReport to send
            recipients: List of email addresses
            attach_json: Whether to attach JSON version

        Returns:
            True if sent successfully
        """
        if not self.email_config:
            logger.error("Email not configured")
            return False

        try:
            msg = MIMEMultipart()
            msg["Subject"] = (
                f"Security Report - {report.report_type.value.title()} - "
                f"{report.generated_at.strftime('%Y-%m-%d')}"
            )
            msg["From"] = self.email_config.from_address
            msg["To"] = ", ".join(recipients)

            # HTML body
            html_body = self.generate_html_report(report)
            msg.attach(MIMEText(html_body, "html"))

            # Attach JSON if requested
            if attach_json:
                json_data = json.dumps(
                    {
                        "report_id": report.report_id,
                        "summary": report.summary,
                        "details": report.details,
                        "recommendations": report.recommendations,
                    },
                    indent=2,
                )
                attachment = MIMEBase("application", "json")
                attachment.set_payload(json_data.encode())
                encoders.encode_base64(attachment)
                attachment.add_header(
                    "Content-Disposition",
                    f"attachment; filename=security_report_{report.generated_at.strftime('%Y%m%d')}.json",
                )
                msg.attach(attachment)

            # Send email
            with smtplib.SMTP(
                self.email_config.smtp_server, self.email_config.smtp_port
            ) as server:
                if self.email_config.use_tls:
                    server.starttls()
                server.login(self.email_config.username, self.email_config.password)
                server.send_message(msg)

            logger.info(f"Report sent to {len(recipients)} recipients")
            return True

        except Exception as e:
            logger.error(f"Failed to send report email: {e}")
            return False

    def generate_compliance_report(
        self,
        stats_data: dict[str, Any],
        frameworks: list[str],
    ) -> ComplianceReport:
        """
        Generate compliance-focused report.

        Args:
            stats_data: Security statistics
            frameworks: List of frameworks (PCI_DSS, GDPR, ISO_27001)

        Returns:
            ComplianceReport with findings and remediation plan
        """
        report_id = self._generate_report_id(ReportType.COMPLIANCE)
        findings: list[dict[str, Any]] = []
        overall_compliant = True

        encryption_rate = stats_data.get("encryption_rate_percent", 0)

        for framework in frameworks:
            if framework == "PCI_DSS":
                finding = self._check_pci_dss(stats_data)
                findings.append(finding)
                if finding["status"] != "PASS":
                    overall_compliant = False

            elif framework == "GDPR":
                finding = self._check_gdpr(stats_data)
                findings.append(finding)
                if finding["status"] != "PASS":
                    overall_compliant = False

            elif framework == "ISO_27001":
                finding = self._check_iso_27001(stats_data)
                findings.append(finding)
                if finding["status"] != "PASS":
                    overall_compliant = False

        return ComplianceReport(
            report_id=report_id,
            frameworks=frameworks,
            generated_at=datetime.now(timezone.utc),
            overall_status="compliant" if overall_compliant else "non_compliant",
            findings=findings,
            evidence={
                "encryption_rate": encryption_rate,
                "total_packets_analyzed": stats_data.get("total_packets", 0),
                "protocols_in_use": stats_data.get("protocol_breakdown", {}),
            },
            remediation_plan=self._generate_remediation_plan(findings),
        )

    def _check_pci_dss(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Check PCI DSS compliance."""
        encryption_rate = stats.get("encryption_rate_percent", 0)
        protocols = stats.get("protocol_breakdown", {})

        issues = []
        if encryption_rate < 100:
            issues.append("Requirement 4.1: Not all cardholder data transmissions are encrypted")

        deprecated = ["tls_1.0", "tls_1.1", "ssl_3.0"]
        for proto in deprecated:
            if protocols.get(proto, 0) > 0:
                issues.append(f"Requirement 4.1: Deprecated protocol {proto} in use")

        return {
            "framework": "PCI_DSS",
            "status": "PASS" if not issues else "FAIL",
            "issues": issues,
            "requirements_checked": ["4.1", "4.2"],
        }

    def _check_gdpr(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Check GDPR compliance."""
        encryption_rate = stats.get("encryption_rate_percent", 0)

        issues = []
        if encryption_rate < 95:
            issues.append("Article 32: Encryption rate may not meet 'appropriate' technical measures")

        return {
            "framework": "GDPR",
            "status": "PASS" if not issues else "WARNING",
            "issues": issues,
            "articles_checked": ["32", "25"],
        }

    def _check_iso_27001(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Check ISO 27001 compliance."""
        encryption_rate = stats.get("encryption_rate_percent", 0)

        issues = []
        if encryption_rate < 95:
            issues.append("A.10.1: Cryptographic controls may be insufficient")

        return {
            "framework": "ISO_27001",
            "status": "PASS" if not issues else "WARNING",
            "issues": issues,
            "controls_checked": ["A.10.1", "A.13.1"],
        }

    def _generate_remediation_plan(
        self, findings: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Generate remediation plan based on findings."""
        plan = []

        for finding in findings:
            if finding["status"] != "PASS":
                for issue in finding.get("issues", []):
                    plan.append({
                        "framework": finding["framework"],
                        "issue": issue,
                        "priority": "HIGH" if "FAIL" in finding["status"] else "MEDIUM",
                        "recommended_action": self._get_remediation_action(issue),
                        "estimated_effort": "1-2 weeks",
                    })

        return plan

    def _get_remediation_action(self, issue: str) -> str:
        """Get recommended action for an issue."""
        if "encryption rate" in issue.lower():
            return "Audit all network traffic and implement TLS for unencrypted services"
        elif "deprecated protocol" in issue.lower():
            return "Upgrade server configurations to TLS 1.2 or TLS 1.3"
        else:
            return "Review and address the identified security gap"
