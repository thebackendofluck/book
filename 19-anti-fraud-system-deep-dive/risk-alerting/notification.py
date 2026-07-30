# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
notification.py – Alert dispatch layer for the risk-alerting service.

Supports three outbound channels:
  1. Opsgenie (primary incident-management channel, mirrors OpsgenieApiClient.scala)
  2. Slack    (secondary real-time channel)
  3. Email    (via SMTP or mailgun)
  4. SIGAP    (Brazil regulatory reporting — COAF/SIGAP submission endpoint)

Channel selection is driven by the alert name's associated description
(loaded from the in-memory cache) and environment-level configuration.
"""

from __future__ import annotations

import json
import os
import smtplib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

import httpx

from models import AlertPriority, RiskAlert, SIGAPReport

import structlog
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPSGENIE_API_URL = os.getenv("OPSGENIE_API_URL", "https://api.opsgenie.com/v2/alerts")
OPSGENIE_API_KEY = os.getenv("OPSGENIE_API_KEY", "")
OPSGENIE_RESPONDER_TEAM = os.getenv("OPSGENIE_RESPONDER_TEAM_NAME", "")
OPSGENIE_ENABLED = os.getenv("OPSGENIE_ENABLED", "false").lower() == "true"

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_ENABLED = bool(SLACK_WEBHOOK_URL)

EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "alerts@platform.local")
EMAIL_TO = os.getenv("EMAIL_ALERTS_TO", "compliance@platform.local")
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"

SIGAP_API_URL = os.getenv("SIGAP_API_URL", "")
SIGAP_API_KEY = os.getenv("SIGAP_API_KEY", "")
SIGAP_ENABLED = bool(SIGAP_API_URL)

# Alerts that must always be forwarded to SIGAP
SIGAP_ALERT_NAMES = {
    "TotalAmountOfDepositsIn24Hours",
    "StructuringDDepositLimitIn3Days",
    "TotalWithdrawalExceeded9000In72HoursAlert",
    "SIGAPHighRisk",
}

HTTP_TIMEOUT = float(os.getenv("NOTIFICATION_HTTP_TIMEOUT", "10"))


# ---------------------------------------------------------------------------
# Base channel
# ---------------------------------------------------------------------------


class NotificationChannel(ABC):
    """Abstract base class for a notification channel."""

    @abstractmethod
    def send(self, alert: RiskAlert) -> bool:
        """Send the alert.  Returns True on success."""


# ---------------------------------------------------------------------------
# Opsgenie
# ---------------------------------------------------------------------------


class OpsgenieChannel(NotificationChannel):
    """
    Sends alerts to Opsgenie.

    Mirrors OpsgenieApiClient.scala with responder team injection and
    deduplication via the `alias` field.
    """

    def __init__(
        self,
        api_url: str = OPSGENIE_API_URL,
        api_key: str = OPSGENIE_API_KEY,
        responder_team: str = OPSGENIE_RESPONDER_TEAM,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.responder_team = responder_team

    def _build_payload(self, alert: RiskAlert) -> Dict:
        payload: Dict = {
            "message": alert.message,
            "alias": alert.alias or f"{alert.alert_name}/{'-'.join(alert.user_ids)}",
            "priority": (alert.priority or AlertPriority.P5).value,
            "details": alert.details,
            "tags": alert.tags,
            "source": alert.source or "risk-alerting",
        }
        if self.responder_team:
            payload["responders"] = [{"name": self.responder_team, "type": "team"}]
        if alert.description:
            payload["description"] = alert.description
        return payload

    def send(self, alert: RiskAlert) -> bool:
        if not self.api_key:
            log.warning("Opsgenie API key not configured – skipping alert %s", alert.alert_name)
            return False
        try:
            headers = {
                "Authorization": f"GenieKey {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = self._build_payload(alert)
            resp = httpx.post(self.api_url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            log.info("Opsgenie alert sent: %s (status %s)", alert.alert_name, resp.status_code)
            return True
        except Exception as exc:
            log.error("Opsgenie send failed for %s: %s", alert.alert_name, exc)
            return False


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


class SlackChannel(NotificationChannel):
    """Posts a formatted alert message to a Slack incoming webhook."""

    # Priority → Slack colour sidebar
    _PRIORITY_COLOUR = {
        AlertPriority.P1: "#FF0000",
        AlertPriority.P2: "#FF6600",
        AlertPriority.P3: "#FFCC00",
        AlertPriority.P4: "#00AAFF",
        AlertPriority.P5: "#AAAAAA",
    }

    def __init__(self, webhook_url: str = SLACK_WEBHOOK_URL) -> None:
        self.webhook_url = webhook_url

    def _build_payload(self, alert: RiskAlert) -> Dict:
        colour = self._PRIORITY_COLOUR.get(alert.priority or AlertPriority.P5, "#AAAAAA")
        fields = [
            {"title": k, "value": v, "short": True}
            for k, v in alert.details.items()
        ]
        return {
            "attachments": [
                {
                    "color": colour,
                    "title": f"[{(alert.priority or AlertPriority.P5).value}] {alert.alert_name}",
                    "text": alert.message,
                    "fields": fields,
                    "footer": "risk-alerting",
                    "ts": int(alert.created_at.timestamp()),
                }
            ]
        }

    def send(self, alert: RiskAlert) -> bool:
        if not self.webhook_url:
            return False
        try:
            resp = httpx.post(self.webhook_url, json=self._build_payload(alert), timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Slack send failed for %s: %s", alert.alert_name, exc)
            return False


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


class EmailChannel(NotificationChannel):
    """Sends alert summaries via SMTP."""

    def __init__(
        self,
        host: str = EMAIL_HOST,
        port: int = EMAIL_PORT,
        user: str = EMAIL_USER,
        password: str = EMAIL_PASSWORD,
        from_addr: str = EMAIL_FROM,
        to_addr: str = EMAIL_TO,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr
        self.to_addr = to_addr

    def send(self, alert: RiskAlert) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[{(alert.priority or AlertPriority.P5).value}] Risk Alert: {alert.alert_name}"
            msg["From"] = self.from_addr
            msg["To"] = self.to_addr

            details_text = "\n".join(f"  {k}: {v}" for k, v in alert.details.items())
            body = (
                f"Alert: {alert.alert_name}\n"
                f"Priority: {alert.priority}\n"
                f"Message: {alert.message}\n"
                f"Users: {', '.join(alert.user_ids)}\n"
                f"Details:\n{details_text}\n"
                f"Raised at: {alert.created_at.isoformat()}\n"
            )
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.host, self.port) as server:
                if self.user and self.password:
                    server.starttls()
                    server.login(self.user, self.password)
                server.sendmail(self.from_addr, [self.to_addr], msg.as_string())

            log.info("Email alert sent for %s", alert.alert_name)
            return True
        except Exception as exc:
            log.error("Email send failed for %s: %s", alert.alert_name, exc)
            return False


# ---------------------------------------------------------------------------
# SIGAP (Brazil – COAF regulatory submission)
# ---------------------------------------------------------------------------


class SIGAPChannel(NotificationChannel):
    """
    Submits high-value transaction reports to Brazil's COAF/SIGAP system.

    Only applicable for alerts in SIGAP_ALERT_NAMES and when the player's
    jurisdiction is BR.  The payload conforms to the SIGAPReport model.
    """

    def __init__(
        self,
        api_url: str = SIGAP_API_URL,
        api_key: str = SIGAP_API_KEY,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key

    def _should_submit(self, alert: RiskAlert) -> bool:
        return alert.alert_name in SIGAP_ALERT_NAMES

    def send(self, alert: RiskAlert) -> bool:
        if not self._should_submit(alert):
            return False
        if not self.api_url:
            log.debug("SIGAP endpoint not configured – skipping %s", alert.alert_name)
            return False
        try:
            report = SIGAPReport(
                user_id=alert.user_ids[0] if alert.user_ids else "unknown",
                alert_name=alert.alert_name,
                amount_cents=int(alert.details.get("amount", "0")),
                currency=alert.details.get("currency", "BRL"),
                details=alert.details,
            )
            headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
            resp = httpx.post(
                self.api_url,
                json=report.model_dump(mode="json"),
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            log.info("SIGAP report submitted for alert %s, report %s", alert.alert_name, report.report_id)
            return True
        except Exception as exc:
            log.error("SIGAP submit failed for %s: %s", alert.alert_name, exc)
            return False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class NotificationDispatcher:
    """
    Routes a RiskAlert to all configured notification channels.

    Channels are evaluated in order; failures in one channel do not prevent
    others from being attempted.
    """

    def __init__(
        self,
        channels: Optional[List[NotificationChannel]] = None,
    ) -> None:
        if channels is not None:
            self.channels = channels
        else:
            self.channels = self._build_default_channels()

    @staticmethod
    def _build_default_channels() -> List[NotificationChannel]:
        channels: List[NotificationChannel] = []
        if OPSGENIE_ENABLED:
            channels.append(OpsgenieChannel())
        if SLACK_ENABLED:
            channels.append(SlackChannel())
        if EMAIL_ENABLED:
            channels.append(EmailChannel())
        if SIGAP_ENABLED:
            channels.append(SIGAPChannel())
        if not channels:
            log.warning(
                "No notification channels configured.  "
                "Set OPSGENIE_ENABLED=true, SLACK_WEBHOOK_URL, or EMAIL_ENABLED=true."
            )
        return channels

    def dispatch(self, alert: RiskAlert) -> Dict[str, bool]:
        """
        Dispatch *alert* to all channels.

        Returns a dict mapping channel class name to success boolean.
        Channel failures are caught so remaining channels still run.
        """
        results: Dict[str, bool] = {}
        for channel in self.channels:
            name = type(channel).__name__
            try:
                results[name] = channel.send(alert)
            except Exception:
                log.exception("Channel %s failed to dispatch alert %s", name, alert.alert_name)
                results[name] = False
        return results
