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
alert_engine.py – Core rule evaluation logic for the risk-alerting service.

Each rule mirrors a Kafka Streams topology from the original Scala service.
Rather than using stateful stream processors, the Python implementation
evaluates rules against an in-memory event store (backed by Redis in
production) so that the logic is testable without a live Kafka cluster.

Rules implemented
-----------------
- HighDepositor               – FTD >= $5 000
- DepositMethodsAbuseWithin1Hour – 2 different payment methods declined in 30 min
- FiveUniqueInstrumentsIn20MinDeclined – 5 unique instruments failed in 20 min
- MultipleUniqueCardsUsed     – 2+ unique cards used successfully
- ThreeOrMoreUniqueCards      – 3+ unique cards used successfully
- TotalAmountOfDepositsIn24H  – total successful deposits > $1 000 in 24 h
- TotalDepositsIn3Days        – structuring: total > $9 000 in first 3 days
- TotalWithdrawalExceeded9000In72H – total accepted withdrawals > $9 000 in 72 h
- Declined20DepositsIn24H     – 20 declined deposits in 24 h
- Declined20DepositsIn7Days   – 20 declined deposits in 7 days
- SharedPaymentMethodsByTwoUsers – same payment method used by 2+ accounts
- Successful5DepositsOneGamingDay – 5+ successful deposits in one gaming day
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import structlog

from models import (
    AlertDescription,
    AlertName,
    AlertPriority,
    DepositEvent,
    PaymentStatus,
    RiskAlert,
    WithdrawalEvent,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants (match Scala originals)
# ---------------------------------------------------------------------------

_CENTS_PER_DOLLAR = 100

HIGH_DEPOSITOR_THRESHOLD_CENTS = 500_000          # $5 000
AMOUNT_24H_THRESHOLD_CENTS = 100_000              # $1 000
TOTAL_3_DAYS_THRESHOLD_CENTS = 900_000            # $9 000
WITHDRAWAL_72H_THRESHOLD_CENTS = 900_000          # $9 000
DEPOSIT_METHODS_ABUSE_WINDOW_MIN = 30
DEPOSIT_METHODS_ABUSE_UNIQUE_COUNT = 2
FIVE_INSTRUMENTS_WINDOW_MIN = 20
FIVE_INSTRUMENTS_COUNT = 5
DECLINED_X_DEPOSITS_24H_COUNT = 20
DECLINED_X_DEPOSITS_7D_COUNT = 20
MULTIPLE_UNIQUE_CARDS_THRESHOLD = 2
THREE_OR_MORE_CARDS_THRESHOLD = 3
SUCCESSFUL_5_DEPOSITS_DAY_THRESHOLD = 5
GAMING_DAY_START_HOUR = 6   # gaming day starts at 06:00 local time

# ---------------------------------------------------------------------------
# Alert description cache (in-memory, seeded with defaults)
# ---------------------------------------------------------------------------

_ALERT_DESCRIPTIONS: Dict[str, AlertDescription] = {
    name.value: AlertDescription(
        alert_name=name.value,
        title=name.value,
        description="",
        priority=AlertPriority.P3,
    )
    for name in AlertName
}


def get_alert_description(alert_name: str) -> Optional[AlertDescription]:
    return _ALERT_DESCRIPTIONS.get(alert_name)


def set_alert_description(desc: AlertDescription) -> None:
    _ALERT_DESCRIPTIONS[desc.alert_name] = desc


def _priority(alert_name: str) -> Optional[AlertPriority]:
    desc = get_alert_description(alert_name)
    return desc.priority if desc else None


# ---------------------------------------------------------------------------
# Helper: time-window filter
# ---------------------------------------------------------------------------


def _within(dt: datetime, minutes: int, now: Optional[datetime] = None) -> bool:
    """Return True if *dt* falls within the last *minutes* from *now*."""
    if now is None:
        now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt) <= timedelta(minutes=minutes)


def _gaming_day_start(dt: datetime) -> datetime:
    """Return the start of the gaming day containing *dt* (06:00 UTC)."""
    base = dt.replace(hour=GAMING_DAY_START_HOUR, minute=0, second=0, microsecond=0,
                      tzinfo=timezone.utc)
    if dt.hour < GAMING_DAY_START_HOUR:
        base -= timedelta(days=1)
    return base


