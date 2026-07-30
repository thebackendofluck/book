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
matrix_engine.py – Multi-factor risk scoring engine for the risk-matrix service.

Orchestrates the scoring pipeline across all supported event types and
risk dimensions:
  - Deposit velocity / total amount
  - Game pattern anomalies
  - Geographic anomalies
  - Time-of-day patterns
  - Deposit limit changes
  - Reversed withdrawals
  - Timeout and interaction counters

This module wraps the lower-level MatrixScorer (scorer.py) with the
event-type–specific MetricProvider implementations that mirror the Scala
MatrixScoreDataProviders.scala dispatch table.

Usage
-----
    from matrix_engine import score_event

    updated_matrices = score_event(
        user_id=42,
        global_id=42,
        event_type="deposit-confirmed",
        jurisdiction="GB",
        data={"depositCount": 6},
        user_events_service=...,
        flags_service=...,
    )
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from models import EventType, MatrixScoreType, UserMatrixScore
from scorer import MatrixScorer, MetricProvider, MessageSender, ScoreRepository, UnresolvedMetricError

import structlog
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract metric service interfaces
# ---------------------------------------------------------------------------


class UserEventsService(ABC):
    """Port for querying historical user events from the database."""

    @abstractmethod
    def count_deposits(self, user_id: int, from_ts: datetime, to_ts: datetime) -> int: ...

    @abstractmethod
    def deposit_total(self, user_id: int, from_ts: datetime, to_ts: datetime) -> int: ...

    @abstractmethod
    def count_declined_deposits(self, user_id: int, from_ts: datetime, to_ts: datetime) -> int: ...

    @abstractmethod
    def count_failed_deposits(self, user_id: int, from_ts: datetime, to_ts: datetime) -> int: ...

    @abstractmethod
    def count_deposit_limit_changes(
        self, user_id: int, from_ts: datetime, to_ts: datetime, global_id: Optional[int]
    ) -> int: ...

    @abstractmethod
    def count_deposit_limit_increases(self, user_id: int, from_ts: datetime, to_ts: datetime) -> int: ...

    @abstractmethod
    def payment_options_created(self, user_id: int, from_ts: datetime, to_ts: datetime) -> int: ...

    @abstractmethod
    def payments_options_created_after_failed_deposit(
        self, user_id: int, from_ts: datetime, to_ts: datetime
    ) -> int: ...

    @abstractmethod
    def depositing_days(self, user_id: int, from_ts: datetime, to_ts: datetime) -> int: ...

    @abstractmethod
    def count_reversed_withdrawals(self, user_id: int, from_ts: datetime, to_ts: datetime) -> int: ...

    @abstractmethod
    def reversed_withdrawals_total(self, user_id: int, from_ts: datetime, to_ts: datetime) -> int: ...

    @abstractmethod
    def count_interactions_by_types(
        self, user_id: int, global_id: int, types: List[str], from_ts: datetime, to_ts: datetime
    ) -> int: ...

    @abstractmethod
    def count_timeouts(
        self, user_id: int, global_id: int, from_ts: datetime, to_ts: datetime
    ) -> int: ...


class FlagsService(ABC):
    """Port for querying player flag assignments."""

    @abstractmethod
    def user_has_flag(self, user_id: int, flag_id: int) -> bool: ...


class MatrixScoreRepository(ABC):
    """Extended repository with level and score-type queries."""

    @abstractmethod
    def get_scores_by_type_for_user(self, user_id: int, score_type: str) -> int: ...

    @abstractmethod
    def get_current_user_level(
        self, user_id: int, matrix_id: str
    ) -> Optional[Any]: ...

    @abstractmethod
    def add_user_score(self, score: UserMatrixScore) -> None: ...


# ---------------------------------------------------------------------------
# Concrete MetricProvider implementations (one per EventType)
# Mirrors MatrixScoreDataProviders.scala
# ---------------------------------------------------------------------------


class _DepositConfirmedProvider(MetricProvider):
    """
    Metrics for the deposit-confirmed event.

    Available metrics: depositCount, depositTotal, paymentOptionsCreated,
    paymentsOptionsCreatedAfterFailedDeposit, depositingDays, has_flag_N.
    """

    def __init__(
        self,
        user_events: UserEventsService,
        flags: FlagsService,
    ) -> None:
        self._ue = user_events
        self._flags = flags

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        if metric == "depositCount":
            return self._ue.count_deposits(user_id, from_ts, to_ts)
        if metric == "depositTotal":
            return self._ue.deposit_total(user_id, from_ts, to_ts)
        if metric == "paymentOptionsCreated":
            return self._ue.payment_options_created(user_id, from_ts, to_ts)
        if metric == "paymentsOptionsCreatedAfterFailedDeposit":
            return self._ue.payments_options_created_after_failed_deposit(user_id, from_ts, to_ts)
        if metric == "depositingDays":
            return self._ue.depositing_days(user_id, from_ts, to_ts)
        if metric.startswith("has_flag"):
            flag_id = int(metric[9:])
            return self._flags.user_has_flag(user_id, flag_id)
        log.warning("Unknown metric for deposit-confirmed: %s", metric)
        raise UnresolvedMetricError(metric)


