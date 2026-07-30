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
Payment service business logic.

Covers:
- DepositProcessor: orchestrates the full deposit flow (resolve method -> PSP -> redirect)
- PaymentService: payment lifecycle management and Kafka event publishing
- KafkaMessageProducer: async Kafka publishing using confluent-kafka
- DepositConsumer: Kafka consumer with exponential-backoff restart resilience
- PaymentProviders: registry that maps PaymentProvider enum to provider implementations
- GatewayProxy: lightweight reverse-proxy for PSP callbacks

The key design insight from the Scala original: crediting a player's account after a
successful deposit is done *asynchronously* via Kafka, not synchronously in the PSP
callback. PSPs have strict response timeouts (typically 10s); Kafka provides
at-least-once delivery with automatic retry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

import structlog
from confluent_kafka import Consumer, KafkaError, Producer

from .models import (
    DepositMessage,
    DepositRequest,
    DepositResult,
    DepositToAccount,
    PaymentMethodVO,
    PaymentProvider,
    PaymentProviderInfo,
    PaymentStatus,
    PaymentStatusChangeMessage,
    PaymentVO,
    Redirection,
    TopicName,
    UserDetails,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract PSP provider -- every provider must implement this
# ---------------------------------------------------------------------------

class AbstractPaymentProvider(ABC):
    """
    The contract every PSP integration must satisfy.

    startPaymentProcess is the single method that every provider implements.
    The DepositProcessor delegates to it without knowing which PSP is involved.
    """

    @property
    @abstractmethod
    def provider_id(self) -> PaymentProvider:
        ...

    @abstractmethod
    async def start_payment_process(
        self,
        user_details: UserDetails,
        payment: PaymentVO,
        payment_method: PaymentMethodVO,
    ) -> Redirection:
        """Initiate payment with the external PSP; return redirect info."""
        ...

    async def build_deposit_details(self, user_id: int, brand_id: int) -> dict[str, Any]:
        """Override to supply provider-specific deposit metadata."""
        return {}


# ---------------------------------------------------------------------------
# Provider registry -- maps PaymentProvider enum -> provider instance
# ---------------------------------------------------------------------------

class PaymentProviders:
    """
    Registry that resolves a PaymentProvider enum value to a live provider
    instance. Providers are registered at startup and resolved by name/enum.

    Replaces the Scala Guice @Named binding pattern with a plain dict lookup.
    """

    def __init__(self) -> None:
        self._registry: dict[PaymentProvider, AbstractPaymentProvider] = {}

    def register(self, provider: AbstractPaymentProvider) -> None:
        self._registry[provider.provider_id] = provider

    def get_by_name(self, name: str) -> AbstractPaymentProvider:
        return self.get(PaymentProvider.get_by_name(name))

    def get(self, provider: PaymentProvider) -> AbstractPaymentProvider:
        impl = self._registry.get(provider)
        if impl is None:
            raise ValueError(f"No provider registered for: {provider}")
        return impl

    def get_by_payment_method(self, method: PaymentMethodVO) -> AbstractPaymentProvider:
        return self.get(method.provider_id)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

class PaymentSettings:
    """
    Provides per-provider and per-provider+brand settings.

    Settings are stored in the database (PAYMENT_PROVIDER_SETTINGS and
    PAYMENT_BRAND_SETTINGS tables), allowing operators to change API keys,
    redirect URLs, and feature flags without redeployment.
    """

    def __init__(self, db_settings: dict[str, Any]) -> None:
        self._provider_settings: dict[str, dict[str, str]] = db_settings.get(
            "per_provider", {}
        )
        self._brand_settings: dict[tuple[str, int], dict[str, str]] = {
            (k["provider"], k["brand"]): k["values"]
            for k in db_settings.get("per_provider_brand", [])
        }

    def setting_for_provider(self, provider_id: str, key: str, default: Any = None) -> Any:
        return self._provider_settings.get(provider_id, {}).get(key, default)

    def setting_for_brand(self, provider_id: str, brand_id: int, key: str, default: Any = None) -> Any:
        brand_val = self._brand_settings.get((provider_id, brand_id), {}).get(key)
        if brand_val is not None:
            return brand_val
        return self.setting_for_provider(provider_id, key, default)

    def callback_url(self, platform_url: str, provider_id: str, command: str) -> str:
        return f"{platform_url}/payment/{provider_id}/{command}"


# ---------------------------------------------------------------------------
# Kafka producer
# ---------------------------------------------------------------------------

class KafkaMessageProducer:
    """
    Publishes payment domain events to Kafka topics.

    Each message type is wrapped in a DepositMessage envelope so consumers
    can route by messageType without deserialising the full payload first.
    """

    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def send_deposit_to_account(self, msg: DepositToAccount) -> None:
        envelope = DepositMessage(
            message_type=msg.message_type,
            content=msg.model_dump_json(),
        )
        self._send(TopicName.DEPOSIT_TO_ACCOUNT.value, envelope)

    def send_payment_status_change(self, msg: PaymentStatusChangeMessage) -> None:
        envelope = DepositMessage(
            message_type=msg.message_type,
            content=msg.model_dump_json(),
        )
        self._send(TopicName.PAYMENT_STATUS_CHANGE.value, envelope)

    def _send(self, topic: str, envelope: DepositMessage) -> None:
        payload = envelope.model_dump_json().encode()
        self._producer.produce(topic, value=payload)
        self._producer.poll(0)  # trigger delivery callbacks

    def flush(self, timeout: float = 5.0) -> None:
        self._producer.flush(timeout)


# ---------------------------------------------------------------------------
# Payment DAO (interface stub -- real impl uses SQLAlchemy async)
# ---------------------------------------------------------------------------

class PaymentDAO:
    """
    Data access for the USER_PAYMENTS table.
    Concrete implementation lives in the SQLAlchemy layer.
    """

    async def next_payment_id(self) -> int:
        raise NotImplementedError

    async def add_payment(self, payment: PaymentVO) -> None:
        raise NotImplementedError

    async def update_payment(self, payment: PaymentVO) -> None:
        raise NotImplementedError

    async def find_by_id(self, payment_id: int) -> PaymentVO | None:
        raise NotImplementedError

    async def mark_abandoned(
        self, status: PaymentStatus, older_than: Any
    ) -> int:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Platform service (interface stub)
# ---------------------------------------------------------------------------

class PlatformServiceClient:
    async def get_user_details(self, user_id: int) -> UserDetails:
        raise NotImplementedError

    async def deposit_validate(self, user_id: int, amount: int) -> None:
        """Raise an exception if the deposit violates any limit."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# PaymentService -- lifecycle + Kafka bridge
# ---------------------------------------------------------------------------

class PaymentService:
    """
    Manages the payment lifecycle: creation, status transitions, and async
    account crediting via Kafka.

    Status transition diagram:
      STARTED -> PENDING -> PROCESSING_ON_ACCOUNT -> SUCCEEDED
                         \\-> FAILED
      Any non-terminal  -> ABANDONED  (bulk expiry job)
    """

    def __init__(
        self,
        payment_dao: PaymentDAO,
        kafka_producer: KafkaMessageProducer,
    ) -> None:
        self._dao = payment_dao
        self._kafka = kafka_producer

    async def new_payment(
        self,
        provider: PaymentProvider,
        deposit: DepositRequest,
    ) -> PaymentVO:
        """Create a payment record in STARTED status."""
        payment = PaymentVO(
            id=deposit.id,
            brand_id=deposit.brand_id,
            user_id=deposit.user_id,
            amount=deposit.amount,
            currency=deposit.currency,
            user_ip=deposit.ip_address,
            status=PaymentStatus.STARTED,
            payment_provider_info=PaymentProviderInfo(
                provider_id=provider,
                method=deposit.method,
                recurring_reference=deposit.recurring_detail_reference,
            ),
        )
        await self._dao.add_payment(payment)
        return payment

    async def change_payment_status(
        self, payment: PaymentVO, status: PaymentStatus
    ) -> None:
        """Transition to a new status and publish a Kafka notification."""
        if payment.status == status or payment.status.is_terminal():
            return

        payment.status = status
        self._kafka.send_payment_status_change(
            PaymentStatusChangeMessage(
                user_id=payment.user_id,
                brand_id=payment.brand_id,
                amount=payment.amount,
                status=status.value,
                payment_id=payment.id,
                currency=payment.currency,
            )
        )
        await self._dao.update_payment(payment)

    async def complete_payment(self, payment: PaymentVO, deposit_code: str) -> None:
        """
        Successful deposit: transition to PROCESSING_ON_ACCOUNT and publish
        a DepositToAccount Kafka message so the account service can credit
        the player's balance asynchronously.
        """
        if payment.status == PaymentStatus.SUCCEEDED:
            return
        await self.change_payment_status(payment, PaymentStatus.PROCESSING_ON_ACCOUNT)
        self._kafka.send_deposit_to_account(
            DepositToAccount(
                user_id=payment.user_id,
                amount=payment.amount,
                payment_id=payment.id,
                provider_id=payment.payment_provider_info.provider_id.value,
                payment_method=payment.payment_provider_info.payment_method or "N/A",
                ref=deposit_code,
            )
        )

    async def payment_failure(
        self, payment: PaymentVO, failure_type: str, reason: str
    ) -> None:
        payment.failure_info.failure_type = failure_type
        payment.failure_info.failure_reason = reason
        await self.change_payment_status(payment, PaymentStatus.FAILED)

    @staticmethod
    def redirect_type_mapping(payment_method: PaymentMethodVO) -> str | None:
        mapping = {
            "redirect_iframe": "iframe",
            "redirect_full_page": "fullPage",
            "custom": "iframe",
            "direct": None,
        }
        return mapping.get(payment_method.flow)


# ---------------------------------------------------------------------------
# DepositProcessor -- main orchestration layer
# ---------------------------------------------------------------------------

class DepositProcessor:
    """
    Orchestrates the complete deposit lifecycle for any PSP.

    Flow:
      1. Resolve payment method -> PSP provider
      2. Re-fetch fresh user details
      3. Validate deposit limits
      4. Create payment record (STARTED)
      5. Delegate to PSP's start_payment_process
      6. Return redirect info to the cashier
    """

    def __init__(
        self,
        platform_service: PlatformServiceClient,
        payment_providers: PaymentProviders,
        payment_dao: PaymentDAO,
        payment_service: PaymentService,
    ) -> None:
        self._platform = platform_service
        self._providers = payment_providers
        self._payment_dao = payment_dao
        self._payment_service = payment_service

    async def make_payment(
        self,
        user_details: UserDetails,
        method_name: str,
        amount: int,
        currency: str,
        ip_address: str,
        payment_methods: dict[str, PaymentMethodVO],
    ) -> DepositResult:
        """
        Main entry point for all deposits regardless of PSP.

        payment_methods is a name->PaymentMethodVO map (loaded from DB at request time).
        """
        payment_method = payment_methods.get(method_name)
        if payment_method is None:
            raise ValueError(f"Unknown payment method: {method_name}")

        return await self._make_payment_with_redirection(
            user_details, amount, currency, ip_address, payment_method
        )

    async def _make_payment_with_redirection(
        self,
        user_details: UserDetails,
        amount: int,
        currency: str,
        ip_address: str,
        payment_method: PaymentMethodVO,
    ) -> DepositResult:
        provider = self._providers.get_by_payment_method(payment_method)

        # Step 1: Re-fetch fresh user details
        fresh_user = await self._platform.get_user_details(user_details.id)

        # Step 2: Validate deposit limits (daily/weekly/monthly caps)
        await self._platform.deposit_validate(fresh_user.id, amount)

        # Step 3: Create payment record
        payment_id = await self._payment_dao.next_payment_id()
        deposit = DepositRequest(
            id=payment_id,
            brand_id=fresh_user.brand_id,
            user_id=fresh_user.id,
            amount=amount,
            currency=currency,
            ip_address=ip_address,
            method=payment_method.name,
        )
        payment = await self._payment_service.new_payment(
            provider.provider_id, deposit
        )

        # Step 4: Delegate to PSP
        redirection = await provider.start_payment_process(
            fresh_user, payment, payment_method
        )
        redirection.payment_id = payment.id

        return DepositResult(
            status="PENDING",
            payment_id=payment.id,
            redirect_url=redirection.url,
            redirect_method="POST" if redirection.post else "GET",
            redirect_type=PaymentService.redirect_type_mapping(payment_method),
            params=redirection.params,
        )


# ---------------------------------------------------------------------------
# Kafka consumer for deposit completion events
# ---------------------------------------------------------------------------

class DepositConsumer:
    """
    Consumes from DepositToAccountFinished topic.

    Uses exponential backoff reconnection (10s-180s with 20% jitter) to handle
    Kafka broker restarts and network partitions. Processes messages sequentially
    to preserve per-payment ordering within a partition.

    Message types handled:
      DEPOSIT_TO_ACCOUNT_FINISHED -> mark payment succeeded, send confirmation
      VOID_TO_ACCOUNT_FINISHED    -> mark payment voided
      (unknown types are logged and skipped)
    """

    MIN_BACKOFF = 10.0
    MAX_BACKOFF = 180.0
    JITTER_FACTOR = 0.2

    def __init__(
        self,
        bootstrap_servers: str,
        payment_service: PaymentService,
        payment_dao: PaymentDAO,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._payment_service = payment_service
        self._payment_dao = payment_dao
        self._topic = TopicName.DEPOSIT_TO_ACCOUNT_FINISHED.value
        self._group_id = "DEPOSIT_FINISHED_CONSUMER_GROUP1"

    async def start(self) -> None:
        """Run the consumer loop with exponential backoff on failure."""
        backoff = self.MIN_BACKOFF
        while True:
            try:
                await self._consume_loop()
            except Exception as exc:  # noqa: BLE001
                log.error("deposit_consumer.error", error=str(exc))
                jitter = backoff * self.JITTER_FACTOR * (2 * __import__("random").random() - 1)
                sleep_time = min(backoff + jitter, self.MAX_BACKOFF)
                log.info("deposit_consumer.reconnect", sleep_seconds=sleep_time)
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
        log.info("deposit_consumer.started", topic=self._topic, group=self._group_id)
        try:
            while True:
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise RuntimeError(f"Kafka error: {msg.error()}")
                await self._handle_message(msg.value())
                consumer.commit(message=msg)
        finally:
            consumer.close()

    async def _handle_message(self, raw: bytes) -> None:
        envelope = DepositMessage.model_validate_json(raw)
        mtype = envelope.message_type

        if mtype == "DEPOSIT_TO_ACCOUNT_FINISHED":
            payload = json.loads(envelope.content)
            payment_id = payload["paymentId"]
            payment = await self._payment_dao.find_by_id(payment_id)
            if payment:
                await self._payment_service.change_payment_status(
                    payment, PaymentStatus.SUCCEEDED
                )
        elif mtype == "VOID_TO_ACCOUNT_FINISHED":
            payload = json.loads(envelope.content)
            payment_id = payload["paymentId"]
            payment = await self._payment_dao.find_by_id(payment_id)
            if payment:
                await self._payment_service.change_payment_status(
                    payment, PaymentStatus.VOIDED
                )
        else:
            log.warning("deposit_consumer.unknown_message_type", message_type=mtype)


# ---------------------------------------------------------------------------
# Gateway proxy -- reverse proxy for PSP callbacks
# ---------------------------------------------------------------------------

class GatewayProxyService:
    """
    Lightweight reverse proxy for PSP (e.g. Adyen) callbacks.

    PSPs embed a 'forwardTo' parameter in merchantReturnData which tells this
    proxy which internal service instance should receive the callback. This
    allows the same public callback URL to serve multiple environments
    (staging vs production) and support blue/green deployments.
    """

    @staticmethod
    def extract_forward_target(form_data: dict[str, list[str]]) -> str:
        """
        Extract the internal host from merchantReturnData query params.

        The PSP echoes back whatever we put in merchantReturnData during the
        initial payment request; we embed the target host there.
        """
        import urllib.parse

        merchant_return_raw = form_data.get("merchantReturnData", [""])[0]
        merchant_return = dict(urllib.parse.parse_qsl(merchant_return_raw))
        target = merchant_return.get("forwardTo")
        if not target:
            raise ValueError("forwardTo not found in merchantReturnData")
        return target
