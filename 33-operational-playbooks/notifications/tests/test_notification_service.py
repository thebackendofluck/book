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
Tests for the transactional notification service.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from notification_service import (
    Channel,
    DeliveryStatus,
    Jurisdiction,
    Notification,
    NotificationType,
    NotificationTemplate,
    clear_delivery_log,
    clear_opt_outs,
    get_delivery_log,
    get_template,
    is_mandatory,
    is_opted_out,
    opt_out,
    register_template,
    render_template,
    retry_failed,
    send_notification,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_state():
    clear_delivery_log()
    clear_opt_outs()
    yield
    clear_delivery_log()
    clear_opt_outs()


_DEFAULT_VARS = {
    "brand_name": "AcmeToCasino",
    "player_name": "John",
    "verification_link": "https://acmetocasino.com/verify/abc123",
}


# ---------------------------------------------------------------------------
# Template registry tests
# ---------------------------------------------------------------------------

class TestTemplateRegistry:
    def test_get_default_template(self):
        t = get_template(NotificationType.REGISTRATION_WELCOME, Channel.EMAIL)
        assert t is not None
        assert "{brand_name}" in t.subject

    def test_jurisdiction_specific_template(self):
        t = get_template(NotificationType.REGISTRATION_WELCOME, Channel.EMAIL, Jurisdiction.UKGC)
        assert t is not None
        assert "BeGambleAware" in t.body

    def test_brazil_template(self):
        t = get_template(NotificationType.REGISTRATION_WELCOME, Channel.EMAIL, Jurisdiction.BRAZIL)
        assert t is not None
        assert "Bem-vindo" in t.body

    def test_fallback_to_default(self):
        # ONTARIO has no specific template, should fall back to DEFAULT
        t = get_template(NotificationType.REGISTRATION_WELCOME, Channel.EMAIL, Jurisdiction.ONTARIO)
        assert t is not None
        assert t.jurisdiction == Jurisdiction.DEFAULT

    def test_no_template_returns_none(self):
        # No IN_APP template for REGISTRATION_WELCOME
        t = get_template(NotificationType.REGISTRATION_WELCOME, Channel.IN_APP)
        assert t is None


# ---------------------------------------------------------------------------
# Template rendering tests
# ---------------------------------------------------------------------------

class TestTemplateRendering:
    def test_render_replaces_placeholders(self):
        t = get_template(NotificationType.REGISTRATION_WELCOME, Channel.EMAIL)
        subject, body = render_template(t, _DEFAULT_VARS)
        assert "AcmeToCasino" in subject
        assert "John" in body
        assert "https://acmetocasino.com/verify/abc123" in body

    def test_render_sms(self):
        t = get_template(NotificationType.TWO_FACTOR_CODE, Channel.SMS)
        _, body = render_template(t, {"brand_name": "AcmeToCasino", "code": "123456"})
        assert "123456" in body
        assert "AcmeToCasino" in body


# ---------------------------------------------------------------------------
# Send notification tests
# ---------------------------------------------------------------------------

class TestSendNotification:
    def test_send_email_success(self):
        notif = send_notification(
            NotificationType.REGISTRATION_WELCOME,
            Channel.EMAIL,
            player_id="player_001",
            recipient="john@example.com",
            variables=_DEFAULT_VARS,
        )
        assert notif.status == DeliveryStatus.SENT
        assert notif.attempts == 1
        assert notif.delivered_at is not None
        assert "AcmeToCasino" in notif.subject

    def test_send_sms(self):
        notif = send_notification(
            NotificationType.TWO_FACTOR_CODE,
            Channel.SMS,
            player_id="player_001",
            recipient="+44123456789",
            variables={"brand_name": "AcmeToCasino", "code": "654321"},
        )
        assert notif.status == DeliveryStatus.SENT
        assert "654321" in notif.body

    def test_send_with_jurisdiction(self):
        notif = send_notification(
            NotificationType.REGISTRATION_WELCOME,
            Channel.EMAIL,
            player_id="player_uk",
            recipient="uk@example.com",
            variables=_DEFAULT_VARS,
            jurisdiction=Jurisdiction.UKGC,
        )
        assert "BeGambleAware" in notif.body

    def test_missing_template_raises(self):
        with pytest.raises(ValueError, match="No template"):
            send_notification(
                NotificationType.BONUS_CREDITED,
                Channel.IN_APP,
                player_id="player_001",
                recipient="device_token",
                variables={},
            )


# ---------------------------------------------------------------------------
# Opt-out tests
# ---------------------------------------------------------------------------

class TestOptOut:
    def test_opt_out_blocks_notification(self):
        opt_out("player_001", Channel.EMAIL, notification_type=NotificationType.BONUS_CREDITED)
        assert is_opted_out("player_001", Channel.EMAIL, NotificationType.BONUS_CREDITED) is True

    def test_opt_out_all_types(self):
        opt_out("player_001", Channel.SMS)
        assert is_opted_out("player_001", Channel.SMS, NotificationType.TWO_FACTOR_CODE) is True
        assert is_opted_out("player_001", Channel.SMS, NotificationType.DEPOSIT_CONFIRMED) is True

    def test_no_opt_out(self):
        assert is_opted_out("player_001", Channel.EMAIL, NotificationType.REGISTRATION_WELCOME) is False

    def test_opted_out_notification_status(self):
        opt_out("player_002", Channel.EMAIL)
        notif = send_notification(
            NotificationType.REGISTRATION_WELCOME,
            Channel.EMAIL,
            player_id="player_002",
            recipient="x@example.com",
            variables=_DEFAULT_VARS,
        )
        assert notif.status == DeliveryStatus.OPTED_OUT

    def test_opt_out_different_channel_not_affected(self):
        opt_out("player_001", Channel.SMS)
        assert is_opted_out("player_001", Channel.EMAIL, NotificationType.TWO_FACTOR_CODE) is False


# ---------------------------------------------------------------------------
# Delivery log tests
# ---------------------------------------------------------------------------

class TestDeliveryLog:
    def test_log_populated_after_send(self):
        send_notification(
            NotificationType.REGISTRATION_WELCOME,
            Channel.EMAIL,
            player_id="player_001",
            recipient="a@b.com",
            variables=_DEFAULT_VARS,
        )
        log = get_delivery_log()
        assert len(log) == 1

    def test_filter_by_player(self):
        send_notification(NotificationType.REGISTRATION_WELCOME, Channel.EMAIL,
                          player_id="p1", recipient="a@b.com", variables=_DEFAULT_VARS)
        send_notification(NotificationType.REGISTRATION_WELCOME, Channel.EMAIL,
                          player_id="p2", recipient="c@d.com", variables=_DEFAULT_VARS)
        assert len(get_delivery_log(player_id="p1")) == 1

    def test_filter_by_type(self):
        send_notification(NotificationType.REGISTRATION_WELCOME, Channel.EMAIL,
                          player_id="p1", recipient="a@b.com", variables=_DEFAULT_VARS)
        send_notification(NotificationType.TWO_FACTOR_CODE, Channel.SMS,
                          player_id="p1", recipient="+1234", variables={"brand_name": "X", "code": "000"})
        assert len(get_delivery_log(notification_type=NotificationType.TWO_FACTOR_CODE)) == 1


# ---------------------------------------------------------------------------
# Retry tests
# ---------------------------------------------------------------------------

class TestRetry:
    def test_retry_failed_notification(self):
        notif = Notification(
            notification_type=NotificationType.REGISTRATION_WELCOME,
            channel=Channel.EMAIL,
            player_id="p1",
            recipient="a@b.com",
            status=DeliveryStatus.FAILED,
            attempts=1,
            max_retries=3,
        )
        retried = retry_failed(notif)
        assert retried.status == DeliveryStatus.SENT
        assert retried.attempts == 2

    def test_retry_exhausted_raises(self):
        notif = Notification(
            notification_type=NotificationType.REGISTRATION_WELCOME,
            channel=Channel.EMAIL,
            player_id="p1",
            recipient="a@b.com",
            status=DeliveryStatus.FAILED,
            attempts=3,
            max_retries=3,
        )
        with pytest.raises(ValueError, match="Max retries"):
            retry_failed(notif)

    def test_retry_non_failed_raises(self):
        notif = Notification(
            notification_type=NotificationType.REGISTRATION_WELCOME,
            channel=Channel.EMAIL,
            player_id="p1",
            recipient="a@b.com",
            status=DeliveryStatus.SENT,
        )
        with pytest.raises(ValueError, match="Cannot retry"):
            retry_failed(notif)


# ---------------------------------------------------------------------------
# Mandatory notification tests
# ---------------------------------------------------------------------------

class TestMandatoryNotifications:
    def test_self_exclusion_is_mandatory(self):
        assert is_mandatory(NotificationType.SELF_EXCLUSION_CONFIRMED) is True

    def test_deposit_limit_is_mandatory(self):
        assert is_mandatory(NotificationType.DEPOSIT_LIMIT_CHANGED) is True

    def test_bonus_credited_is_not_mandatory(self):
        assert is_mandatory(NotificationType.BONUS_CREDITED) is False

    def test_complaint_received_is_mandatory(self):
        assert is_mandatory(NotificationType.COMPLAINT_RECEIVED) is True
