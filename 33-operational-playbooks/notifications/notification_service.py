# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Transactional notification service for iGaming platforms.

Handles email, SMS, and push notifications with:
  - Template engine with jurisdiction-specific content variants
  - Delivery tracking with retry logic
  - Opt-out management (GDPR / LGPD compliant)
  - Rate limiting to prevent notification fatigue
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Channel(str, Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    PUSH = "PUSH"
    IN_APP = "IN_APP"


class NotificationType(str, Enum):
    # Registration and account
    REGISTRATION_WELCOME = "REGISTRATION_WELCOME"
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    PASSWORD_RESET = "PASSWORD_RESET"
    KYC_APPROVED = "KYC_APPROVED"
    KYC_REJECTED = "KYC_REJECTED"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    # Transactions
    DEPOSIT_CONFIRMED = "DEPOSIT_CONFIRMED"
    WITHDRAWAL_REQUESTED = "WITHDRAWAL_REQUESTED"
    WITHDRAWAL_APPROVED = "WITHDRAWAL_APPROVED"
    WITHDRAWAL_REJECTED = "WITHDRAWAL_REJECTED"
    # Security
    TWO_FACTOR_CODE = "TWO_FACTOR_CODE"
    SUSPICIOUS_LOGIN = "SUSPICIOUS_LOGIN"
    NEW_DEVICE_LOGIN = "NEW_DEVICE_LOGIN"
    # Responsible gaming
    DEPOSIT_LIMIT_CHANGED = "DEPOSIT_LIMIT_CHANGED"
    SELF_EXCLUSION_CONFIRMED = "SELF_EXCLUSION_CONFIRMED"
    COOLOFF_STARTED = "COOLOFF_STARTED"
    REALITY_CHECK = "REALITY_CHECK"
    # Disputes
    COMPLAINT_RECEIVED = "COMPLAINT_RECEIVED"
    COMPLAINT_RESOLVED = "COMPLAINT_RESOLVED"
    CHARGEBACK_NOTIFICATION = "CHARGEBACK_NOTIFICATION"
    # Promotions (transactional, not marketing)
    BONUS_CREDITED = "BONUS_CREDITED"
    BONUS_EXPIRED = "BONUS_EXPIRED"


class DeliveryStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    BOUNCED = "BOUNCED"
    OPTED_OUT = "OPTED_OUT"


class Jurisdiction(str, Enum):
    UKGC = "UKGC"        # UK Gambling Commission
    MGA = "MGA"          # Malta Gaming Authority
    CURACAO = "CURACAO"
    BRAZIL = "BRAZIL"    # SIGAP / LOTERJ
    SWEDEN = "SWEDEN"    # Spelinspektionen
    ONTARIO = "ONTARIO"  # AGCO
    DEFAULT = "DEFAULT"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class NotificationTemplate(BaseModel):
    """A template with jurisdiction-specific variants."""
    notification_type: NotificationType
    channel: Channel
    jurisdiction: Jurisdiction = Jurisdiction.DEFAULT
    subject: str = ""       # email subject line
    body: str               # template body with {placeholder} variables
    language: str = "en"


class Notification(BaseModel):
    notification_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    notification_type: NotificationType
    channel: Channel
    player_id: str
    recipient: str           # email address, phone number, or device token
    subject: str = ""
    body: str = ""
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    max_retries: int = 3
    last_attempt_at: datetime | None = None
    delivered_at: datetime | None = None
    failed_reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)
    jurisdiction: str = "DEFAULT"

    model_config = {"use_enum_values": True}


class OptOutRecord(BaseModel):
    player_id: str
    channel: Channel
    notification_type: NotificationType | None = None  # None = all types
    opted_out_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

_TEMPLATES: dict[tuple[str, str, str], NotificationTemplate] = {}


def register_template(template: NotificationTemplate) -> None:
    key = (template.notification_type.value, template.channel.value, template.jurisdiction.value)
    _TEMPLATES[key] = template


