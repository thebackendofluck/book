# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
TechMojo payments integration services.

Key components:
- KafkaMessageProducer: publishes deposit and matrix-score events
- DepositToAccountProcessor: bridges deposit confirmation with Kafka
- DepositConsumer: consumes deposit lifecycle events with restart resilience
- PaymentService: payment lifecycle management
- PaymentDAO: data access for USER_PAYMENTS table

The consumer pattern mirrors the Scala original:
  Exponential backoff (10s -> 180s, 20% jitter) via a restart loop.
  Sequential processing (one message at a time) preserves ordering within partitions.
  Offsets committed only after successful processing.

Post-deposit pipeline:
  onDepositToAccountFinished:
    1. Mark payment SUCCEEDED
    2. Send UpdateMatrixScores Kafka message
    3. (Optional) Send confirmation email if brand setting enabled
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Callable, Awaitable

import structlog
from confluent_kafka import Consumer, KafkaError, Producer

from .models import (
    DepositConsumerMessageName,
    DepositMessage,
    DepositToAccount,
    DepositToAccountFinished,
    PaymentStatus,
    PaymentVO,
    TopicName,
    UpdateMatrixScores,
    VoidToAccount,
    VoidToAccountFinished,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Kafka producer
# ---------------------------------------------------------------------------

class KafkaMessageProducer:
    """Publishes payment domain events to Kafka topics."""

    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def send_deposit_to_account(self, msg: DepositToAccount) -> None:
        envelope = DepositMessage(
            message_type=msg.message_type,
            content=msg.model_dump_json(),
        )
        self._send(TopicName.DEPOSIT_TO_ACCOUNT.value, envelope)

    def send_void_to_account(self, msg: VoidToAccount) -> None:
        envelope = DepositMessage(
            message_type=msg.message_type,
            content=msg.model_dump_json(),
        )
        self._send(TopicName.VOID_TO_ACCOUNT.value, envelope)

    def send_update_matrix_scores(self, msg: UpdateMatrixScores) -> None:
        envelope = DepositMessage(
            message_type=msg.message_type,
            content=msg.model_dump_json(),
        )
        self._send(TopicName.DEPOSIT_TO_ACCOUNT.value, envelope)

    def _send(self, topic: str, envelope: DepositMessage) -> None:
        self._producer.produce(topic, value=envelope.model_dump_json().encode())
        self._producer.poll(0)

    def flush(self, timeout: float = 5.0) -> None:
        self._producer.flush(timeout)


# ---------------------------------------------------------------------------
# DepositToAccountProcessor
# ---------------------------------------------------------------------------

class DepositToAccountProcessor:
    """
    Bridges deposit confirmation with async Kafka messaging.

    When a deposit is confirmed (via PSP callback or direct admin action),
    this processor publishes a Kafka message that the platform's account service
    consumes to credit the player's balance. Decoupling is critical:
    PSP callbacks have strict timeout requirements (typically 10s);
    account crediting involves multiple DB operations.
    """

    def __init__(self, kafka_producer: KafkaMessageProducer) -> None:
        self._kafka = kafka_producer

    def process_deposit(
        self,
        payment_id: int,
        amount: int,
        provider: str,
        payment_method: str,
        comments: str | None,
        opt_bonus_group: int | None,
        user_id: int,
        ref: str,
        is_mobile: bool | None,
        params: dict[str, str] | None = None,
        extra_params: dict[str, str] | None = None,
        auth_code: str | None = None,
    ) -> None:
        msg = DepositToAccount(
            user_id=user_id,
            amount=amount,
            ref=ref,
            is_mobile_payment=is_mobile,
            payment_id=payment_id,
            provider_id=provider,
            comment=comments,
            payment_bonus_group_id=opt_bonus_group,
            payment_method=payment_method,
            params=params or {},
            extra_params=extra_params or {},
            auth_code=auth_code,
        )
        self._kafka.send_deposit_to_account(msg)

    def process_void(
        self,
        payment_id: int,
        amount: int,
        provider: str,
        comments: str | None,
        user_id: int,
        params: dict[str, str] | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> None:
        msg = VoidToAccount(
            user_id=user_id,
            amount=amount,
            payment_id=payment_id,
            provider_id=provider,
            comment=comments,
            params=params or {},
            extra_params=extra_params or {},
        )
        self._kafka.send_void_to_account(msg)


# ---------------------------------------------------------------------------
# Payment DAO interface
# ---------------------------------------------------------------------------

class PaymentDAO:
    async def find_by_id(self, payment_id: int) -> PaymentVO | None:
        raise NotImplementedError

    async def update_payment(self, payment: PaymentVO) -> None:
        raise NotImplementedError

    async def add_payment(self, payment: PaymentVO) -> None:
        raise NotImplementedError

    async def list_completed_payments(
        self,
        user_id: int,
        from_dt: Any,
        to_dt: Any,
    ) -> list[PaymentVO]:
        raise NotImplementedError

    async def mark_abandoned(self, status: PaymentStatus, older_than: Any) -> int:
        raise NotImplementedError

    async def merge_owner_name(
        self, user_id: int, provider_id: str, name: str, approved: bool
    ) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# PaymentService
# ---------------------------------------------------------------------------

class PaymentService:
    """Manages payment lifecycle transitions."""

    def __init__(
        self,
        payment_dao: PaymentDAO,
        kafka_producer: KafkaMessageProducer,
    ) -> None:
        self._dao = payment_dao
        self._kafka = kafka_producer

    async def on_payment(
        self,
        payment_id: int,
        handler: Callable[[PaymentVO], Awaitable[None]],
    ) -> None:
        """Fetch payment by id and invoke the handler; no-op if not found."""
        payment = await self._dao.find_by_id(payment_id)
        if payment is not None:
            await handler(payment)

    async def payment_succeeded(self, payment: PaymentVO) -> None:
        if payment.status.is_terminal():
            return
        payment.status = PaymentStatus.SUCCEEDED
        await self._dao.update_payment(payment)

    async def payment_voided(self, payment: PaymentVO) -> None:
        if payment.status.is_terminal():
            return
        payment.status = PaymentStatus.VOIDED
        await self._dao.update_payment(payment)

    async def send_confirmation_email(
        self, payment: PaymentVO, extra_params: dict[str, Any]
    ) -> None:
        """Stub -- real implementation calls the mailer service."""
        log.info("payment_service.confirmation_email", payment_id=payment.id)


# ---------------------------------------------------------------------------
# Deposit consumer -- Kafka consumer with restart resilience
# ---------------------------------------------------------------------------

class DepositConsumer:
    """
    Kafka consumer for deposit lifecycle events.

    Listens on the DepositToAccountFinished topic and dispatches by messageType:
      DEPOSIT_TO_ACCOUNT_FINISHED -> mark succeeded, update matrix scores, email
      UPDATE_MATRIX_SCORE_FINISHED -> acknowledgement (no-op)
      RECORD_REFUSAL_FINISHED      -> acknowledgement (no-op)
      VOID_TO_ACCOUNT_FINISHED     -> mark voided, update matrix scores

    Sequential processing (one msg at a time) ensures ordering within a partition.
    Exponential backoff (10s -> 180s, 20% jitter) handles broker restarts and
    network partitions without thundering-herd effects.
    """

    MIN_BACKOFF = 10.0
    MAX_BACKOFF = 180.0
    JITTER_FACTOR = 0.2

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        payment_service: PaymentService,
        kafka_producer: KafkaMessageProducer,
        brand_settings: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._group_id = group_id
        self._payment_service = payment_service
        self._kafka = kafka_producer
        self._brand_settings = brand_settings or {}
        self._topic = TopicName.DEPOSIT_TO_ACCOUNT_FINISHED.value

    async def start(self) -> None:
        backoff = self.MIN_BACKOFF
        while True:
            try:
                log.info(
                    "deposit_consumer.starting",
                    topic=self._topic,
                    group_id=self._group_id,
                )
                await self._consume_loop()
            except Exception as exc:  # noqa: BLE001
                log.error("deposit_consumer.error", error=str(exc))
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
                    await self._dispatch(msg.value())
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "deposit_consumer.dispatch_error",
                        error=str(exc),
                    )
                consumer.commit(message=msg)
        finally:
            consumer.close()

    async def _dispatch(self, raw: bytes) -> None:
        envelope = DepositMessage.model_validate_json(raw)
        log.info("deposit_consumer.message", message_type=envelope.message_type)

        mtype = envelope.message_type
        if mtype == DepositConsumerMessageName.DEPOSIT_TO_ACCOUNT_FINISHED:
            finished = DepositToAccountFinished.model_validate_json(envelope.content)
            await self._on_deposit_finished(
                finished.user_id,
                finished.brand_id,
                finished.params,
                {},
                finished.payment_id,
            )

        elif mtype == DepositConsumerMessageName.VOID_TO_ACCOUNT_FINISHED:
            finished = VoidToAccountFinished.model_validate_json(envelope.content)
            await self._on_void_finished(
                finished.user_id,
                finished.brand_id,
                finished.params,
                {},
                finished.payment_id,
            )

        elif mtype in (
            DepositConsumerMessageName.UPDATE_MATRIX_SCORE_FINISHED,
            DepositConsumerMessageName.RECORD_REFUSAL_FINISHED,
        ):
            log.info("deposit_consumer.ack", message_type=mtype)

        else:
            raise RuntimeError(f"Unknown message type: {mtype}")

    async def _on_deposit_finished(
        self,
        user_id: int,
        brand_id: int,
        params: dict[str, str],
        extra_params: dict[str, Any],
        payment_id: int,
    ) -> None:
        """
        Post-deposit pipeline:
          1. Mark payment as SUCCEEDED
          2. Send UpdateMatrixScores event to Kafka
          3. Send confirmation email if brand setting enabled
        """
        async def handler(payment: PaymentVO) -> None:
            if payment_id != 0:
                await self._payment_service.payment_succeeded(payment)

            matrix_msg = UpdateMatrixScores(
                user_id=user_id,
                brand_id=brand_id,
            )
            self._kafka.send_update_matrix_scores(matrix_msg)

            send_email = self._brand_settings.get(brand_id, {}).get(
                "send_deposit_email", False
            )
            if send_email:
                await self._payment_service.send_confirmation_email(payment, extra_params)

        await self._payment_service.on_payment(payment_id, handler)

    async def _on_void_finished(
        self,
        user_id: int,
        brand_id: int,
        params: dict[str, str],
        extra_params: dict[str, Any],
        payment_id: int,
    ) -> None:
        async def handler(payment: PaymentVO) -> None:
            if payment_id != 0:
                await self._payment_service.payment_voided(payment)
            matrix_msg = UpdateMatrixScores(user_id=user_id, brand_id=brand_id)
            self._kafka.send_update_matrix_scores(matrix_msg)

        await self._payment_service.on_payment(payment_id, handler)
