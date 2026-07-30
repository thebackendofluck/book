# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Outbox Service
# Source: Production casino platform (sanitized)
# Chapter 35 - Incident Management
#
# Polls the database for pending alerts and dispatches them via the
# mailer client. Uses an asyncio-based polling loop with 10-second
# intervals to match the original fs2 Stream.metered(10.seconds) behaviour.
# =============================================================================

from __future__ import annotations

import asyncio
import logging

import httpx

from models import Alert, AlertStatus, MailerAlertParams, MailerRequest
from repository import AlertRepository, AlertTypeRepository, EmailAddressRepository

logger = logging.getLogger(__name__)


class MailerClient:
    """HTTP client for the internal mailer service."""

    def __init__(self, base_url: str, alerts_path: str) -> None:
        self._url = f"{base_url.rstrip('/')}/{alerts_path.lstrip('/')}"

    async def send_alert(self, request: MailerRequest) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self._url, json=request.model_dump())
            return resp.status_code in (200, 201, 202)


class OutboxService:
    """
    Polls the database for pending alerts and dispatches them via the
    mailer client, then updates the alert status to Sent or Error.

    The poll loop runs every 10 seconds (configurable via POLL_INTERVAL_SECONDS).
    """

    POLL_INTERVAL_SECONDS = 10

    def __init__(
        self,
        alert_repository: AlertRepository,
        alert_type_repository: AlertTypeRepository,
        email_address_repository: EmailAddressRepository,
        mailer_client: MailerClient,
    ) -> None:
        self._alerts = alert_repository
        self._alert_types = alert_type_repository
        self._email_addresses = email_address_repository
        self._mailer = mailer_client

    async def process_alert(self, alert: Alert) -> None:
        if alert.id is None:
            return
        try:
            await self._send_alert(alert)
            self._alerts.update_status(alert.id, AlertStatus.SENT)
        except Exception as exc:
            logger.error("could not send alert id=%s: %s", alert.id, exc)
            self._alerts.update_status(alert.id, AlertStatus.ERROR)

    async def _send_alert(self, alert: Alert) -> None:
        alert_type = self._alert_types.find_alert_type_by_name(alert.alert_type)
        if alert_type is None:
            raise ValueError(f"alert type {alert.alert_type!r} not found")

        email_address = self._email_addresses.find_address_by_name_and_jurisdiction(
            alert_type.recipient, alert_type.jurisdiction_id
        )
        if email_address is None:
            raise ValueError(
                f"no email address found for recipient {alert_type.recipient!r} "
                f"jurisdiction {alert_type.jurisdiction_id!r}"
            )

        request = MailerRequest(
            template_name=alert_type.template_name,
            params=MailerAlertParams(
                recipient=email_address.address,
                user_id=alert.user_id,
                brand_id=alert.brand_id,
                params=alert.params,
            ),
        )
        ok = await self._mailer.send_alert(request)
        if not ok:
            raise RuntimeError("mailer returned non-2xx response")

    async def poll_and_dispatch(self) -> None:
        """One poll cycle: fetch pending alerts and process each one."""
        alerts = self._alerts.find_alerts_to_send()
        logger.debug("Outbox poll: found %d pending alerts", len(alerts))
        for alert in alerts:
            await self.process_alert(alert)

    async def run_forever(self) -> None:
        """Infinite poll loop with POLL_INTERVAL_SECONDS between cycles."""
        logger.info("OutboxService started (interval=%ds)", self.POLL_INTERVAL_SECONDS)
        while True:
            try:
                await self.poll_and_dispatch()
            except Exception as exc:
                logger.error("OutboxService poll error: %s", exc)
            await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
