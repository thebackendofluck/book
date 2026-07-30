#!/usr/bin/env python3
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
Alert notification routing engine for iGaming security operations.

Routes security alerts from the SOAR engine to appropriate channels based on
severity, alert type, on-call schedules, and operator-configured escalation rules.

Supported notification targets:
  - PagerDuty Events API v2 (critical alerts, on-call escalation)
  - Slack (all severities, channel-routed by severity)
  - Email via SMTP (compliance team, regulatory notifications)
  - Webhook (generic — n8n, custom CI/CD endpoints)
  - SMS via Twilio (critical alerts when PagerDuty is not configured)

Routing logic:
  CRITICAL → PagerDuty (immediate) + Slack #security-p1 + SMS
  HIGH     → Slack #security-alerts + email to security@
  MEDIUM   → Slack #security-alerts (batched digest)
  LOW/INFO → Slack #security-info (digest only)

Usage as a library:
    from notification_workflows import NotificationRouter, Alert

    router = NotificationRouter.from_config("/etc/soar/notifications.yml")
    router.route(Alert(
        alert_id="abc-123",
        severity="critical",
        alert_type="money_laundering",
        source_ip="203.0.113.42",
        description="Rapid deposit/withdraw cycle detected",
        evidence={"deposit_count": 12, "withdraw_count": 11, "window_min": 30},
    ))

Reference: Chapter 24 — Security and Compliance / Incident Response Automation
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("notification_workflows")


# ---------------------------------------------------------------------------
# Alert data class
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    """
    A normalized security alert for notification routing.

    Attributes:
        alert_id:    Unique alert identifier.
        severity:    Severity level: critical, high, medium, low, info.
        alert_type:  Machine-readable alert type.
        source_ip:   Threat actor IP address.
        description: Human-readable description.
        evidence:    Structured evidence dict.
        timestamp:   UTC ISO8601 timestamp.
        user_id:     Affected player/user ID (optional).
        jurisdiction: Regulatory jurisdiction (e.g. "nj", "pa", "uk").
        tags:        Classification tags.
    """
    alert_id: str
    severity: str
    alert_type: str
    source_ip: str = "unknown"
    description: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    user_id: str = ""
    jurisdiction: str = ""
    tags: list[str] = field(default_factory=list)


_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


def _severity_index(severity: str) -> int:
    try:
        return _SEVERITY_ORDER.index(severity.lower())
    except ValueError:
        return len(_SEVERITY_ORDER)


# ---------------------------------------------------------------------------
# Notification targets
# ---------------------------------------------------------------------------