# ---------------------------------------------------------------------------
# Rule evaluators
# Each returns a RiskAlert or None.
# ---------------------------------------------------------------------------


def check_high_depositor(
    user_id: int,
    deposit_history: List[DepositEvent],
) -> Optional[RiskAlert]:
    """
    Alert when a player's first-ever successful deposit is >= $5 000 (FTD).

    Mirrors: HighDepositor.scala
    """
    alert_name = AlertName.HIGH_DEPOSITOR.value
    successful = [
        e for e in deposit_history if e.content.status == PaymentStatus.SUCCEEDED
    ]
    if len(successful) == 1 and successful[0].content.amount >= HIGH_DEPOSITOR_THRESHOLD_CENTS:
        payment_id = successful[0].content.payment_id
        log.debug("HighDepositor: user %s FTD %s cents", user_id, successful[0].content.amount)
        return RiskAlert(
            message="FTD equal to or greater than $5000.00",
            alert_name=alert_name,
            priority=_priority(alert_name),
            details={"userId": str(user_id), "paymentId": str(payment_id)},
            user_ids=[str(user_id)],
        )
    return None


def check_deposit_methods_abuse(
    user_id: int,
    deposit_history: List[DepositEvent],
    now: Optional[datetime] = None,
) -> Optional[RiskAlert]:
    """
    Alert when a player uses 2 different payment methods that both declined
    within a 30-minute window.

    Mirrors: DepositMethodsAbuseWithin1Hour.scala
    """
    alert_name = AlertName.DEPOSIT_METHODS_ABUSE_1H.value
    recent_failed_methods: Set[str] = set()
    for event in deposit_history:
        c = event.content
        if c.status == PaymentStatus.FAILED and _within(c.timestamp, DEPOSIT_METHODS_ABUSE_WINDOW_MIN, now):
            if c.payment_method:
                recent_failed_methods.add(c.payment_method)

    if len(recent_failed_methods) >= DEPOSIT_METHODS_ABUSE_UNIQUE_COUNT:
        log.debug("DepositMethodsAbuse: user %s methods %s", user_id, recent_failed_methods)
        return RiskAlert(
            message="Deposit Method Abuse - Deposit declines using x2 payment methods within 30 minute period",
            alert_name=alert_name,
            priority=_priority(alert_name),
            details={"userId": str(user_id), "failedOnDeposits": ", ".join(sorted(recent_failed_methods))},
            user_ids=[str(user_id)],
        )
    return None


def check_five_unique_instruments_declined(
    user_id: int,
    deposit_history: List[DepositEvent],
    now: Optional[datetime] = None,
) -> Optional[RiskAlert]:
    """
    Alert when 5 distinct payment instruments fail within 20 minutes.
    Applies only to paywithmybank and vippreferred methods.

    Mirrors: FiveUniqueInstrumentsIn20MinutesDeclined.scala
    """
    alert_name = AlertName.FIVE_UNIQUE_INSTRUMENTS_20MIN.value
    ach_methods = {"paywithmybank_paywithmybank", "vippreferred"}
    failed_instruments: List[str] = []
    for event in deposit_history:
        c = event.content
        if (
            c.status == PaymentStatus.FAILED
            and c.payment_method in ach_methods
            and c.payment_instrument_id
            and _within(c.timestamp, FIVE_INSTRUMENTS_WINDOW_MIN, now)
        ):
            if c.payment_instrument_id not in failed_instruments:
                failed_instruments.append(c.payment_instrument_id)
        elif c.status == PaymentStatus.SUCCEEDED and c.payment_method in ach_methods:
            failed_instruments = []  # reset on success

    unique_failed = list(dict.fromkeys(failed_instruments))  # preserve order, deduplicate
    if len(unique_failed) >= FIVE_INSTRUMENTS_COUNT:
        log.debug("5UniqueInstruments: user %s instruments %s", user_id, unique_failed)
        return RiskAlert(
            message="5 deposit attempts with different instruments used in last 20 minutes were unsuccessful",
            alert_name=alert_name,
            priority=_priority(alert_name),
            details={"userId": str(user_id), "instrumentIds": ",".join(unique_failed[:FIVE_INSTRUMENTS_COUNT])},
            user_ids=[str(user_id)],
        )
    return None


