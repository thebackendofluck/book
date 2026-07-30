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
VIP Rule Processor services.

Provides:
- ConditionLogic: boundary-based condition evaluation
- RuleProcessor: weighted tier matching algorithm (v2.0)
- EvaluateUserStatus: full VIP recalculation pipeline
- VipRepository: database access for rules, activity, and status
- TransactionsEventProcessor: Kafka consumer for real-time transaction events
- RecalculationCommandProcessor: Kafka consumer for explicit recalculate requests
- SchedulerFlow: midnight batch recalculation for all users

v2.0 algorithm improvements:
  1. Weighted bet volume: live_casino x1.5, table_games x1.3
  2. Net deposit consideration in scoring
  3. Frequency multiplier (active days / 30 * 50% bonus)
  4. Minimum account age enforcement
  5. Cooldown period between tier changes
  6. Self-excluded players always return None (never assigned a VIP tier)
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from confluent_kafka import Consumer, KafkaError, Producer

from .models import (
    AccountsEvent,
    AppConfig,
    BrandId,
    LastChange,
    PlayerActivity,
    RecalculateCommand,
    RuleId,
    SchedulerConfig,
    TierChangeNotification,
    UserId,
    UserStatus,
    UserVipRuleUpdated,
    VipRule,
    VipTierBenefit,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Condition logic (v1 boundary checks)
# ---------------------------------------------------------------------------

class ConditionLogic:
    @staticmethod
    def applies_condition(value: int, low: int | None, hi: int | None) -> bool:
        if low is not None and hi is not None:
            return low <= value <= hi
        if low is not None:
            return value >= low
        if hi is not None:
            return value <= hi
        return False


# ---------------------------------------------------------------------------
# Rule processor (v2.0)
# ---------------------------------------------------------------------------

class RuleProcessor:
    """
    VIP tier matching algorithm.

    Evaluates all rules for a player and returns the best matching rule
    based on a weighted score:
      score = (tier * 1000 + volume_score + net_bonus) * frequency_multiplier

    Game-type weights:
      slots: 1.0, live_casino: 1.5, table_games: 1.3, sports: 1.0

    Eligibility pre-filters:
      - minimum_days_active: account must be at least N days old
      - cooldown_days: N days must pass since the last tier change
      - is_self_excluded: always returns None immediately
    """

    GAME_TYPE_WEIGHTS: dict[str, float] = {
        "slots": 1.0,
        "live_casino": 1.5,
        "table_games": 1.3,
        "sports": 1.0,
    }

    @classmethod
    def apply(
        cls,
        rules: list[VipRule],
        activity: PlayerActivity,
        current_status: UserStatus | None = None,
        jurisdiction: str | None = None,
        now: datetime | None = None,
    ) -> VipRule | None:
        if activity.is_self_excluded:
            return None

        if now is None:
            now = datetime.now(timezone.utc)

        weighted_bets = cls._calculate_weighted_bet_volume(activity)

        eligible = [
            r for r in rules
            if cls._meets_eligibility(r, activity, current_status, now)
        ]

        scored: list[tuple[VipRule, float]] = []
        for rule in eligible:
            thresholds = cls._resolve_thresholds(rule, jurisdiction)
            if (
                ConditionLogic.applies_condition(
                    activity.total_deposit_volume,
                    thresholds["deposit_low"],
                    thresholds["deposit_hi"],
                )
                and ConditionLogic.applies_condition(
                    weighted_bets,
                    thresholds["handle_low"],
                    thresholds["handle_hi"],
                )
            ):
                score = cls._calculate_score(rule, activity, weighted_bets)
                scored.append((rule, score))

        if not scored:
            return None
        return max(scored, key=lambda x: x[1])[0]

    @classmethod
    def apply_simple(
        cls, rules: list[VipRule], deposits: int, bets: int
    ) -> VipRule | None:
        """Backward-compatible v1 algorithm."""
        for rule in rules:
            if (
                ConditionLogic.applies_condition(
                    deposits,
                    rule.deposit_low_boundary,
                    rule.deposit_hi_boundary,
                )
                and ConditionLogic.applies_condition(
                    bets, rule.handle_low_boundary, rule.handle_hi_boundary
                )
            ):
                return rule
        return None

    @classmethod
    def _calculate_weighted_bet_volume(cls, activity: PlayerActivity) -> int:
        if not activity.game_type_bet_volumes:
            return activity.total_bet_volume
        weighted = sum(
            int(vol * cls.GAME_TYPE_WEIGHTS.get(gt, 1.0))
            for gt, vol in activity.game_type_bet_volumes.items()
        )
        return max(weighted, activity.total_bet_volume)

    @staticmethod
    def _meets_eligibility(
        rule: VipRule,
        activity: PlayerActivity,
        current: UserStatus | None,
        now: datetime,
    ) -> bool:
        if rule.minimum_days_active is not None:
            if activity.account_age_days < rule.minimum_days_active:
                return False
        if rule.cooldown_days is not None and current is not None:
            days_since = (now - current.timestamp).days
            if days_since < rule.cooldown_days:
                return False
        return True

    @staticmethod
    def _resolve_thresholds(rule: VipRule, jurisdiction: str | None) -> dict[str, Any]:
        if jurisdiction and jurisdiction in rule.jurisdiction_overrides:
            jt = rule.jurisdiction_overrides[jurisdiction]
            return {
                "deposit_low": jt.deposit_low_boundary or rule.deposit_low_boundary,
                "deposit_hi": jt.deposit_hi_boundary or rule.deposit_hi_boundary,
                "handle_low": jt.handle_low_boundary or rule.handle_low_boundary,
                "handle_hi": jt.handle_hi_boundary or rule.handle_hi_boundary,
            }
        return {
            "deposit_low": rule.deposit_low_boundary,
            "deposit_hi": rule.deposit_hi_boundary,
            "handle_low": rule.handle_low_boundary,
            "handle_hi": rule.handle_hi_boundary,
        }

    @staticmethod
    def _calculate_score(
        rule: VipRule, activity: PlayerActivity, weighted_bets: int
    ) -> float:
        tier_score = rule.tier * 1000.0
        volume_score = (activity.total_deposit_volume + weighted_bets) / 100_000.0
        freq_mult = 1.0 + (min(activity.active_days, 30) / 30.0 * 0.5)
        net_bonus = (
            activity.net_deposit_volume / activity.total_deposit_volume * 100.0
            if activity.net_deposit_volume > 0 and activity.total_deposit_volume > 0
            else 0.0
        )
        return (tier_score + volume_score + net_bonus) * freq_mult


# ---------------------------------------------------------------------------
# VIP Repository interface
# ---------------------------------------------------------------------------

class VipRepository:
    """Data access for VIP rules, player activity, and user status."""

    async def get_rules_by_brand(self, brand_id: BrandId) -> list[VipRule]:
        raise NotImplementedError

    async def get_player_activity(
        self, user_id: UserId, from_dt: datetime, to_dt: datetime
    ) -> PlayerActivity:
        raise NotImplementedError

    async def get_latest_user_status(self, user_id: UserId) -> UserStatus | None:
        raise NotImplementedError

    async def insert_user_status(self, status: UserStatus) -> UserStatus:
        raise NotImplementedError

    async def list_all_users_for_brand(self, brand_id: BrandId) -> list[UserId]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# EvaluateUserStatus
# ---------------------------------------------------------------------------

class EvaluateUserStatus:
    """
    Core VIP recalculation pipeline.

    For a given user + brand:
      1. Load rules for the brand
      2. Aggregate 30-day activity
      3. Run RuleProcessor with weighted algorithm
      4. Compare new tier to current; persist if changed
      5. Build tier-change notification if tier changed
    """

    class Updated:
        def __init__(
            self,
            user_status: UserStatus,
            old_status: UserStatus | None,
            notification: TierChangeNotification | None,
        ) -> None:
            self.user_status = user_status
            self.old_status = old_status
            self.notification = notification

    class NotChanged:
        def __init__(self, user_status: UserStatus | None) -> None:
            self.user_status = user_status

    @classmethod
    async def run(
        cls,
        user_id: UserId,
        brand_id: BrandId,
        trigger: LastChange,
        repo: VipRepository,
        jurisdiction: str | None = None,
    ) -> "EvaluateUserStatus.Updated | EvaluateUserStatus.NotChanged":
        rules = await repo.get_rules_by_brand(brand_id)
        now = datetime.now(timezone.utc)
        activity = await repo.get_player_activity(user_id, now - timedelta(days=30), now)
        log.debug(
            "vip.activity",
            user_id=user_id.value,
            deposits=activity.total_deposit_volume,
            bets=activity.total_bet_volume,
            net=activity.net_deposit_volume,
        )
        current = await repo.get_latest_user_status(user_id)
        matched_rule = RuleProcessor.apply(rules, activity, current, jurisdiction, now)

        new_status = UserStatus(
            id=-1,
            user_id=user_id,
            rule_id=matched_rule.rule_id if matched_rule else None,
            rule_name=matched_rule.status_name if matched_rule else None,
            tier=matched_rule.tier if matched_rule else None,
            bet_volume=activity.total_bet_volume,
            deposit_volume=activity.total_deposit_volume,
            net_deposit_volume=activity.net_deposit_volume,
            timestamp=now,
            last_change=trigger,
            jurisdiction=jurisdiction,
        )

        tier_changed = (current.rule_id if current else None) != new_status.rule_id
        volume_changed = current is not None and (
            current.bet_volume != new_status.bet_volume
            or current.deposit_volume != new_status.deposit_volume
        )

        if tier_changed or volume_changed:
            saved = await repo.insert_user_status(new_status)
            log.info(
                "vip.tier_updated",
                user_id=user_id.value,
                old_tier=current.rule_name if current else "none",
                new_tier=new_status.rule_name or "none",
            )
            notification = (
                cls._build_notification(user_id, current, new_status, matched_rule)
                if tier_changed
                else None
            )
            return cls.Updated(saved, current, notification)

        return cls.NotChanged(current)

    @staticmethod
    def _build_notification(
        user_id: UserId,
        old: UserStatus | None,
        new: UserStatus,
        rule: VipRule | None,
    ) -> TierChangeNotification:
        old_tier = old.tier if old else None
        new_tier = new.tier

        if old_tier is None and new_tier is not None:
            direction = "initial"
        elif old_tier is not None and new_tier is not None and new_tier > old_tier:
            direction = "upgrade"
        elif (old_tier is not None and new_tier is None) or (
            old_tier is not None and new_tier is not None and new_tier < old_tier
        ):
            direction = "downgrade"
        else:
            direction = "unchanged"

        vip_manager = None
        if new_tier and new_tier >= 4:
            vip_manager = "Senior VIP Manager" if new_tier >= 5 else "VIP Account Manager"

        return TierChangeNotification(
            user_id=user_id,
            direction=direction,
            previous_tier=old.rule_name if old else None,
            new_tier=new.rule_name,
            benefits=rule.benefits if rule else [],
            vip_manager_assigned=vip_manager,
        )


# ---------------------------------------------------------------------------
# Kafka consumers
# ---------------------------------------------------------------------------

MIN_BACKOFF = 10.0
MAX_BACKOFF = 180.0


class TransactionsEventProcessor:
    """
    Consumes transaction events from Kafka and triggers VIP recalculation.

    Each AccountsEvent (deposit, bet, withdrawal) can change a user's tier.
    The consumer processes events sequentially to preserve ordering.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        topic: str,
        repo: VipRepository,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._group_id = group_id
        self._topic = topic
        self._repo = repo

    async def start(self) -> None:
        backoff = MIN_BACKOFF
        while True:
            try:
                await self._consume_loop()
            except Exception as exc:  # noqa: BLE001
                log.error("transactions_consumer.error", error=str(exc))
                jitter = backoff * 0.2 * (2 * random.random() - 1)
                await asyncio.sleep(min(backoff + jitter, MAX_BACKOFF))
                backoff = min(backoff * 2, MAX_BACKOFF)

    async def _consume_loop(self) -> None:
        consumer = Consumer(
            {
                "bootstrap.servers": self._bootstrap,
                "group.id": self._group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        consumer.subscribe([self._topic])
        log.info("transactions_consumer.started", topic=self._topic)
        try:
            while True:
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise RuntimeError(str(msg.error()))
                try:
                    await self._process(msg.value())
                except Exception as exc:  # noqa: BLE001
                    log.error("transactions_consumer.dispatch_error", error=str(exc))
                consumer.commit(message=msg)
        finally:
            consumer.close()

    async def _process(self, raw: bytes) -> None:
        event = AccountsEvent.model_validate_json(raw)
        user_id = UserId(value=event.user_id)
        brand_id = BrandId(value=event.brand_id)
        trigger = LastChange(
            change_type=event.event_type,
            reference_id=None,
            timestamp=datetime.now(timezone.utc),
        )
        await EvaluateUserStatus.run(user_id, brand_id, trigger, self._repo)


class RecalculationCommandProcessor:
    """Processes explicit recalculation commands from an internal Kafka topic."""

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        topic: str,
        repo: VipRepository,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._group_id = group_id
        self._topic = topic
        self._repo = repo

    async def start(self) -> None:
        backoff = MIN_BACKOFF
        while True:
            try:
                await self._consume_loop()
            except Exception as exc:  # noqa: BLE001
                log.error("recalc_consumer.error", error=str(exc))
                jitter = backoff * 0.2 * (2 * random.random() - 1)
                await asyncio.sleep(min(backoff + jitter, MAX_BACKOFF))
                backoff = min(backoff * 2, MAX_BACKOFF)

    async def _consume_loop(self) -> None:
        consumer = Consumer(
            {
                "bootstrap.servers": self._bootstrap,
                "group.id": self._group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        consumer.subscribe([self._topic])
        log.info("recalc_consumer.started", topic=self._topic)
        try:
            while True:
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise RuntimeError(str(msg.error()))
                try:
                    cmd = RecalculateCommand.model_validate_json(msg.value())
                    trigger = LastChange(change_type="manual", timestamp=cmd.timestamp)
                    await EvaluateUserStatus.run(cmd.user_id, cmd.brand_id, trigger, self._repo)
                except Exception as exc:  # noqa: BLE001
                    log.error("recalc_consumer.dispatch_error", error=str(exc))
                consumer.commit(message=msg)
        finally:
            consumer.close()


# ---------------------------------------------------------------------------
# Midnight scheduler
# ---------------------------------------------------------------------------

class SchedulerFlow:
    """
    Batch midnight recalculation for all users.

    Runs at the configured start_hour:start_minute every day.
    Iterates all users for the configured brand and re-evaluates their VIP status.
    Tick interval controls how frequently the scheduler checks whether it's time to run.
    """

    def __init__(self, config: SchedulerConfig, repo: VipRepository) -> None:
        self._config = config
        self._repo = repo

    async def start(self) -> None:
        if not self._config.enabled:
            log.info("scheduler.disabled")
            return

        log.info(
            "scheduler.started",
            brand_id=self._config.brand_id,
            start_time=f"{self._config.start_hour:02d}:{self._config.start_minute:02d}",
        )

        last_run: datetime | None = None
        while True:
            now = datetime.now(timezone.utc)
            should_run = (
                now.hour == self._config.start_hour
                and now.minute == self._config.start_minute
                and (last_run is None or (now - last_run).total_seconds() > 60)
            )
            if should_run:
                log.info("scheduler.running", brand_id=self._config.brand_id)
                await self._run_batch()
                last_run = now
            await asyncio.sleep(self._config.clock_interval_seconds)

    async def _run_batch(self) -> None:
        brand_id = BrandId(value=self._config.brand_id)
        users = await self._repo.list_all_users_for_brand(brand_id)
        log.info("scheduler.batch_start", user_count=len(users))
        trigger = LastChange(change_type="scheduler", timestamp=datetime.now(timezone.utc))
        processed = 0
        for user_id in users:
            try:
                await EvaluateUserStatus.run(user_id, brand_id, trigger, self._repo)
                processed += 1
            except Exception as exc:  # noqa: BLE001
                log.error("scheduler.user_error", user_id=user_id.value, error=str(exc))
        log.info("scheduler.batch_done", processed=processed, total=len(users))
