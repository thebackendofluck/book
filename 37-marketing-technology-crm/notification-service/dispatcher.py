# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Notification dispatcher — routes messages to the correct channel
and performs template rendering.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from string import Template as StringTemplate
from typing import Any

from models import Notification, NotificationStatus, NotificationType, Template

logger = logging.getLogger(__name__)


def render_template(body: str, variables: dict[str, Any]) -> str:
    """
    Simple ${variable} substitution compatible with str.Template.
    Falls back to returning the original body if rendering fails.
    """
    try:
        return StringTemplate(body).safe_substitute(variables)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Template rendering error: %s", exc)
        return body


def _send_email(notification: Notification) -> None:
    """Stub: in production, call SES / SendGrid / Mailgun."""
    logger.info(
        "EMAIL → player=%s subject=%s",
        notification.player_id,
        notification.subject,
    )


def _send_sms(notification: Notification) -> None:
    """Stub: in production, call Twilio / Vonage."""
    logger.info("SMS → player=%s body_len=%d", notification.player_id, len(notification.rendered_body))


def _send_push(notification: Notification) -> None:
    """Stub: in production, call FCM / APNs."""
    logger.info("PUSH → player=%s", notification.player_id)


def _send_in_app(notification: Notification) -> None:
    """Stub: in production, write to in-app inbox table / WebSocket."""
    logger.info("IN_APP → player=%s", notification.player_id)


_CHANNEL_HANDLERS = {
    NotificationType.EMAIL: _send_email,
    NotificationType.SMS: _send_sms,
    NotificationType.PUSH: _send_push,
    NotificationType.IN_APP: _send_in_app,
}


def dispatch(notification: Notification) -> Notification:
    """
    Dispatch a notification to the correct channel handler.
    Updates notification.status in place and returns it.
    """
    channel = NotificationType(notification.channel)
    handler = _CHANNEL_HANDLERS.get(channel)
    if handler is None:
        notification.status = NotificationStatus.FAILED
        notification.error_message = f"Unknown channel: {channel}"
        return notification

    try:
        handler(notification)
        notification.status = NotificationStatus.SENT
        notification.sent_at = datetime.now(timezone.utc)
    except Exception as exc:  # noqa: BLE001
        notification.status = NotificationStatus.FAILED
        notification.error_message = str(exc)
        logger.error("Dispatch failed for %s: %s", notification.notification_id, exc)

    return notification


def build_notification_from_template(
    template: Template,
    player_id: str,
    variables: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> Notification:
    """Render a Template into a ready-to-dispatch Notification."""
    rendered_subject = render_template(template.subject or "", variables) or None
    rendered_body = render_template(template.body, variables)

    return Notification(
        player_id=player_id,
        channel=NotificationType(template.channel),
        template_id=template.template_id,
        subject=rendered_subject,
        rendered_body=rendered_body,
        metadata=metadata or {},
    )
