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
test_risk_alerting.py – 15+ tests for the risk-alerting service.

Covers:
  - Each alert rule in alert_engine
  - Notification dispatcher routing
  - Kafka consumer event processing (deposit + withdrawal)
  - Shared payment method cross-user detection
  - SIGAP report eligibility
"""

from __future__ import annotations

import importlib.util
import sys
import os
from datetime import datetime, timedelta, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import List, Optional, cast
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# See the matching comment in `risk-scoring/tests/test_risk_matrix.py`:
# both services ship a `models.py`, so we must install the risk-alerting
# copy under `sys.modules["models"]` before any sibling module imports it.
SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str):
    """Load a sibling module under an explicit sys.modules entry."""
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, SERVICE_DIR / file_name,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# `models` first, then everything that `from models import ...` at module
# level. `kafka_consumer` is lazily imported inside a few tests, so we
# pre-load it here to lock its `from models` bindings to the risk-alerting
# copy even if another test file later replaces `sys.modules["models"]`
# during collection.
_load_local_module("models", "models.py")
_load_local_module("notification", "notification.py")
_load_local_module("alert_engine", "alert_engine.py")
_load_local_module("kafka_consumer", "kafka_consumer.py")

from alert_engine import (
    check_deposit_methods_abuse,
    check_five_unique_instruments_declined,
    check_high_depositor,
    check_multiple_unique_cards,
    check_shared_payment_methods,
    check_structuring_3_days,
    check_successful_5_deposits_gaming_day,
    check_total_deposits_24h,
    check_withdrawal_exceeded_9000_72h,
    check_declined_x_deposits_in_period,
    evaluate_deposit_rules,
    evaluate_withdrawal_rules,
    set_alert_description,
)
from models import (
    AlertDescription,
    AlertName,
    AlertPriority,
    DepositEvent,
    PaymentStatus,
    PaymentStatusChangeEvent,
    RiskAlert,
    WithdrawalEvent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2025, 6, 15, 14, 0, 0, tzinfo=timezone.utc)


def _deposit(
    user_id: int,
    amount_cents: int,
    status: PaymentStatus,
    payment_id: int = 1,
    currency: str = "USD",
    payment_method: Optional[str] = None,
    recurring_reference: Optional[str] = None,
    payment_instrument_id: Optional[str] = None,
    minutes_ago: int = 0,
) -> DepositEvent:
    ts = NOW - timedelta(minutes=minutes_ago)
    return DepositEvent(
        content=PaymentStatusChangeEvent(
            user_id=user_id,
            payment_id=payment_id,
            amount=amount_cents,
            currency=currency,
            status=status,
            payment_method=payment_method,
            recurring_reference=recurring_reference,
            payment_instrument_id=payment_instrument_id,
            timestamp=ts,
        )
    )


def _withdrawal(
    user_id: int,
    amount_cents: int,
    status: str = "ACCEPTED",
    minutes_ago: int = 0,
) -> WithdrawalEvent:
    ts = NOW - timedelta(minutes=minutes_ago)
    return WithdrawalEvent(
        user_id=user_id,
        amount=amount_cents,
        currency="USD",
        status=status,
        timestamp=ts,
    )


# ---------------------------------------------------------------------------
# 1. HighDepositor – FTD >= $5 000
# ---------------------------------------------------------------------------


def test_high_depositor_triggers_on_exactly_5000():
    deposits = [_deposit(1, 500_000, PaymentStatus.SUCCEEDED, payment_id=10)]
    alert = check_high_depositor(1, deposits)
    assert alert is not None
    assert alert.alert_name == AlertName.HIGH_DEPOSITOR.value
    assert "paymentId" in alert.details


def test_high_depositor_no_alert_below_threshold():
    deposits = [_deposit(1, 499_999, PaymentStatus.SUCCEEDED)]
    assert check_high_depositor(1, deposits) is None


def test_high_depositor_no_alert_on_second_deposit():
    deposits = [
        _deposit(1, 600_000, PaymentStatus.SUCCEEDED, payment_id=1),
        _deposit(1, 600_000, PaymentStatus.SUCCEEDED, payment_id=2),
    ]
    assert check_high_depositor(1, deposits) is None


def test_high_depositor_no_alert_on_failed_ftd():
    deposits = [_deposit(1, 600_000, PaymentStatus.FAILED)]
    assert check_high_depositor(1, deposits) is None


# ---------------------------------------------------------------------------
# 2. DepositMethodsAbuse – 2 methods declined within 30 min
# ---------------------------------------------------------------------------


def test_deposit_methods_abuse_triggers():
    deposits = [
        _deposit(2, 100_00, PaymentStatus.FAILED, payment_method="visa", minutes_ago=5),
        _deposit(2, 100_00, PaymentStatus.FAILED, payment_method="mastercard", minutes_ago=10),
    ]
    alert = check_deposit_methods_abuse(2, deposits, now=NOW)
    assert alert is not None
    assert "failedOnDeposits" in alert.details
    assert "visa" in alert.details["failedOnDeposits"]


def test_deposit_methods_abuse_same_method_no_alert():
    deposits = [
        _deposit(2, 100_00, PaymentStatus.FAILED, payment_method="visa", minutes_ago=5),
        _deposit(2, 100_00, PaymentStatus.FAILED, payment_method="visa", minutes_ago=10),
    ]
    assert check_deposit_methods_abuse(2, deposits, now=NOW) is None


def test_deposit_methods_abuse_outside_window_no_alert():
    deposits = [
        _deposit(2, 100_00, PaymentStatus.FAILED, payment_method="visa", minutes_ago=5),
        _deposit(2, 100_00, PaymentStatus.FAILED, payment_method="mastercard", minutes_ago=35),  # outside
    ]
    assert check_deposit_methods_abuse(2, deposits, now=NOW) is None


# ---------------------------------------------------------------------------
# 3. TotalAmountOfDepositsIn24H – > $1 000 in 24 h
# ---------------------------------------------------------------------------


def test_total_deposits_24h_triggers():
    deposits = [
        _deposit(3, 60_000, PaymentStatus.SUCCEEDED, payment_id=1, minutes_ago=60),
        _deposit(3, 50_001, PaymentStatus.SUCCEEDED, payment_id=2, minutes_ago=120),
    ]
    alert = check_total_deposits_24h(3, deposits, now=NOW)
    assert alert is not None
    assert int(alert.details["amount"]) > 100_000


def test_total_deposits_24h_no_alert_exactly_threshold():
    deposits = [_deposit(3, 100_000, PaymentStatus.SUCCEEDED, minutes_ago=30)]
    assert check_total_deposits_24h(3, deposits, now=NOW) is None


def test_total_deposits_24h_ignores_failed():
    deposits = [
        _deposit(3, 200_000, PaymentStatus.FAILED, payment_id=1, minutes_ago=30),
        _deposit(3, 50_000, PaymentStatus.SUCCEEDED, payment_id=2, minutes_ago=60),
    ]
    assert check_total_deposits_24h(3, deposits, now=NOW) is None


# ---------------------------------------------------------------------------
# 4. Structuring / TotalDepositsIn3Days – > $9 000 in first 3 days
# ---------------------------------------------------------------------------


def test_structuring_3_days_triggers():
    base = NOW - timedelta(days=2)
    deps = [
        DepositEvent(content=PaymentStatusChangeEvent(
            user_id=4, payment_id=i, amount=300_001, currency="USD",
            status=PaymentStatus.SUCCEEDED,
            timestamp=base + timedelta(hours=i)
        ))
        for i in range(3)
    ]
    alert = check_structuring_3_days(4, deps)
    assert alert is not None
    assert alert.alert_name == AlertName.TOTAL_DEPOSITS_3_DAYS.value


def test_structuring_3_days_ignores_deposits_after_3d_window():
    base = NOW - timedelta(days=1)
    # Two deposits: one in first 3 days window, one after 3 days
    late = base - timedelta(days=5)
    deps = [
        DepositEvent(content=PaymentStatusChangeEvent(
            user_id=4, payment_id=1, amount=500_000, currency="USD",
            status=PaymentStatus.SUCCEEDED, timestamp=late
        )),
        DepositEvent(content=PaymentStatusChangeEvent(
            user_id=4, payment_id=2, amount=200_000, currency="USD",
            status=PaymentStatus.SUCCEEDED, timestamp=base
        )),
    ]
    # First deposit is the "first"; second is > 3 days later so not counted
    alert = check_structuring_3_days(4, deps)
    assert alert is None  # only 500_000 < 900_000


# ---------------------------------------------------------------------------
# 5. TotalWithdrawalExceeded9000In72H
# ---------------------------------------------------------------------------


def test_withdrawal_exceeded_9000_72h_triggers():
    withdrawals = [_withdrawal(5, 500_001, minutes_ago=10), _withdrawal(5, 400_000, minutes_ago=20)]
    alert = check_withdrawal_exceeded_9000_72h(5, withdrawals, now=NOW)
    assert alert is not None
    assert int(alert.details["amount"]) > 900_000


def test_withdrawal_pending_not_counted():
    withdrawals = [_withdrawal(5, 1_000_000, status="PENDING", minutes_ago=5)]
    assert check_withdrawal_exceeded_9000_72h(5, withdrawals, now=NOW) is None


def test_withdrawal_outside_72h_window_not_counted():
    withdrawals = [_withdrawal(5, 1_000_000, minutes_ago=73 * 60)]
    assert check_withdrawal_exceeded_9000_72h(5, withdrawals, now=NOW) is None


# ---------------------------------------------------------------------------
# 6. MultipleUniqueCards
# ---------------------------------------------------------------------------


def test_multiple_unique_cards_triggers_at_threshold():
    deposits = [
        _deposit(6, 100, PaymentStatus.SUCCEEDED, payment_method="pxp_card_visa", recurring_reference="ref1"),
        _deposit(6, 100, PaymentStatus.SUCCEEDED, payment_method="pxp_card_mc", recurring_reference="ref2"),
    ]
    alert = check_multiple_unique_cards(6, deposits, threshold=2)
    assert alert is not None
    assert "ref1" in alert.details["uniqueCardsUsed"]


def test_multiple_unique_cards_same_reference_no_alert():
    deposits = [
        _deposit(6, 100, PaymentStatus.SUCCEEDED, payment_method="pxp_card_visa", recurring_reference="ref1"),
        _deposit(6, 200, PaymentStatus.SUCCEEDED, payment_method="pxp_card_visa", recurring_reference="ref1"),
    ]
    assert check_multiple_unique_cards(6, deposits, threshold=2) is None


# ---------------------------------------------------------------------------
# 7. SharedPaymentMethodsByTwoUsers
# ---------------------------------------------------------------------------


def test_shared_payment_methods_triggers():
    alert = check_shared_payment_methods("card-ref-abc", {101, 102})
    assert alert is not None
    assert "101" in alert.user_ids
    assert "102" in alert.user_ids
    assert "card-ref-abc" in alert.details["methodIdentifier"]


def test_shared_payment_methods_single_user_no_alert():
    assert check_shared_payment_methods("card-ref-xyz", {101}) is None


# ---------------------------------------------------------------------------
# 8. Successful5DepositsOneGamingDay
# ---------------------------------------------------------------------------


def test_5_deposits_gaming_day_triggers():
    gaming_day_start = NOW.replace(hour=6, minute=0, second=0, microsecond=0)
    deposits = [
        _deposit(7, 100, PaymentStatus.SUCCEEDED, payment_id=i,
                 minutes_ago=int((NOW - (gaming_day_start + timedelta(hours=i))).total_seconds() // 60))
        for i in range(5)
    ]
    alert = check_successful_5_deposits_gaming_day(7, deposits, now=NOW)
    assert alert is not None
    assert int(alert.details["numberOfDeposits"]) >= 5


def test_5_deposits_gaming_day_only_4_no_alert():
    gaming_day_start = NOW.replace(hour=6, minute=0, second=0, microsecond=0)
    deposits = [
        _deposit(7, 100, PaymentStatus.SUCCEEDED, payment_id=i,
                 minutes_ago=int((NOW - (gaming_day_start + timedelta(hours=i))).total_seconds() // 60))
        for i in range(4)
    ]
    assert check_successful_5_deposits_gaming_day(7, deposits, now=NOW) is None


# ---------------------------------------------------------------------------
# 9. DeclinedXDepositsInPeriod
# ---------------------------------------------------------------------------


def test_declined_20_in_24h_triggers():
    deposits = [
        _deposit(8, 100, PaymentStatus.FAILED, payment_id=i, minutes_ago=i * 5)
        for i in range(20)
    ]
    alert = check_declined_x_deposits_in_period(8, deposits, 20, 24 * 60, now=NOW)
    assert alert is not None


def test_declined_20_in_24h_resets_on_success():
    # 10 declines, then 1 success, then 10 more declines = only 10 in last window
    deposits = [
        _deposit(8, 100, PaymentStatus.FAILED, payment_id=i, minutes_ago=(30 - i)) for i in range(10)
    ] + [
        _deposit(8, 100, PaymentStatus.SUCCEEDED, payment_id=100, minutes_ago=20)
    ] + [
        _deposit(8, 100, PaymentStatus.FAILED, payment_id=200 + i, minutes_ago=(15 - i)) for i in range(10)
    ]
    assert check_declined_x_deposits_in_period(8, deposits, 20, 24 * 60, now=NOW) is None


# ---------------------------------------------------------------------------
# 10. Composite rule evaluation
# ---------------------------------------------------------------------------


def test_evaluate_deposit_rules_returns_multiple_alerts():
    """
    Two large deposits in 24h that also form a structuring pattern
    should trigger both TotalAmountOfDepositsIn24Hours and structuring alert.
    """
    base = NOW - timedelta(hours=1)
    deposits = [
        DepositEvent(content=PaymentStatusChangeEvent(
            user_id=9, payment_id=1, amount=500_000, currency="USD",
            status=PaymentStatus.SUCCEEDED, timestamp=base
        )),
        DepositEvent(content=PaymentStatusChangeEvent(
            user_id=9, payment_id=2, amount=400_001, currency="USD",
            status=PaymentStatus.SUCCEEDED, timestamp=base + timedelta(minutes=30)
        )),
    ]
    alerts = evaluate_deposit_rules(9, deposits, now=NOW)
    names = [a.alert_name for a in alerts]
    assert AlertName.TOTAL_AMOUNT_DEPOSITS_24H.value in names
    assert AlertName.TOTAL_DEPOSITS_3_DAYS.value in names


def test_evaluate_withdrawal_rules_returns_alert():
    withdrawals = [_withdrawal(10, 1_000_001)]
    alerts = evaluate_withdrawal_rules(10, withdrawals, now=NOW)
    assert len(alerts) == 1
    assert alerts[0].alert_name == AlertName.TOTAL_WITHDRAWAL_9000_72H.value


# ---------------------------------------------------------------------------
# 11. Alert priority from description cache
# ---------------------------------------------------------------------------


def test_alert_priority_from_description_cache():
    set_alert_description(AlertDescription(
        alert_name=AlertName.HIGH_DEPOSITOR.value,
        title="High Depositor",
        description="FTD >= $5000",
        priority=AlertPriority.P1,
    ))
    deposits = [_deposit(11, 500_000, PaymentStatus.SUCCEEDED)]
    alert = check_high_depositor(11, deposits)
    assert alert is not None
    assert alert.priority == AlertPriority.P1


# ---------------------------------------------------------------------------
# 12. Notification dispatcher channels
# ---------------------------------------------------------------------------


def test_notification_dispatcher_calls_all_channels():
    from notification import NotificationDispatcher

    mock_channel_a = MagicMock()
    mock_channel_a.send.return_value = True
    mock_channel_b = MagicMock()
    mock_channel_b.send.return_value = True

    dispatcher = NotificationDispatcher(channels=[mock_channel_a, mock_channel_b])
    alert = RiskAlert(
        message="Test",
        alert_name="TestAlert",
        user_ids=["1"],
    )
    results = dispatcher.dispatch(alert)

    mock_channel_a.send.assert_called_once_with(alert)
    mock_channel_b.send.assert_called_once_with(alert)


def test_notification_dispatcher_continues_on_channel_failure():
    from notification import NotificationDispatcher

    failing = MagicMock()
    failing.send.side_effect = RuntimeError("channel down")
    succeeding = MagicMock()
    succeeding.send.return_value = True

    dispatcher = NotificationDispatcher(channels=[failing, succeeding])
    alert = RiskAlert(message="Test", alert_name="TestAlert", user_ids=["1"])

    # Should not raise even if one channel fails
    results = dispatcher.dispatch(alert)
    succeeding.send.assert_called_once()


# ---------------------------------------------------------------------------
# 13. SIGAP channel eligibility
# ---------------------------------------------------------------------------


def test_sigap_channel_skips_non_eligible_alert():
    from notification import SIGAPChannel, SIGAP_ALERT_NAMES

    channel = SIGAPChannel(api_url="", api_key="")
    alert = RiskAlert(message="Test", alert_name="HighDepositor", user_ids=["1"])
    # Should return False immediately (not in SIGAP_ALERT_NAMES or no URL)
    result = channel.send(alert)
    assert result is False


def test_sigap_channel_eligible_alert_attempts_send():
    from notification import SIGAPChannel

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        channel = SIGAPChannel(api_url="http://sigap.example.com/report", api_key="key123")
        alert = RiskAlert(
            message="Structuring detected",
            alert_name="StructuringDDepositLimitIn3Days",
            user_ids=["42"],
            details={"amount": "950000", "currency": "BRL"},
        )
        result = channel.send(alert)
        assert result is True
        mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# 14. Kafka consumer event processing
# ---------------------------------------------------------------------------


def test_process_deposit_event_updates_store():
    from kafka_consumer import EventStore, process_deposit_event
    from notification import NotificationDispatcher

    store = EventStore()
    dispatcher = NotificationDispatcher(channels=[])
    event = _deposit(20, 100, PaymentStatus.SUCCEEDED)
    process_deposit_event(event, store, dispatcher, now=NOW)
    assert len(store.get_deposits(20)) == 1


def test_process_withdrawal_event_updates_store():
    from kafka_consumer import EventStore, process_withdrawal_event
    from notification import NotificationDispatcher

    store = EventStore()
    dispatcher = NotificationDispatcher(channels=[])
    event = _withdrawal(21, 500_000)
    process_withdrawal_event(event, store, dispatcher, now=NOW)
    assert len(store.get_withdrawals(21)) == 1