def check_multiple_unique_cards(
    user_id: int,
    deposit_history: List[DepositEvent],
    threshold: int = MULTIPLE_UNIQUE_CARDS_THRESHOLD,
    alert_name: Optional[str] = None,
) -> Optional[RiskAlert]:
    """
    Alert when a player uses N+ unique card instruments successfully.

    Mirrors: MultipleUniqueCardsUsed.scala
    """
    if alert_name is None:
        alert_name = (
            AlertName.MULTIPLE_UNIQUE_CARDS.value
            if threshold == MULTIPLE_UNIQUE_CARDS_THRESHOLD
            else AlertName.THREE_OR_MORE_UNIQUE_CARDS.value
        )
    unique_cards: Set[str] = set()
    for event in deposit_history:
        c = event.content
        if (
            c.status == PaymentStatus.SUCCEEDED
            and c.payment_method
            and c.payment_method.startswith("pxp_card")
            and c.recurring_reference
        ):
            unique_cards.add(c.recurring_reference)

    if len(unique_cards) >= threshold:
        log.debug("%s: user %s cards %s", alert_name, user_id, unique_cards)
        return RiskAlert(
            message="Multiple cards added to profile",
            alert_name=alert_name,
            priority=_priority(alert_name),
            details={"userId": str(user_id), "uniqueCardsUsed": ",".join(sorted(unique_cards))},
            user_ids=[str(user_id)],
        )
    return None


def check_total_deposits_24h(
    user_id: int,
    deposit_history: List[DepositEvent],
    now: Optional[datetime] = None,
) -> Optional[RiskAlert]:
    """
    Alert when the player deposits more than $1 000 total in any 24-hour window.

    Mirrors: TotalAmountOfDepositsIn24Hours.scala
    """
    alert_name = AlertName.TOTAL_AMOUNT_DEPOSITS_24H.value
    recent = [
        e for e in deposit_history
        if e.content.status == PaymentStatus.SUCCEEDED and _within(e.content.timestamp, 24 * 60, now)
    ]
    if not recent:
        return None

    # Group by currency
    by_currency: Dict[str, int] = defaultdict(int)
    for e in recent:
        by_currency[e.content.currency] += e.content.amount

    alerts: List[RiskAlert] = []
    for currency, total_cents in by_currency.items():
        if total_cents > AMOUNT_24H_THRESHOLD_CENTS:
            log.debug("TotalDeposits24H: user %s total %s %s", user_id, total_cents, currency)
            alerts.append(RiskAlert(
                message=f"The user {user_id} has made more than {AMOUNT_24H_THRESHOLD_CENTS // _CENTS_PER_DOLLAR} {currency}",
                alert_name=alert_name,
                priority=_priority(alert_name),
                details={"userId": str(user_id), "amount": str(total_cents)},
                user_ids=[str(user_id)],
            ))

    return alerts[0] if alerts else None


def check_structuring_3_days(
    user_id: int,
    deposit_history: List[DepositEvent],
) -> Optional[RiskAlert]:
    """
    Structuring detection: total successful deposits within the first 3 days
    of the player's deposit history exceed $9 000.

    Mirrors: TotalDepositsIn3Days.scala
    """
    alert_name = AlertName.TOTAL_DEPOSITS_3_DAYS.value
    successful = sorted(
        [e for e in deposit_history if e.content.status == PaymentStatus.SUCCEEDED],
        key=lambda e: e.content.timestamp,
    )
    if not successful:
        return None

    first_ts = successful[0].content.timestamp
    if first_ts.tzinfo is None:
        first_ts = first_ts.replace(tzinfo=timezone.utc)
    limit = first_ts + timedelta(days=3)

    first_3_days = [
        e for e in successful
        if (e.content.timestamp.replace(tzinfo=timezone.utc) if e.content.timestamp.tzinfo is None
            else e.content.timestamp) <= limit
    ]
    total = sum(e.content.amount for e in first_3_days)

    if total >= TOTAL_3_DAYS_THRESHOLD_CENTS:
        log.debug("Structuring3Days: user %s total %s", user_id, total)
        return RiskAlert(
            message="Structure D- Deposit amount",
            alert_name=alert_name,
            priority=_priority(alert_name),
            details={"userId": str(user_id), "amount": str(total)},
            user_ids=[str(user_id)],
        )
    return None