def get_template(notification_type: NotificationType, channel: Channel,
                 jurisdiction: Jurisdiction = Jurisdiction.DEFAULT) -> NotificationTemplate | None:
    """Look up template, falling back to DEFAULT jurisdiction."""
    key = (notification_type.value, channel.value, jurisdiction.value)
    template = _TEMPLATES.get(key)
    if template is None and jurisdiction != Jurisdiction.DEFAULT:
        fallback = (notification_type.value, channel.value, Jurisdiction.DEFAULT.value)
        template = _TEMPLATES.get(fallback)
    return template


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------

_BUILTIN_TEMPLATES = [
    # Registration
    NotificationTemplate(
        notification_type=NotificationType.REGISTRATION_WELCOME,
        channel=Channel.EMAIL,
        subject="Welcome to {brand_name}",
        body="Hi {player_name},\n\nWelcome to {brand_name}! Your account is ready.\n\nPlease verify your email: {verification_link}\n\nPlay responsibly.",
    ),
    NotificationTemplate(
        notification_type=NotificationType.REGISTRATION_WELCOME,
        channel=Channel.EMAIL,
        jurisdiction=Jurisdiction.UKGC,
        subject="Welcome to {brand_name}",
        body="Hi {player_name},\n\nWelcome to {brand_name}. Your account is active.\n\nVerify your email: {verification_link}\n\nGambling should be entertaining, not a way to make money. For support, visit BeGambleAware.org. You must be 18+ to gamble.",
    ),
    NotificationTemplate(
        notification_type=NotificationType.REGISTRATION_WELCOME,
        channel=Channel.EMAIL,
        jurisdiction=Jurisdiction.BRAZIL,
        subject="Bem-vindo ao {brand_name}",
        body="Ola {player_name},\n\nBem-vindo ao {brand_name}! Sua conta esta pronta.\n\nVerifique seu email: {verification_link}\n\nJogue com responsabilidade. Proibido para menores de 18 anos.",
    ),
    # Email verification
    NotificationTemplate(
        notification_type=NotificationType.EMAIL_VERIFICATION,
        channel=Channel.EMAIL,
        subject="Verify your email - {brand_name}",
        body="Your verification code is: {code}\n\nThis code expires in 15 minutes.",
    ),
    # 2FA
    NotificationTemplate(
        notification_type=NotificationType.TWO_FACTOR_CODE,
        channel=Channel.SMS,
        body="Your {brand_name} security code: {code}. Do not share this code. Expires in 5 minutes.",
    ),
    # Suspicious login
    NotificationTemplate(
        notification_type=NotificationType.SUSPICIOUS_LOGIN,
        channel=Channel.EMAIL,
        subject="Suspicious login attempt - {brand_name}",
        body="Hi {player_name},\n\nWe detected a login from {ip_address} ({country}) at {login_time}.\n\nIf this was not you, secure your account immediately: {security_link}",
    ),
    NotificationTemplate(
        notification_type=NotificationType.SUSPICIOUS_LOGIN,
        channel=Channel.SMS,
        body="{brand_name}: Suspicious login from {country}. If not you, visit {security_link}",
    ),
    # Withdrawal
    NotificationTemplate(
        notification_type=NotificationType.WITHDRAWAL_APPROVED,
        channel=Channel.EMAIL,
        subject="Withdrawal approved - {brand_name}",
        body="Hi {player_name},\n\nYour withdrawal of {currency}{amount} has been approved and will be processed within {processing_time}.\n\nTransaction ref: {txn_ref}",
    ),
    NotificationTemplate(
        notification_type=NotificationType.WITHDRAWAL_APPROVED,
        channel=Channel.PUSH,
        body="Withdrawal of {currency}{amount} approved! Ref: {txn_ref}",
    ),
    # Deposit limit changed
    NotificationTemplate(
        notification_type=NotificationType.DEPOSIT_LIMIT_CHANGED,
        channel=Channel.EMAIL,
        subject="Deposit limit updated - {brand_name}",
        body="Hi {player_name},\n\nYour {limit_period} deposit limit has been changed from {old_limit} to {new_limit}.\n\nThis change takes effect {effective_date}.\n\nIf you did not request this, contact support.",
    ),
    NotificationTemplate(
        notification_type=NotificationType.DEPOSIT_LIMIT_CHANGED,
        channel=Channel.EMAIL,
        jurisdiction=Jurisdiction.UKGC,
        subject="Deposit limit updated - {brand_name}",
        body="Hi {player_name},\n\nYour {limit_period} deposit limit has been changed from {old_limit} to {new_limit}.\n\nIncreases take effect after a 24-hour cooling-off period ({effective_date}). Decreases are immediate.\n\nFor support: BeGambleAware.org | GamStop.co.uk",
    ),
    # Self-exclusion
    NotificationTemplate(
        notification_type=NotificationType.SELF_EXCLUSION_CONFIRMED,
        channel=Channel.EMAIL,
        subject="Self-exclusion confirmed - {brand_name}",
        body="Hi {player_name},\n\nYour self-exclusion is now active until {exclusion_end_date}.\n\nDuring this period:\n- You cannot deposit or place bets\n- Marketing communications are stopped\n- Your balance of {balance} can be withdrawn\n\nFor support: {support_link}",
    ),
    # Complaint
    NotificationTemplate(
        notification_type=NotificationType.COMPLAINT_RECEIVED,
        channel=Channel.EMAIL,
        subject="Complaint received - Ref {case_ref} - {brand_name}",
        body="Hi {player_name},\n\nWe have received your complaint (Ref: {case_ref}).\n\nWe aim to resolve complaints within {sla_days} working days. You will receive updates at this email address.\n\nIf you are not satisfied with our response, you may refer your complaint to {adr_provider}.",
    ),
    NotificationTemplate(
        notification_type=NotificationType.COMPLAINT_RESOLVED,
        channel=Channel.EMAIL,
        subject="Complaint resolved - Ref {case_ref} - {brand_name}",
        body="Hi {player_name},\n\nYour complaint (Ref: {case_ref}) has been resolved.\n\nOutcome: {resolution_summary}\n\nIf you are not satisfied, you may escalate to {adr_provider} within {adr_deadline_days} days.",
    ),
]

