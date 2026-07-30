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
test_internal_alerts.py – 10+ tests for the internal-alerts service.

Covers:
  - AlertDispatcher happy path (single alert, multi-channel)
  - AlertDispatcher error handling (unknown type, no email address)
  - PagerDuty channel eligibility filter
  - Slack channel payload structure
  - HealthMonitor aggregation
  - HealthCheck individual results
  - process_pending batch dispatch
"""

from __future__ import annotations

import sys
import os
from typing import List, Optional
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import (
    Alert,
    AlertMessage,
    AlertStatus,
    AlertType,
    EmailAddress,
)
from alert_dispatcher import (
    AlertDispatcher,
    AlertRepository,
    AlertTypeRepository,
    DispatchChannel,
    EmailAddressRepository,
    MailerChannel,
    PagerDutyChannel,
    SlackChannel,
)
from health_monitor import (
    CheckResult,
    DatabaseHealthCheck,
    HealthMonitor,
    HealthStatus,
    HttpHealthCheck,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _InMemoryAlertRepo(AlertRepository):
    def __init__(self, alerts: Optional[List[Alert]] = None):
        self._alerts = alerts or []
        self.updates: List[tuple] = []

    def update_status(self, alert: Alert, status: AlertStatus) -> None:
        self.updates.append((alert.id, status))

    def find_alerts_to_send(self) -> List[Alert]:
        return [a for a in self._alerts if a.status == AlertStatus.PENDING]

    def create_alert(self, alert: Alert) -> Optional[int]:
        self._alerts.append(alert)
        return len(self._alerts)


class _InMemoryAlertTypeRepo(AlertTypeRepository):
    def __init__(self, types: dict):
        self._types = types

    def find_by_name(self, name: str) -> Optional[AlertType]:
        return self._types.get(name)


class _InMemoryEmailRepo(EmailAddressRepository):
    def __init__(self, addresses: dict):
        self._addresses = addresses

    def find_by_name_and_jurisdiction(
        self, name: str, jurisdiction_id: Optional[str]
    ) -> Optional[EmailAddress]:
        return self._addresses.get((name, jurisdiction_id)) or self._addresses.get((name, None))


def _make_dispatcher(
    alert: Optional[Alert] = None,
    alert_type: Optional[AlertType] = None,
    email: Optional[EmailAddress] = None,
    channels: Optional[List[DispatchChannel]] = None,
) -> tuple:
    alert = alert or Alert(
        id=1,
        alert_type="rg_level_up",
        user_id=42,
        brand_id=1,
        params={"score": "25"},
        status=AlertStatus.PENDING,
    )
    alert_type = alert_type or AlertType(
        name="rg_level_up",
        recipient="compliance-team",
        template_name="rg-level-up",
        max_frequency_seconds=3600,
    )
    email = email or EmailAddress(
        name="compliance-team",
        jurisdiction_id=None,
        address="compliance@casino.example.com",
    )
    alert_repo = _InMemoryAlertRepo([alert])
    type_repo = _InMemoryAlertTypeRepo({alert_type.name: alert_type})
    email_repo = _InMemoryEmailRepo({(email.name, None): email})

    mock_channel = MagicMock(spec=DispatchChannel)
    mock_channel.send.return_value = True

    dispatcher = AlertDispatcher(
        alert_repo=alert_repo,
        alert_type_repo=type_repo,
        email_repo=email_repo,
        channels=channels or [mock_channel],
    )
    return dispatcher, alert_repo, mock_channel


# ---------------------------------------------------------------------------
# 1–3: AlertDispatcher happy-path tests
# ---------------------------------------------------------------------------


def test_dispatcher_processes_alert_successfully():
    dispatcher, repo, channel = _make_dispatcher()
    alert = repo._alerts[0]
    dispatcher.process_alert(alert)
    channel.send.assert_called_once()
    assert (alert.id, AlertStatus.SENT) in repo.updates


def test_dispatcher_marks_error_when_all_channels_fail():
    mock_channel = MagicMock(spec=DispatchChannel)
    mock_channel.send.return_value = False  # all channels fail
    dispatcher, repo, _ = _make_dispatcher(channels=[mock_channel])
    alert = repo._alerts[0]
    dispatcher.process_alert(alert)
    assert (alert.id, AlertStatus.ERROR) in repo.updates


def test_dispatcher_calls_multiple_channels():
    ch1 = MagicMock(spec=DispatchChannel)
    ch1.send.return_value = True
    ch2 = MagicMock(spec=DispatchChannel)
    ch2.send.return_value = True
    dispatcher, repo, _ = _make_dispatcher(channels=[ch1, ch2])
    alert = repo._alerts[0]
    dispatcher.process_alert(alert)
    ch1.send.assert_called_once()
    ch2.send.assert_called_once()


# ---------------------------------------------------------------------------
# 4: Unknown alert type raises error → marks ERROR
# ---------------------------------------------------------------------------


def test_dispatcher_unknown_alert_type_marks_error():
    alert = Alert(
        id=99, alert_type="non_existent_type", user_id=1,
        status=AlertStatus.PENDING,
    )
    alert_repo = _InMemoryAlertRepo([alert])
    type_repo = _InMemoryAlertTypeRepo({})
    email_repo = _InMemoryEmailRepo({})
    mock_channel = MagicMock(spec=DispatchChannel)
    mock_channel.send.return_value = True

    dispatcher = AlertDispatcher(
        alert_repo=alert_repo,
        alert_type_repo=type_repo,
        email_repo=email_repo,
        channels=[mock_channel],
    )
    dispatcher.process_alert(alert)
    assert (99, AlertStatus.ERROR) in alert_repo.updates


# ---------------------------------------------------------------------------
# 5: No email address registered
# ---------------------------------------------------------------------------


def test_dispatcher_missing_email_marks_error():
    dispatcher, repo, _ = _make_dispatcher()
    # rebuild with empty email repo
    alert = repo._alerts[0]
    alert_type = AlertType(
        name=alert.alert_type,
        recipient="nobody",
        template_name="tmpl",
        max_frequency_seconds=0,
    )
    type_repo = _InMemoryAlertTypeRepo({alert.alert_type: alert_type})
    email_repo = _InMemoryEmailRepo({})
    mock_channel = MagicMock(spec=DispatchChannel)
    mock_channel.send.return_value = True
    dispatcher2 = AlertDispatcher(
        alert_repo=repo,
        alert_type_repo=type_repo,
        email_repo=email_repo,
        channels=[mock_channel],
    )
    dispatcher2.process_alert(alert)
    assert AlertStatus.ERROR in [s for _, s in repo.updates]


# ---------------------------------------------------------------------------
# 6–7: PagerDuty channel eligibility
# ---------------------------------------------------------------------------


def test_pagerduty_only_sends_for_critical_types():
    pd_channel = PagerDutyChannel(
        routing_key="key123",
        critical_types={"service_down"},
    )
    non_critical = Alert(id=1, alert_type="rg_level_up", user_id=1, status=AlertStatus.PENDING)
    alert_type = AlertType(name="rg_level_up", recipient="r", template_name="t", max_frequency_seconds=0)
    email = EmailAddress(name="r", jurisdiction_id=None, address="x@x.com")
    result = pd_channel.send(non_critical, alert_type, email)
    assert result is False


def test_pagerduty_sends_for_critical_type():
    pd_channel = PagerDutyChannel(
        routing_key="key123",
        critical_types={"service_down"},
    )
    critical = Alert(id=2, alert_type="service_down", user_id=None, status=AlertStatus.PENDING)
    alert_type = AlertType(name="service_down", recipient="ops", template_name="t", max_frequency_seconds=0)
    email = EmailAddress(name="ops", jurisdiction_id=None, address="ops@casino.example.com")

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=202, raise_for_status=MagicMock())
        result = pd_channel.send(critical, alert_type, email)
    assert result is True


# ---------------------------------------------------------------------------
# 8: Slack payload structure
# ---------------------------------------------------------------------------


def test_slack_channel_posts_structured_payload():
    slack = SlackChannel(webhook_url="https://hooks.slack.com/test")
    alert = Alert(id=3, alert_type="rg_level_up", user_id=88, brand_id=2,
                  params={"level": "2"}, status=AlertStatus.PENDING)
    alert_type = AlertType(name="rg_level_up", recipient="r", template_name="t", max_frequency_seconds=0)
    email = EmailAddress(name="r", jurisdiction_id=None, address="a@b.com")

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        result = slack.send(alert, alert_type, email)

    assert result is True
    payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert "attachments" in payload


# ---------------------------------------------------------------------------
# 9: HealthMonitor aggregates multiple checks
# ---------------------------------------------------------------------------


def test_health_monitor_all_healthy():
    check_a = MagicMock()
    check_a.name = "db"
    check_a.check.return_value = CheckResult(name="db", healthy=True)
    check_b = MagicMock()
    check_b.name = "kafka"
    check_b.check.return_value = CheckResult(name="kafka", healthy=True)

    monitor = HealthMonitor()
    monitor._checks = [check_a, check_b]
    status = monitor.run_checks()
    assert status.overall is True
    assert all(c.healthy for c in status.checks)


def test_health_monitor_one_failing():
    check_a = MagicMock()
    check_a.name = "db"
    check_a.check.return_value = CheckResult(name="db", healthy=True)
    check_b = MagicMock()
    check_b.name = "kafka"
    check_b.check.return_value = CheckResult(name="kafka", healthy=False, message="connection refused")

    monitor = HealthMonitor()
    monitor._checks = [check_a, check_b]
    status = monitor.run_checks()
    assert status.overall is False
    degraded = [c for c in status.checks if not c.healthy]
    assert len(degraded) == 1


# ---------------------------------------------------------------------------
# 10: DatabaseHealthCheck
# ---------------------------------------------------------------------------


def test_db_health_check_success():
    execute = MagicMock(return_value=[(1,)])
    check = DatabaseHealthCheck(execute_fn=execute)
    result = check.check()
    assert result.healthy is True
    execute.assert_called_once_with("SELECT 1")


def test_db_health_check_failure():
    def bad_execute(q):
        raise ConnectionError("db unreachable")

    check = DatabaseHealthCheck(execute_fn=bad_execute)
    result = check.check()
    assert result.healthy is False
    assert "db unreachable" in result.message


# ---------------------------------------------------------------------------
# 11: process_pending batch
# ---------------------------------------------------------------------------


def test_process_pending_dispatches_all_pending_alerts():
    alerts = [
        Alert(id=i, alert_type="rg_level_up", user_id=i, status=AlertStatus.PENDING)
        for i in range(1, 4)
    ]
    dispatcher, repo, channel = _make_dispatcher()
    # Replace repo with one containing multiple alerts
    alert_type = AlertType(name="rg_level_up", recipient="compliance-team",
                           template_name="rg-level-up", max_frequency_seconds=3600)
    email = EmailAddress(name="compliance-team", jurisdiction_id=None,
                         address="c@casino.example.com")
    repo2 = _InMemoryAlertRepo(alerts)
    type_repo2 = _InMemoryAlertTypeRepo({"rg_level_up": alert_type})
    email_repo2 = _InMemoryEmailRepo({("compliance-team", None): email})
    ch = MagicMock(spec=DispatchChannel)
    ch.send.return_value = True
    dispatcher2 = AlertDispatcher(
        alert_repo=repo2,
        alert_type_repo=type_repo2,
        email_repo=email_repo2,
        channels=[ch],
    )
    count = dispatcher2.process_pending()
    assert count == 3
    assert ch.send.call_count == 3