def check_withdrawal_exceeded_9000_72h(
    user_id: int,
    withdrawal_history: List[WithdrawalEvent],
    now: Optional[datetime] = None,
) -> Optional[RiskAlert]:
    """
    Alert when total accepted withdrawals exceed $9 000 within 72 hours.

    Mirrors: TotalWithdrawalExceeded9000In72Hours.scala
    """
    alert_name = AlertName.TOTAL_WITHDRAWAL_9000_72H.value
    recent_total = sum(
        e.amount
        for e in withdrawal_history
        if e.status == "ACCEPTED" and _within(e.timestamp, 72 * 60, now)
    )
    if recent_total > WITHDRAWAL_72H_THRESHOLD_CENTS:
        log.debug("Withdrawal9000_72H: user %s total %s", user_id, recent_total)
        return RiskAlert(
            message=f"The user {user_id} has withdrawn more than {WITHDRAWAL_72H_THRESHOLD_CENTS // _CENTS_PER_DOLLAR} USD",
            alert_name=alert_name,
            priority=_priority(alert_name),
            details={"userId": str(user_id), "amount": str(recent_total)},
            user_ids=[str(user_id)],
        )
    return None


def check_declined_x_deposits_in_period(
    user_id: int,
    deposit_history: List[DepositEvent],
    deposit_count: int,
    window_minutes: int,
    now: Optional[datetime] = None,
) -> Optional[RiskAlert]:
    """
    Alert when a player has N+ declined deposits within a time window.
    Resets on any successful deposit.

    Mirrors: DeclinedXDepositsInPeriod.scala (abstract base)
    """
    window_hours = window_minutes // 60
    window_label = (
        f"{window_hours}hours" if window_hours < 168 else f"{window_hours // 24}days"
    )
    alert_name = f"Last{deposit_count}DepositsIn{window_hours}{'hours' if window_hours < 24 else f'{window_hours // 24}days'}Declined"

    declined_ids: Set[str] = set()
    # Walk events within window; reset on success
    relevant = [
        e for e in deposit_history
        if _within(e.content.timestamp, window_minutes, now)
        and e.content.status in {PaymentStatus.FAILED, PaymentStatus.SUCCEEDED}
    ]
    for event in relevant:
        if event.content.status == PaymentStatus.SUCCEEDED:
            declined_ids = set()
        elif event.content.status == PaymentStatus.FAILED:
            declined_ids.add(str(event.content.payment_id))

    if len(declined_ids) >= deposit_count:
        log.debug("DeclinedDeposits: user %s count %s window %s min", user_id, len(declined_ids), window_minutes)
        return RiskAlert(
            message=f"Deposit method abuse - x{deposit_count} deposit declines within {window_hours} hours period.",
            alert_name=alert_name,
            priority=_priority(alert_name),
            details={"userId": str(user_id), "numberOfDeposits": str(len(declined_ids))},
            user_ids=[str(user_id)],
        )
    return None


def check_shared_payment_methods(
    recurring_ref: str,
    user_ids: Set[int],
) -> Optional[RiskAlert]:
    """
    Alert when two or more player accounts share the same payment instrument.

    Mirrors: SharedPaymentMethodsByTwoUsers.scala
    """
    alert_name = AlertName.SHARED_PAYMENT_METHODS.value
    if len(user_ids) > 1:
        users_str = "-".join(sorted(str(u) for u in user_ids))
        log.debug("SharedPaymentMethods: ref %s users %s", recurring_ref, user_ids)
        return RiskAlert(
            message="Payment method was used by two separate gaming accounts",
            alert_name=alert_name,
            alias=f"SharedPaymentMethodsByTwoUsersAlert/method/{recurring_ref}/users/{users_str}",
            priority=_priority(alert_name),
            details={
                "methodIdentifier": recurring_ref,
                "usersUsingTheSameMethod": ", ".join(sorted(str(u) for u in user_ids)),
            },
            user_ids=[str(u) for u in sorted(user_ids)],
        )
    return None