class _DepositDeclinedProvider(MetricProvider):
    """Metrics for deposit-declined: depositsDeclined."""

    def __init__(self, user_events: UserEventsService) -> None:
        self._ue = user_events

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        if metric == "depositsDeclined":
            return self._ue.count_declined_deposits(user_id, from_ts, to_ts)
        log.warning("Unknown metric for deposit-declined: %s", metric)
        raise UnresolvedMetricError(metric)


class _DepositFailedProvider(MetricProvider):
    """Metrics for deposit-failed: failedDepositCount."""

    def __init__(self, user_events: UserEventsService) -> None:
        self._ue = user_events

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        if metric == "failedDepositCount":
            return self._ue.count_failed_deposits(user_id, from_ts, to_ts)
        log.warning("Unknown metric for deposit-failed: %s", metric)
        raise UnresolvedMetricError(metric)


class _DepositLimitChangedProvider(MetricProvider):
    """Metrics for deposit-limit-changed: userDepositLimitChange, globalDepositLimitChange."""

    def __init__(self, user_events: UserEventsService) -> None:
        self._ue = user_events

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        if metric == "userDepositLimitChange":
            return self._ue.count_deposit_limit_changes(user_id, from_ts, to_ts, None)
        if metric == "globalDepositLimitChange":
            return self._ue.count_deposit_limit_changes(user_id, from_ts, to_ts, global_id)
        log.warning("Unknown metric for deposit-limit-changed: %s", metric)
        raise UnresolvedMetricError(metric)


class _DepositLimitIncreasedProvider(MetricProvider):
    """Metrics for deposit-limit-increased: depositLimitIncreases."""

    def __init__(self, user_events: UserEventsService) -> None:
        self._ue = user_events

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        if metric == "depositLimitIncreases":
            return self._ue.count_deposit_limit_increases(user_id, from_ts, to_ts)
        log.warning("Unknown metric for deposit-limit-increased: %s", metric)
        raise UnresolvedMetricError(metric)


class _WithdrawalReversedProvider(MetricProvider):
    """Metrics for user-reversed-withdrawal: reversedWithdrawalCount, reversedWithdrawalTotal."""

    def __init__(self, user_events: UserEventsService) -> None:
        self._ue = user_events

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        if metric == "reversedWithdrawalCount":
            return self._ue.count_reversed_withdrawals(user_id, from_ts, to_ts)
        if metric == "reversedWithdrawalTotal":
            return self._ue.reversed_withdrawals_total(user_id, from_ts, to_ts)
        log.warning("Unknown metric for user-reversed-withdrawal: %s", metric)
        raise UnresolvedMetricError(metric)


class _InteractionAppliedProvider(MetricProvider):
    """Metrics for interaction-apply: rg31rg41InteractionsCount."""

    def __init__(self, user_events: UserEventsService) -> None:
        self._ue = user_events

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        if metric == "rg31rg41InteractionsCount":
            return self._ue.count_interactions_by_types(
                user_id, global_id, ["rg3.1", "rg4.1"], from_ts, to_ts
            )
        log.warning("Unknown metric for interaction-apply: %s", metric)
        raise UnresolvedMetricError(metric)


class _ResetScoreProvider(MetricProvider):
    """Metrics for reset-score: rg_level2, rg_level3."""

    def __init__(self, score_repo: MatrixScoreRepository) -> None:
        self._repo = score_repo

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        from .models import SCORE_TYPE_RG2, SCORE_TYPE_RG3
        if metric == "rg_level2":
            return self._repo.get_scores_by_type_for_user(user_id, SCORE_TYPE_RG2) + 1
        if metric == "rg_level3":
            return self._repo.get_scores_by_type_for_user(user_id, SCORE_TYPE_RG3) + 1
        log.warning("Unknown metric for reset-score: %s", metric)
        raise UnresolvedMetricError(metric)


