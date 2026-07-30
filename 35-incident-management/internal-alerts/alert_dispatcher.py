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
alert_dispatcher.py – Routes internal operational alerts to the correct channels.

Mirrors the Scala OutboxService + MailerClient logic:
  1. Look up the AlertType configuration for the alert
  2. Resolve the recipient email address by name + jurisdiction
  3. Send via email (template-based mailer) and/or Slack
  4. Update alert status (Sent / Error)

Channel routing logic:
  - Email:    all alert types with a configured email recipient
  - Slack:    alert types with a slack_channel field set
  - PagerDuty: alert types tagged severity=critical
"""

from __future__ import annotations

import logging
import os
import smtplib
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import httpx

from models import (
    Alert,
    AlertStatus,
    AlertType,
    EmailAddress,
    MailerAlertParams,
    MailerRequest,
    MailerResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAILER_BASE_URI = os.getenv("MAILER_BASE_URI", "http://mailer-service:8080")
MAILER_ALERTS_PATH = os.getenv("MAILER_ALERTS_PATH", "/api/v1/send")
HTTP_TIMEOUT = float(os.getenv("NOTIFICATION_HTTP_TIMEOUT", "10"))

SLACK_WEBHOOK_URL = os.getenv("INTERNAL_ALERTS_SLACK_WEBHOOK", "")
SLACK_ENABLED = bool(SLACK_WEBHOOK_URL)

PAGERDUTY_ROUTING_KEY = os.getenv("PAGERDUTY_ROUTING_KEY", "")
PAGERDUTY_API_URL = "https://events.pagerduty.com/v2/enqueue"
PAGERDUTY_ENABLED = bool(PAGERDUTY_ROUTING_KEY)

# Alert types whose severity warrants PagerDuty escalation
PAGERDUTY_ALERT_TYPES = set(
    os.getenv("PAGERDUTY_ALERT_TYPES", "service_down,database_error,payment_gateway_down").split(",")
)


# ---------------------------------------------------------------------------
# Repository ports (injected by caller)
# ---------------------------------------------------------------------------


class AlertRepository(ABC):
    @abstractmethod
    def update_status(self, alert: Alert, status: AlertStatus) -> None: ...

    @abstractmethod
    def find_alerts_to_send(self) -> List[Alert]: ...

    @abstractmethod
    def create_alert(self, alert: Alert) -> Optional[int]: ...


class AlertTypeRepository(ABC):
    @abstractmethod
    def find_by_name(self, name: str) -> Optional[AlertType]: ...


class EmailAddressRepository(ABC):
    @abstractmethod
    def find_by_name_and_jurisdiction(
        self, name: str, jurisdiction_id: Optional[str]
    ) -> Optional[EmailAddress]: ...


# ---------------------------------------------------------------------------
# Channel abstraction
# ---------------------------------------------------------------------------


class DispatchChannel(ABC):
    @abstractmethod
    def send(self, alert: Alert, alert_type: AlertType, recipient: EmailAddress) -> bool:
        """Return True on success."""


# ---------------------------------------------------------------------------
# Mailer channel (template-based email)
# ---------------------------------------------------------------------------


class MailerChannel(DispatchChannel):
    """
    Sends alerts via an external mailer micro-service using named templates.
    Mirrors MailerClient.scala.
    """

    def __init__(
        self,
        base_uri: str = MAILER_BASE_URI,
        alerts_path: str = MAILER_ALERTS_PATH,
    ) -> None:
        self.url = f"{base_uri.rstrip('/')}{alerts_path}"

    def send(self, alert: Alert, alert_type: AlertType, recipient: EmailAddress) -> bool:
        params = MailerAlertParams(
            recipient=recipient.address,
            user_id=alert.user_id,
            brand_id=alert.brand_id,
            params=alert.params,
        )
        request = MailerRequest(template_name=alert_type.template_name, params=params)
        try:
            resp = httpx.post(
                f"{self.url}/{alert_type.template_name}",
                json=request.model_dump(mode="json"),
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            result = MailerResponse(**resp.json())
            if not result.status:
                logger.warning("Mailer returned failure for alert %s: %s", alert.id, result.message)
                return False
            return True
        except Exception as exc:
            logger.error("Mailer send failed for alert %s: %s", alert.id, exc)
            return False


# ---------------------------------------------------------------------------
# Slack channel
# ---------------------------------------------------------------------------


class SlackChannel(DispatchChannel):
    """Posts a Slack notification for each internal alert."""

    def __init__(self, webhook_url: str = SLACK_WEBHOOK_URL) -> None:
        self.webhook_url = webhook_url

    def send(self, alert: Alert, alert_type: AlertType, recipient: EmailAddress) -> bool:
        if not self.webhook_url:
            return False
        payload = {
            "text": f":bell: *Internal Alert*: `{alert.alert_type}`",
            "attachments": [
                {
                    "color": "#FF6600",
                    "fields": [
                        {"title": "Type", "value": alert.alert_type, "short": True},
                        {"title": "User ID", "value": str(alert.user_id or "N/A"), "short": True},
                        {"title": "Template", "value": alert_type.template_name, "short": True},
                        {"title": "Recipient", "value": recipient.address, "short": True},
                    ],
                }
            ],
        }
        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("Slack send failed for alert %s: %s", alert.id, exc)
            return False


# ---------------------------------------------------------------------------
# PagerDuty channel
# ---------------------------------------------------------------------------


class PagerDutyChannel(DispatchChannel):
    """Escalates critical alerts to PagerDuty via the Events API v2."""

    def __init__(
        self,
        routing_key: str = PAGERDUTY_ROUTING_KEY,
        critical_types: set = PAGERDUTY_ALERT_TYPES,
    ) -> None:
        self.routing_key = routing_key
        self.critical_types = critical_types

    def send(self, alert: Alert, alert_type: AlertType, recipient: EmailAddress) -> bool:
        if alert.alert_type not in self.critical_types:
            return False  # not applicable for this alert type
        if not self.routing_key:
            logger.debug("PagerDuty routing key not configured; skipping %s", alert.alert_type)
            return False
        payload = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "dedup_key": f"{alert.alert_type}/{alert.user_id}/{alert.brand_id}",
            "payload": {
                "summary": f"Internal alert: {alert.alert_type}",
                "severity": "critical",
                "source": "internal-alerts",
                "custom_details": alert.params or {},
            },
        }
        try:
            resp = httpx.post(PAGERDUTY_API_URL, json=payload, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("PagerDuty send failed for alert %s: %s", alert.id, exc)
            return False


# ---------------------------------------------------------------------------
# Alert Dispatcher
# ---------------------------------------------------------------------------


class AlertDispatcher:
    """
    Orchestrates alert delivery across all configured channels.

    Mirrors the OutboxService.sendAlert() method in Scala but extended with
    multi-channel routing (email, Slack, PagerDuty).

    Usage
    -----
    dispatcher = AlertDispatcher(
        alert_repo=...,
        alert_type_repo=...,
        email_repo=...,
    )
    dispatcher.process_alert(alert)
    """

    def __init__(
        self,
        alert_repo: AlertRepository,
        alert_type_repo: AlertTypeRepository,
        email_repo: EmailAddressRepository,
        channels: Optional[List[DispatchChannel]] = None,
    ) -> None:
        self._alert_repo = alert_repo
        self._alert_type_repo = alert_type_repo
        self._email_repo = email_repo
        self._channels = channels if channels is not None else self._default_channels()

    @staticmethod
    def _default_channels() -> List[DispatchChannel]:
        channels: List[DispatchChannel] = [MailerChannel()]
        if SLACK_ENABLED:
            channels.append(SlackChannel())
        if PAGERDUTY_ENABLED:
            channels.append(PagerDutyChannel())
        return channels

    def process_alert(self, alert: Alert) -> None:
        """
        Deliver one alert to all applicable channels and update its status.
        """
        try:
            self._send_alert(alert)
            self._alert_repo.update_status(alert, AlertStatus.SENT)
        except Exception as exc:
            logger.error("Failed to process alert %s (%s): %s", alert.id, alert.alert_type, exc)
            self._alert_repo.update_status(alert, AlertStatus.ERROR)

    def _send_alert(self, alert: Alert) -> None:
        alert_type = self._alert_type_repo.find_by_name(alert.alert_type)
        if alert_type is None:
            raise ValueError(f"Unknown alert type: {alert.alert_type}")

        email_addr = self._email_repo.find_by_name_and_jurisdiction(
            alert_type.recipient, alert_type.jurisdiction_id
        )
        if email_addr is None:
            raise ValueError(
                f"No email address for recipient={alert_type.recipient} "
                f"jurisdiction={alert_type.jurisdiction_id}"
            )

        success = False
        for channel in self._channels:
            try:
                ok = channel.send(alert, alert_type, email_addr)
                if ok:
                    success = True
            except Exception as exc:
                logger.warning(
                    "Channel %s failed for alert %s: %s",
                    type(channel).__name__, alert.id, exc,
                )

        if not success:
            raise RuntimeError(f"All channels failed for alert {alert.id}")

    def process_pending(self) -> int:
        """
        Poll the database for pending alerts and dispatch them.
        Returns the count of successfully processed alerts.
        """
        pending = self._alert_repo.find_alerts_to_send()
        processed = 0
        for alert in pending:
            try:
                self.process_alert(alert)
                processed += 1
            except Exception as exc:
                logger.error("Error processing alert %s: %s", alert.id, exc)
        return processed