def check_successful_5_deposits_gaming_day(
    user_id: int,
    deposit_history: List[DepositEvent],
    now: Optional[datetime] = None,
) -> Optional[RiskAlert]:
    """
    Alert when a player makes 5+ successful deposits within one gaming day.

    Mirrors: Successful5DepositsOneDay.scala
    """
    alert_name = AlertName.SUCCESSFUL_5_DEPOSITS_ONE_DAY.value
    if now is None:
        now = datetime.now(timezone.utc)
    day_start = _gaming_day_start(now)

    count = sum(
        1
        for e in deposit_history
        if e.content.status == PaymentStatus.SUCCEEDED
        and (e.content.timestamp.replace(tzinfo=timezone.utc) if e.content.timestamp.tzinfo is None
             else e.content.timestamp) >= day_start
    )
    if count >= SUCCESSFUL_5_DEPOSITS_DAY_THRESHOLD:
        day_label = day_start.strftime("%Y-%m-%d")
        log.debug("5DepositsGamingDay: user %s count %s", user_id, count)
        return RiskAlert(
            message="User made 5 or more successful deposits in one gaming date",
            alert_name=alert_name,
            alias=f"Successful5DepositsOneGamingDayAlert/{user_id}/{day_label}",
            priority=_priority(alert_name),
            details={"userId": str(user_id), "numberOfDeposits": str(count)},
            user_ids=[str(user_id)],
        )
    return None


# ---------------------------------------------------------------------------
# Composite evaluator: run all deposit rules for a single user
# ---------------------------------------------------------------------------


def evaluate_deposit_rules(
    user_id: int,
    deposit_history: List[DepositEvent],
    now: Optional[datetime] = None,
) -> List[RiskAlert]:
    """
    Run every deposit-related alert rule and return all triggered alerts.

    This is called by the Kafka consumer after each deposit event for the
    affected user_id.
    """
    triggered: List[RiskAlert] = []

    rules: List[Callable[..., Optional[RiskAlert]]] = [
        lambda: check_high_depositor(user_id, deposit_history),
        lambda: check_deposit_methods_abuse(user_id, deposit_history, now),
        lambda: check_five_unique_instruments_declined(user_id, deposit_history, now),
        lambda: check_multiple_unique_cards(user_id, deposit_history, MULTIPLE_UNIQUE_CARDS_THRESHOLD),
        lambda: check_multiple_unique_cards(user_id, deposit_history, THREE_OR_MORE_CARDS_THRESHOLD,
                                            AlertName.THREE_OR_MORE_UNIQUE_CARDS.value),
        lambda: check_total_deposits_24h(user_id, deposit_history, now),
        lambda: check_structuring_3_days(user_id, deposit_history),
        lambda: check_declined_x_deposits_in_period(user_id, deposit_history, 20, 24 * 60, now),
        lambda: check_declined_x_deposits_in_period(user_id, deposit_history, 20, 7 * 24 * 60, now),
        lambda: check_successful_5_deposits_gaming_day(user_id, deposit_history, now),
    ]

    for rule in rules:
        try:
            result = rule()
            if result is not None:
                triggered.append(result)
        except Exception as exc:
            log.exception("Alert rule error for user %s: %s", user_id, exc)

    return triggered


def evaluate_withdrawal_rules(
    user_id: int,
    withdrawal_history: List[WithdrawalEvent],
    now: Optional[datetime] = None,
) -> List[RiskAlert]:
    """Run all withdrawal-related alert rules."""
    triggered: List[RiskAlert] = []
    result = check_withdrawal_exceeded_9000_72h(user_id, withdrawal_history, now)
    if result:
        triggered.append(result)
    return triggered