class _TimeoutAppliedProvider(MetricProvider):
    """
    Metrics for timeout-apply:
    timeout, timeout1d, timeout90d, cam_amber, has_flag_N.
    """

    def __init__(
        self,
        user_events: UserEventsService,
        score_repo: MatrixScoreRepository,
        flags: FlagsService,
    ) -> None:
        self._ue = user_events
        self._repo = score_repo
        self._flags = flags

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        from .models import MATRIX_AFF
        if metric == "timeout":
            return self._ue.count_timeouts(user_id, global_id, from_ts, to_ts)
        if metric == "timeout1d":
            return self._ue.count_timeouts(user_id, global_id, to_ts - timedelta(days=1), to_ts)
        if metric == "timeout90d":
            return self._ue.count_timeouts(user_id, global_id, to_ts - timedelta(days=90), to_ts)
        if metric == "cam_amber":
            level = self._repo.get_current_user_level(user_id, MATRIX_AFF)
            return bool(level and level.level_number == 1)
        if metric.startswith("has_flag"):
            flag_id = int(metric[9:])
            return self._flags.user_has_flag(user_id, flag_id)
        log.warning("Unknown metric for timeout-apply: %s", metric)
        raise UnresolvedMetricError(metric)


class _NoOpProvider(MetricProvider):
    """Fallback: only handles has_flag_N metrics."""

    def __init__(self, flags: FlagsService) -> None:
        self._flags = flags

    def provide_metric(
        self, user_id: int, global_id: int, metric: str,
        from_ts: datetime, to_ts: datetime, is_global: bool = False,
    ) -> Any:
        if metric.startswith("has_flag"):
            flag_id = int(metric[9:])
            return self._flags.user_has_flag(user_id, flag_id)
        log.warning("No metric provider for: %s", metric)
        raise UnresolvedMetricError(metric)


# ---------------------------------------------------------------------------
# Dispatch table (mirrors MatrixScoreDataProviders.apply())
# ---------------------------------------------------------------------------

def _build_provider(
    event_type: str,
    user_events: UserEventsService,
    score_repo: MatrixScoreRepository,
    flags: FlagsService,
) -> MetricProvider:
    """Return the correct MetricProvider for a given event type string."""
    mapping = {
        EventType.DEPOSIT_CONFIRMED.value: lambda: _DepositConfirmedProvider(user_events, flags),
        EventType.DEPOSIT_DECLINED.value: lambda: _DepositDeclinedProvider(user_events),
        EventType.DEPOSIT_FAILED.value: lambda: _DepositFailedProvider(user_events),
        EventType.DEPOSIT_LIMIT_CHANGED.value: lambda: _DepositLimitChangedProvider(user_events),
        EventType.DEPOSIT_LIMIT_INCREASED.value: lambda: _DepositLimitIncreasedProvider(user_events),
        EventType.USER_REVERSED_WITHDRAWAL.value: lambda: _WithdrawalReversedProvider(user_events),
        EventType.INTERACTION_APPLIED.value: lambda: _InteractionAppliedProvider(user_events),
        EventType.RESET_SCORE.value: lambda: _ResetScoreProvider(score_repo),
        EventType.TIMEOUT_APPLIED.value: lambda: _TimeoutAppliedProvider(user_events, score_repo, flags),
    }
    factory = mapping.get(event_type)
    return factory() if factory else _NoOpProvider(flags)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def score_event(
    user_id: int,
    global_id: int,
    event_type: str,
    jurisdiction: str,
    score_types: List[MatrixScoreType],
    score_repo: MatrixScoreRepository,
    user_events: UserEventsService,
    flags: FlagsService,
    message_sender: Optional[MessageSender] = None,
    data: Optional[Dict[str, Any]] = None,
    on_date: Optional[datetime] = None,
) -> List[str]:
    """
    Score a player event against the full risk matrix configuration.

    Parameters
    ----------
    user_id / global_id:
        Player and their global (grouped) identity.
    event_type:
        One of the EventType enum values (e.g. "deposit-confirmed").
    jurisdiction:
        Two-letter ISO jurisdiction code (e.g. "GB", "BR", "MT").
    score_types:
        Full list of MatrixScoreType rules (loaded from DB / config).
    score_repo / user_events / flags:
        Service ports for data access.
    message_sender:
        Optional outbound messenger (Kafka producer).  If None, alerts and
        interactions are logged but not dispatched.
    data:
        Extra event-level data values (merged into the metric namespace).
    on_date:
        Override "now" for deterministic testing.

    Returns
    -------
    List of matrix IDs that received at least one new score entry.
    """
    scorer = MatrixScorer(
        score_types=score_types,
        score_repo=score_repo,
        message_sender=message_sender,
    )
    provider = _build_provider(event_type, user_events, score_repo, flags)
    return scorer.apply_scores(
        user_id=user_id,
        global_id=global_id,
        event=event_type,
        metric_provider=provider,
        data=data or {},
        jurisdiction=jurisdiction,
        on_date=on_date,
    )