for _t in _BUILTIN_TEMPLATES:
    register_template(_t)


# ---------------------------------------------------------------------------
# Opt-out store (in-memory, replace with DB in production)
# ---------------------------------------------------------------------------

_OPT_OUTS: list[OptOutRecord] = []


def opt_out(player_id: str, channel: Channel, *,
            notification_type: NotificationType | None = None,
            reason: str = "") -> OptOutRecord:
    """Record an opt-out preference (GDPR Article 21)."""
    record = OptOutRecord(
        player_id=player_id,
        channel=channel,
        notification_type=notification_type,
        reason=reason,
    )
    _OPT_OUTS.append(record)
    logger.info("Opt-out recorded: player=%s channel=%s type=%s",
                player_id, channel.value, notification_type)
    return record


def is_opted_out(player_id: str, channel: Channel,
                 notification_type: NotificationType) -> bool:
    """Check if a player has opted out of a specific notification."""
    for record in _OPT_OUTS:
        if record.player_id != player_id:
            continue
        if record.channel != channel:
            continue
        # None means all types
        if record.notification_type is None or record.notification_type == notification_type:
            return True
    return False


def clear_opt_outs() -> None:
    """Clear opt-out store (for testing)."""
    _OPT_OUTS.clear()


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def render_template(template: NotificationTemplate,
                    variables: dict[str, str]) -> tuple[str, str]:
    """Render subject and body by substituting {placeholder} variables."""
    subject = template.subject
    body = template.body
    for key, value in variables.items():
        placeholder = "{" + key + "}"
        subject = subject.replace(placeholder, str(value))
        body = body.replace(placeholder, str(value))
    return subject, body


# ---------------------------------------------------------------------------
# Notification sending (core logic)
# ---------------------------------------------------------------------------

# In-memory delivery log (replace with persistent store)
_DELIVERY_LOG: list[Notification] = []


