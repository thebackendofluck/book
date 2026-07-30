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
Optimove TnT integration services.

Provides:
- OptimoveTnTClient: HTTP client for the Optimove Track and Trigger API
- EventSender: filters events (user consent + include list) and dispatches to Optimove
- BrokerConsumer: Kafka consumer with restart backoff for both main and error topics
- Application: wires everything together and starts concurrent consumers

Key design from the Scala original:
  Two independent Kafka consumer streams run concurrently:
    1. Main topic consumer (user events)
    2. Main topic consumer (transaction events)
  If processErrorTopics=true, two additional consumers run for error topics.

  User filtering:
    - An include-list filter allows testing with a subset of users in staging
    - Events with excludedFromMarketing=true are always dropped
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any, Callable, Awaitable

import httpx
import structlog
from confluent_kafka import Consumer, KafkaError, Producer

from .models import (
    AppConfig,
    DomainEvent,
    EventProcessedResult,
    OptimoveEvent,
    OptimoveLicenseeSetting,
    UserId,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Optimove TnT API client
# ---------------------------------------------------------------------------

class OptimoveTnTClient:
    """
    HTTP client for the Optimove Track and Trigger (TnT) API.

    Each event is sent to the licensee-specific endpoint with the tenant ID
    and event data. The client handles retries and error reporting.
    """

    def __init__(self, licensee_settings: list[OptimoveLicenseeSetting]) -> None:
        self._settings_by_licensee: dict[str, OptimoveLicenseeSetting] = {
            s.licensee_name: s for s in licensee_settings
        }
        self._client = httpx.AsyncClient(timeout=30.0)

    async def send_event(
        self, licensee_name: str | None, event: OptimoveEvent
    ) -> dict[str, Any] | None:
        """
        Send an event to Optimove. Returns None if licensee is not configured.
        """
        if licensee_name is None:
            return None
        setting = self._settings_by_licensee.get(licensee_name)
        if setting is None:
            log.warning("optimove.licensee_not_configured", licensee=licensee_name)
            return None

        url = f"{setting.url}/tenants/{setting.tenant}/events"
        response = await self._client.post(url, json=event.model_dump())
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Event sender (filter + dispatch)
# ---------------------------------------------------------------------------

class EventSender:
    """
    Filters domain events and sends them to Optimove.

    Filtering rules:
      1. If user is in the include-list (or list is empty = all users): allow
      2. If event.excluded_from_marketing is True: drop
      3. Otherwise: send
    """

    def __init__(
        self,
        user_include_filter: Callable[[UserId], bool],
        optimove_client: OptimoveTnTClient,
    ) -> None:
        self._user_filter = user_include_filter
        self._client = optimove_client

    async def process(self, events: list[DomainEvent]) -> EventProcessedResult:
        filtered = [
            e for e in events
            if self._user_filter(e.user_id)
            and not e.excluded_from_marketing
        ]
        if not filtered:
            return EventProcessedResult.IGNORED

        # Convert domain events to Optimove API DTOs
        om_events = [self._to_optimove_event(e) for e in filtered]
        licensee = filtered[0].licensee_name if filtered else None

        try:
            for om_event in om_events:
                result = await self._client.send_event(licensee, om_event)
                if result is None:
                    return EventProcessedResult.LICENSEE_NOT_CONFIGURED
        except httpx.HTTPStatusError as exc:
            log.error("optimove.send_failed", status=exc.response.status_code)
            return EventProcessedResult.FAILURE
        except Exception as exc:  # noqa: BLE001
            log.error("optimove.send_error", error=str(exc))
            return EventProcessedResult.FAILURE

        return EventProcessedResult.SENT

    @staticmethod
    def _to_optimove_event(event: DomainEvent) -> OptimoveEvent:
        return OptimoveEvent(
            customer_id=str(event.user_id),
            timestamp=event.timestamp.isoformat(),
            event_type_name=type(event).__name__,
            params={
                "brand_id": event.brand_id,
                "brand_name": event.brand_name,
                "country": event.country,
                "language": event.language,
            },
        )


# ---------------------------------------------------------------------------
# Kafka broker consumer
# ---------------------------------------------------------------------------

class BrokerConsumer:
    """
    Kafka consumer with exponential backoff restart resilience.

    Mirrors the Scala BrokerConsumer built on fs2-kafka:
      - Each consumer runs on its own topic/group pair
      - Backoff range: 10s -> 180s with 20% jitter
      - Processes one batch of events at a time and sends to EventSender
    """

    MIN_BACKOFF = 10.0
    MAX_BACKOFF = 180.0
    JITTER_FACTOR = 0.2

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        topic: str,
        event_sender: EventSender,
        deserialise: Callable[[bytes], list[DomainEvent]],
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._group_id = group_id
        self._topic = topic
        self._sender = event_sender
        self._deserialise = deserialise

    async def start(self) -> None:
        backoff = self.MIN_BACKOFF
        while True:
            try:
                await self._consume_loop()
            except Exception as exc:  # noqa: BLE001
                log.error("broker_consumer.error", topic=self._topic, error=str(exc))
                jitter = backoff * self.JITTER_FACTOR * (2 * random.random() - 1)
                sleep_time = min(backoff + jitter, self.MAX_BACKOFF)
                await asyncio.sleep(sleep_time)
                backoff = min(backoff * 2, self.MAX_BACKOFF)

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
        log.info("broker_consumer.started", topic=self._topic, group=self._group_id)
        try:
            while True:
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise RuntimeError(f"Kafka error: {msg.error()}")
                try:
                    events = self._deserialise(msg.value())
                    result = await self._sender.process(events)
                    log.debug("broker_consumer.processed", topic=self._topic, result=result)
                except Exception as exc:  # noqa: BLE001
                    log.error("broker_consumer.dispatch_error", error=str(exc))
                consumer.commit(message=msg)
        finally:
            consumer.close()


# ---------------------------------------------------------------------------
# Include-user filter
# ---------------------------------------------------------------------------

class IncludeUserFilter:
    """
    If the include list is non-empty, only process events for listed users.
    If empty, allow all users (production default).
    """

    def __init__(self, include_user_ids: list[UserId]) -> None:
        self._ids: set[UserId] = set(include_user_ids)

    def validate(self, user_id: UserId) -> bool:
        if not self._ids:
            return True
        return user_id in self._ids


# ---------------------------------------------------------------------------
# Application entrypoint
# ---------------------------------------------------------------------------

class Application:
    """
    Wires all components and starts concurrent Kafka consumers.

    Consumers started:
      - user events (main topic)
      - transaction events (main topic)
      - user events (error topic)      -- if process_error_topics=True
      - transaction events (error topic) -- if process_error_topics=True
    """

    KAFKA_GROUP_ID = "optimove-tnt"

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    async def start(self) -> None:
        config = self._config
        client = OptimoveTnTClient(config.optimove)
        include_filter = IncludeUserFilter(config.om_include_filter)
        sender = EventSender(include_filter.validate, client)

        consumers = self._build_consumers(config, sender)

        try:
            await asyncio.gather(*[c.start() for c in consumers])
        finally:
            await client.aclose()

    def _build_consumers(
        self, config: AppConfig, sender: EventSender
    ) -> list[BrokerConsumer]:
        kafka = config.kafka
        consumers = [
            BrokerConsumer(
                kafka.bootstrap,
                self.KAFKA_GROUP_ID,
                kafka.consumer_users.topic,
                sender,
                self._deserialise_user_event,
            ),
            BrokerConsumer(
                kafka.bootstrap,
                self.KAFKA_GROUP_ID,
                kafka.consumer_transactions.topic,
                sender,
                self._deserialise_transaction_event,
            ),
        ]
        if config.process_error_topics:
            consumers += [
                BrokerConsumer(
                    kafka.bootstrap,
                    self.KAFKA_GROUP_ID,
                    kafka.consumer_users.error_topic,
                    sender,
                    self._deserialise_user_event,
                ),
                BrokerConsumer(
                    kafka.bootstrap,
                    self.KAFKA_GROUP_ID,
                    kafka.consumer_transactions.error_topic,
                    sender,
                    self._deserialise_transaction_event,
                ),
            ]
        return consumers

    @staticmethod
    def _deserialise_user_event(raw: bytes) -> list[DomainEvent]:
        from .models import (
            UserRegistration, UserLogin, UserDeposited, DepositFailed,
            UserActivation, MarketingPreferencesUpdated,
        )
        data = json.loads(raw)
        event_type = data.pop("event_type", None)
        type_map = {
            "UserRegistration": UserRegistration,
            "UserLogin": UserLogin,
            "UserDeposited": UserDeposited,
            "DepositFailed": DepositFailed,
            "UserActivation": UserActivation,
            "MarketingPreferencesUpdated": MarketingPreferencesUpdated,
        }
        cls = type_map.get(event_type)
        if cls is None:
            log.warning("optimove.unknown_user_event_type", event_type=event_type)
            return []
        return [cls.model_validate(data)]

    @staticmethod
    def _deserialise_transaction_event(raw: bytes) -> list[DomainEvent]:
        from .models import (
            CashWin, BonusWin, Withdraw, WithdrawAccepted, WithdrawReversed,
            CashDebit, ReleasedBonus,
        )
        data = json.loads(raw)
        event_type = data.pop("event_type", None)
        type_map = {
            "CashWin": CashWin,
            "BonusWin": BonusWin,
            "Withdraw": Withdraw,
            "WithdrawAccepted": WithdrawAccepted,
            "WithdrawReversed": WithdrawReversed,
            "CashDebit": CashDebit,
            "ReleasedBonus": ReleasedBonus,
        }
        cls = type_map.get(event_type)
        if cls is None:
            log.warning("optimove.unknown_transaction_event_type", event_type=event_type)
            return []
        return [cls.model_validate(data)]