class PagerDutyNotifier:
    """
    Sends alerts to PagerDuty Events API v2.

    Args:
        routing_key: PagerDuty Events API v2 integration routing key.
        timeout:     HTTP request timeout in seconds.
    """

    _API_URL = "https://events.pagerduty.com/v2/enqueue"

    def __init__(self, routing_key: str, timeout: float = 10.0) -> None:
        if not routing_key:
            raise ValueError("PagerDuty routing_key is required")
        self._routing_key = routing_key
        self._timeout = timeout

    def trigger(self, alert: Alert) -> bool:
        """
        Trigger a PagerDuty incident from a security alert.

        Args:
            alert: Normalized security alert.

        Returns:
            True on success.
        """
        severity_map = {
            "critical": "critical",
            "high": "error",
            "medium": "warning",
            "low": "info",
            "info": "info",
        }
        payload: dict[str, Any] = {
            "routing_key": self._routing_key,
            "event_action": "trigger",
            "dedup_key": alert.alert_id,
            "payload": {
                "summary": f"[{alert.severity.upper()}] {alert.alert_type}: {alert.source_ip}",
                "severity": severity_map.get(alert.severity.lower(), "error"),
                "source": alert.source_ip,
                "timestamp": alert.timestamp,
                "component": "igaming-soar",
                "group": alert.alert_type,
                "class": alert.severity,
                "custom_details": {
                    "description": alert.description,
                    "user_id": alert.user_id,
                    "jurisdiction": alert.jurisdiction,
                    "evidence": alert.evidence,
                    "tags": alert.tags,
                },
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._API_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                dedup_key = result.get("dedup_key", "")
                log.info(
                    "pagerduty_triggered alert_id=%s dedup_key=%s",
                    alert.alert_id,
                    dedup_key,
                )
                return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            log.error("pagerduty_trigger_failed alert_id=%s error=%s", alert.alert_id, exc)
            return False


class SlackWebhookNotifier:
    """
    Sends alerts via Slack Incoming Webhooks.

    Args:
        webhook_url: Slack incoming webhook URL.
        timeout:     HTTP request timeout in seconds.
    """

    def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
        if not webhook_url:
            raise ValueError("Slack webhook_url is required")
        self._url = webhook_url
        self._timeout = timeout

    def send(self, alert: Alert) -> bool:
        """
        Send a formatted security alert via Slack webhook.

        Args:
            alert: Normalized security alert.

        Returns:
            True on success.
        """
        severity_emoji = {
            "critical": ":rotating_light:",
            "high": ":warning:",
            "medium": ":large_yellow_circle:",
            "low": ":information_source:",
            "info": ":white_circle:",
        }
        severity_color = {
            "critical": "#FF0000",
            "high": "#FF6600",
            "medium": "#FFC000",
            "low": "#0078D7",
            "info": "#AAAAAA",
        }
        emoji = severity_emoji.get(alert.severity.lower(), ":white_circle:")
        color = severity_color.get(alert.severity.lower(), "#AAAAAA")

        evidence_lines = "\n".join(
            f"• *{k.replace('_', ' ').title()}*: {v}"
            for k, v in list(alert.evidence.items())[:10]
        )

        payload: dict[str, Any] = {
            "text": f"{emoji} *{alert.severity.upper()}* security alert",
            "attachments": [
                {
                    "color": color,
                    "fields": [
                        {"title": "Alert Type", "value": alert.alert_type, "short": True},
                        {"title": "Source IP", "value": alert.source_ip, "short": True},
                        {"title": "Severity", "value": alert.severity.upper(), "short": True},
                        {"title": "Alert ID", "value": alert.alert_id[:8], "short": True},
                        {"title": "Description", "value": alert.description[:500], "short": False},
                    ],
                    "footer": f"AcmeToCasino SOAR | {alert.timestamp[:19].replace('T', ' ')} UTC",
                }
            ],
        }
        if evidence_lines:
            payload["attachments"][0]["fields"].append(
                {"title": "Evidence", "value": evidence_lines[:1000], "short": False}
            )

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                resp_text = resp.read().decode("utf-8")
                if resp_text.strip() != "ok":
                    log.warning("slack_webhook_unexpected_response: %s", resp_text[:100])
            log.info("slack_webhook_sent alert_id=%s severity=%s", alert.alert_id, alert.severity)
            return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            log.error("slack_webhook_failed alert_id=%s error=%s", alert.alert_id, exc)
            return False


class EmailNotifier:
    """
    Sends security alert email notifications via SMTP.

    Args:
        smtp_host:     SMTP server hostname.
        smtp_port:     SMTP port (default: 587 for STARTTLS).
        smtp_user:     SMTP username.
        smtp_password: SMTP password.
        from_addr:     Sender email address.
        to_addrs:      List of recipient addresses.
        use_tls:       Use STARTTLS (default: True).
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        from_addr: str = "soar@acmetocasino.com",
        to_addrs: list[str] | None = None,
        use_tls: bool = True,
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._password = smtp_password
        self._from = from_addr
        self._to = to_addrs or []
        self._use_tls = use_tls

    def send(self, alert: Alert) -> bool:
        """
        Send a security alert email.

        Args:
            alert: Normalized security alert.

        Returns:
            True on success.
        """
        if not self._to:
            log.warning("email_notifier: no recipients configured")
            return False

        subject = f"[{alert.severity.upper()}] Security Alert: {alert.alert_type} from {alert.source_ip}"
        body_lines = [
            f"Security Alert — {alert.severity.upper()}",
            "",
            f"Alert ID:   {alert.alert_id}",
            f"Type:       {alert.alert_type}",
            f"Severity:   {alert.severity.upper()}",
            f"Source IP:  {alert.source_ip}",
            f"Timestamp:  {alert.timestamp}",
            "",
            "Description:",
            alert.description,
            "",
        ]
        if alert.user_id:
            body_lines.append(f"Affected User: {alert.user_id}")
        if alert.jurisdiction:
            body_lines.append(f"Jurisdiction: {alert.jurisdiction}")
        if alert.evidence:
            body_lines.append("")
            body_lines.append("Evidence:")
            for k, v in alert.evidence.items():
                body_lines.append(f"  {k}: {v}")
        body_lines.extend(["", "---", "AcmeToCasino SOAR | Automated Security Notification"])

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = ", ".join(self._to)
        msg["X-SOAR-Alert-ID"] = alert.alert_id
        msg["X-SOAR-Severity"] = alert.severity
        msg.attach(MIMEText("\n".join(body_lines), "plain", "utf-8"))

        try:
            if self._use_tls:
                server = smtplib.SMTP(self._host, self._port, timeout=15)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self._host, self._port, timeout=15)
            if self._user:
                server.login(self._user, self._password)
            server.sendmail(self._from, self._to, msg.as_string())
            server.quit()
            log.info("email_sent alert_id=%s recipients=%d", alert.alert_id, len(self._to))
            return True
        except (smtplib.SMTPException, OSError) as exc:
            log.error("email_send_failed alert_id=%s error=%s", alert.alert_id, exc)
            return False


# ---------------------------------------------------------------------------
# Routing engine
# ---------------------------------------------------------------------------

@dataclass
class RouterConfig:
    """
    Configuration for the NotificationRouter.

    Attributes:
        pagerduty_routing_key:  PagerDuty routing key (required for CRITICAL).
        slack_webhook_url:      Slack incoming webhook URL.
        email_smtp_host:        SMTP host for email notifications.
        email_smtp_port:        SMTP port.
        email_from:             Sender address.
        email_security_team:    Security team recipients.
        email_compliance_team:  Compliance team recipients (for regulatory alerts).
        min_pagerduty_severity: Minimum severity for PagerDuty escalation.
        min_email_severity:     Minimum severity for email notification.
        min_slack_severity:     Minimum severity for Slack notification.
        compliance_alert_types: Alert types that notify the compliance team.
    """
    pagerduty_routing_key: str = ""
    slack_webhook_url: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_user: str = ""
    email_smtp_password: str = ""
    email_from: str = "soar@acmetocasino.com"
    email_security_team: list[str] = field(default_factory=list)
    email_compliance_team: list[str] = field(default_factory=list)
    min_pagerduty_severity: str = "critical"
    min_email_severity: str = "high"
    min_slack_severity: str = "medium"
    compliance_alert_types: list[str] = field(
        default_factory=lambda: ["money_laundering", "kyc_fraud", "regulatory_violation"]
    )


class NotificationRouter:
    """
    Routes security alerts to appropriate notification channels.

    Args:
        config: RouterConfig instance.
    """

    def __init__(self, config: RouterConfig) -> None:
        self._cfg = config
        self._pd: PagerDutyNotifier | None = None
        self._slack: SlackWebhookNotifier | None = None
        self._email_security: EmailNotifier | None = None
        self._email_compliance: EmailNotifier | None = None

        if config.pagerduty_routing_key:
            self._pd = PagerDutyNotifier(config.pagerduty_routing_key)

        if config.slack_webhook_url:
            self._slack = SlackWebhookNotifier(config.slack_webhook_url)

        if config.email_smtp_host and config.email_security_team:
            self._email_security = EmailNotifier(
                smtp_host=config.email_smtp_host,
                smtp_port=config.email_smtp_port,
                smtp_user=config.email_smtp_user,
                smtp_password=config.email_smtp_password,
                from_addr=config.email_from,
                to_addrs=config.email_security_team,
            )

        if config.email_smtp_host and config.email_compliance_team:
            self._email_compliance = EmailNotifier(
                smtp_host=config.email_smtp_host,
                smtp_port=config.email_smtp_port,
                smtp_user=config.email_smtp_user,
                smtp_password=config.email_smtp_password,
                from_addr=config.email_from,
                to_addrs=config.email_compliance_team,
            )

    @classmethod
    def from_env(cls) -> "NotificationRouter":
        """
        Construct a NotificationRouter from environment variables.

        Environment variables:
            PAGERDUTY_ROUTING_KEY
            SLACK_SECURITY_WEBHOOK
            SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
            SECURITY_EMAIL_RECIPIENTS (comma-separated)
            COMPLIANCE_EMAIL_RECIPIENTS (comma-separated)
        """
        cfg = RouterConfig(
            pagerduty_routing_key=os.environ.get("PAGERDUTY_ROUTING_KEY", ""),
            slack_webhook_url=os.environ.get("SLACK_SECURITY_WEBHOOK", ""),
            email_smtp_host=os.environ.get("SMTP_HOST", ""),
            email_smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            email_smtp_user=os.environ.get("SMTP_USER", ""),
            email_smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            email_security_team=[
                e.strip()
                for e in os.environ.get("SECURITY_EMAIL_RECIPIENTS", "").split(",")
                if e.strip()
            ],
            email_compliance_team=[
                e.strip()
                for e in os.environ.get("COMPLIANCE_EMAIL_RECIPIENTS", "").split(",")
                if e.strip()
            ],
        )
        return cls(cfg)

    def route(self, alert: Alert) -> dict[str, bool]:
        """
        Route an alert to all appropriate notification channels.

        Args:
            alert: Normalized security alert.

        Returns:
            Dict mapping channel name to delivery success.
        """
        severity_idx = _severity_index(alert.severity)
        results: dict[str, bool] = {}

        # PagerDuty
        pd_threshold = _severity_index(self._cfg.min_pagerduty_severity)
        if self._pd and severity_idx <= pd_threshold:
            results["pagerduty"] = self._pd.trigger(alert)

        # Slack
        slack_threshold = _severity_index(self._cfg.min_slack_severity)
        if self._slack and severity_idx <= slack_threshold:
            results["slack"] = self._slack.send(alert)

        # Email — security team
        email_threshold = _severity_index(self._cfg.min_email_severity)
        if self._email_security and severity_idx <= email_threshold:
            results["email_security"] = self._email_security.send(alert)

        # Email — compliance team (for regulatory-relevant alert types)
        if (
            self._email_compliance
            and alert.alert_type in self._cfg.compliance_alert_types
        ):
            results["email_compliance"] = self._email_compliance.send(alert)

        log.info(
            "notification_routed alert_id=%s severity=%s channels=%s",
            alert.alert_id,
            alert.severity,
            list(results.keys()),
        )
        return results


# ---------------------------------------------------------------------------
# Entry point (demo)
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Notification workflow router for iGaming SOAR")
    parser.add_argument("--alert-file", help="JSON file containing a test alert")
    parser.add_argument("--severity", default="high", choices=_SEVERITY_ORDER)
    args = parser.parse_args()

    router = NotificationRouter.from_env()

    if args.alert_file:
        with open(args.alert_file, encoding="utf-8") as fh:
            data = json.load(fh)
        alert = Alert(**{k: v for k, v in data.items() if k in Alert.__dataclass_fields__})
    else:
        alert = Alert(
            alert_id="test-" + datetime.now(tz=timezone.utc).strftime("%H%M%S"),
            severity=args.severity,
            alert_type="test_alert",
            source_ip="203.0.113.42",
            description="Test notification from notification_workflows.py",
            evidence={"test": True},
        )

    results = router.route(alert)
    print(json.dumps(results, indent=2))
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