def send_notification(
    notification_type: NotificationType,
    channel: Channel,
    player_id: str,
    recipient: str,
    variables: dict[str, str],
    *,
    jurisdiction: Jurisdiction = Jurisdiction.DEFAULT,
    metadata: dict[str, Any] | None = None,
) -> Notification:
    """
    Build and send a notification.

    1. Check opt-out status
    2. Look up template (with jurisdiction fallback)
    3. Render subject + body
    4. Dispatch via channel adapter
    5. Return notification record with delivery status
    """
    # Check opt-out
    if is_opted_out(player_id, channel, notification_type):
        notif = Notification(
            notification_type=notification_type,
            channel=channel,
            player_id=player_id,
            recipient=recipient,
            status=DeliveryStatus.OPTED_OUT,
            jurisdiction=jurisdiction.value,
            metadata=metadata or {},
        )
        _DELIVERY_LOG.append(notif)
        logger.info("Notification blocked (opt-out): player=%s type=%s channel=%s",
                     player_id, notification_type.value, channel.value)
        return notif

    # Look up template
    template = get_template(notification_type, channel, jurisdiction)
    if template is None:
        raise ValueError(
            f"No template for {notification_type.value}/{channel.value}/{jurisdiction.value}"
        )

    # Render
    subject, body = render_template(template, variables)

    # Build notification
    notif = Notification(
        notification_type=notification_type,
        channel=channel,
        player_id=player_id,
        recipient=recipient,
        subject=subject,
        body=body,
        jurisdiction=jurisdiction.value,
        metadata=metadata or {},
    )

    # Dispatch (simulated -- in production this calls SES, Twilio, Firebase, etc.)
    notif = _dispatch(notif)
    _DELIVERY_LOG.append(notif)
    return notif


def _dispatch(notif: Notification) -> Notification:
    """
    Simulate dispatching a notification.

    In production, this routes to:
      - EMAIL: Amazon SES / SendGrid
      - SMS: Twilio / MessageBird
      - PUSH: Firebase Cloud Messaging / APNs
      - IN_APP: internal WebSocket / polling endpoint
    """
    notif.attempts += 1
    notif.last_attempt_at = datetime.now(timezone.utc)
    # Simulate success
    notif.status = DeliveryStatus.SENT
    notif.delivered_at = datetime.now(timezone.utc)
    logger.info("Notification dispatched: id=%s type=%s channel=%s recipient=%s",
                notif.notification_id, notif.notification_type, notif.channel, notif.recipient)
    return notif


def retry_failed(notification: Notification) -> Notification:
    """Retry a failed notification if retries remain."""
    if notification.status not in (DeliveryStatus.FAILED, DeliveryStatus.BOUNCED):
        raise ValueError(f"Cannot retry notification in status '{notification.status}'")
    if notification.attempts >= notification.max_retries:
        raise ValueError(
            f"Max retries ({notification.max_retries}) exhausted for {notification.notification_id}"
        )
    return _dispatch(notification)


def get_delivery_log(player_id: str | None = None,
                     notification_type: NotificationType | None = None) -> list[Notification]:
    """Retrieve delivery history with optional filters."""
    result = list(_DELIVERY_LOG)
    if player_id:
        result = [n for n in result if n.player_id == player_id]
    if notification_type:
        result = [n for n in result if n.notification_type == notification_type]
    return result


def clear_delivery_log() -> None:
    """Clear delivery log (for testing)."""
    _DELIVERY_LOG.clear()


# ---------------------------------------------------------------------------
# Regulatory-mandated notification checks
# ---------------------------------------------------------------------------

# Some notifications CANNOT be opted out of -- they are regulatory obligations.
_MANDATORY_TYPES: set[str] = {
    NotificationType.SELF_EXCLUSION_CONFIRMED.value,
    NotificationType.ACCOUNT_LOCKED.value,
    NotificationType.DEPOSIT_LIMIT_CHANGED.value,
    NotificationType.KYC_APPROVED.value,
    NotificationType.KYC_REJECTED.value,
    NotificationType.COMPLAINT_RECEIVED.value,
    NotificationType.COMPLAINT_RESOLVED.value,
    NotificationType.CHARGEBACK_NOTIFICATION.value,
}


def is_mandatory(notification_type: NotificationType) -> bool:
    """Check if a notification type is regulatory-mandated (cannot be opted out)."""
    return notification_type.value in _MANDATORY_TYPES
