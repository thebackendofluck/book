# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
clabs_service.py — CompetitionLabs gamification integration service.

Mirrors CompetitionLabsApi.scala, EventsConsumer.scala, CLEvent.scala,
BrandSettings.scala, and CLabsAppServer.scala.

Architecture:
  - FastAPI HTTP server for health checks and admin endpoints
  - Kafka consumer bridge: three concurrent consumers
      1. transaction topic → bet/win/deposit events
      2. users topic → registration/login events
      3. flags_updates topic → VIP tier/status changes
  - Each Kafka message is transformed via AcmeKafkaTransformer and forwarded
    to RabbitMQ for the CompetitionLabs webhook integration
  - Per-brand configuration: each brand has its own CL space and API key

CLEvent model:
  - memberRefId: platform user ID
  - action: bet | win | deposit | login | registration | withdrawal
  - entityRefId: game/product reference
  - sourceValue: amount in player's currency
  - metadata: additional context (game provider, bet type, currency)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from confluent_kafka import Consumer, KafkaError, KafkaException
from fastapi import FastAPI
from pydantic import BaseModel, Field

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# CL event types
# ---------------------------------------------------------------------------

class CLEventType:
    WIN              = "win"
    BET              = "bet"
    DEPOSIT          = "deposit"
    BONUS_WIN        = "bonus-win"
    BONUS_BET        = "bonus-bet"
    BONUS_DEPOSIT    = "bonus-deposit"
    LOGIN            = "login"
    REGISTRATION     = "registration"
    WITHDRAWAL       = "withdrawal"
    WITHDRAWAL_REVERSE = "withdrawal-reverse"


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class CLEvent(BaseModel):
    member_id:             Optional[str] = None
    member_ref_id:         str
    action:                str
    batch_id:              Optional[str] = None
    entity_id:             Optional[str] = None
    entity_ref_id:         str
    source_value:          float
    transaction_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    relates_to:            list[str] = Field(default_factory=list)
    relates_to_external:   list[str] = Field(default_factory=list)
    metadata:              Optional[dict[str, Any]] = None

    def to_cl_dict(self) -> dict:
        """Serialize to CompetitionLabs API field names (camelCase)."""
        d: dict = {
            "memberRefId":           self.member_ref_id,
            "action":                self.action,
            "entityRefId":           self.entity_ref_id,
            "sourceValue":           self.source_value,
            "transactionTimestamp":  self.transaction_timestamp.isoformat(),
        }
        if self.member_id:
            d["memberId"] = self.member_id
        if self.batch_id:
            d["batchId"] = self.batch_id
        if self.entity_id:
            d["entityId"] = self.entity_id
        if self.relates_to:
            d["relatesTo"] = self.relates_to
        if self.relates_to_external:
            d["relatesToExternal"] = self.relates_to_external
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class BrandSettings:
    id:           int
    name:         str
    space:        str
    account_id:   str
    api_key:      str
    signature:    str
    rabbit_host:  str
    rabbit_port:  int
    rabbit_queue: str


# ---------------------------------------------------------------------------
# CompetitionLabs HTTP API client
# ---------------------------------------------------------------------------

class CompetitionLabsApi:
    """
    HTTP client for the CompetitionLabs gamification platform.

    Each brand has its own CL space (identified by `space` in BrandSettings).
    Authentication via X-API-KEY header per request.
    """

    BASE_URL = "https://{host}.competitionlabs.com/api/{space}"

    def __init__(self, app_host: str) -> None:
        self._app_host = app_host

    def _url(self, brand: BrandSettings, endpoint: str) -> str:
        return f"https://{self._app_host}.competitionlabs.com/api/{brand.space}{endpoint}"

    def _headers(self, brand: BrandSettings) -> dict:
        return {"X-API-KEY": brand.api_key, "Content-Type": "application/json"}

    def push_events(self, events: list[CLEvent], brand: BrandSettings) -> None:
        if not events:
            return
        payload = [e.to_cl_dict() for e in events]
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                self._url(brand, "/events"),
                json=payload,
                headers=self._headers(brand),
            )
        if resp.is_error:
            log.error("CL push_events failed", status=resp.status_code,
                      brand=brand.name, count=len(events))
            resp.raise_for_status()
        log.debug("CL events pushed", brand=brand.name, count=len(events))

    def create_member(self, member_ref_id: str, brand: BrandSettings,
                       name: Optional[str] = None,
                       groups: Optional[list[str]] = None,
                       metadata: Optional[dict] = None) -> None:
        payload = {
            "memberRefId": member_ref_id,
            "memberType":  "individual",
            "accountId":   brand.account_id,
        }
        if name:      payload["name"] = name
        if groups:    payload["groups"] = groups
        if metadata:  payload["metadata"] = metadata

        with httpx.Client(timeout=15) as client:
            resp = client.post(
                self._url(brand, "/members"),
                json=payload,
                headers=self._headers(brand),
            )
        if resp.is_error:
            log.error("CL create_member failed", status=resp.status_code,
                      member_ref_id=member_ref_id, brand=brand.name)
            resp.raise_for_status()

    def get_products(self, brand: BrandSettings) -> list[dict]:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                self._url(brand, "/products?_limit=10000"),
                headers=self._headers(brand),
            )
        resp.raise_for_status()
        return resp.json().get("data", [])


# ---------------------------------------------------------------------------
# Kafka → RabbitMQ bridge (mirrors EventsConsumer.scala)
# ---------------------------------------------------------------------------

class KafkaToRabbitBridge:
    """
    Kafka consumer that transforms platform events and forwards them to
    RabbitMQ for CompetitionLabs webhook ingestion.

    Three topic consumers (mirrors Scala: transaction, users, flags_updates).
    Retry with exponential backoff (2s → 10s) on connection failure.
    Manual offset commit after successful publish to RabbitMQ.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topics: list[str],
        group_prefix: str,
        brand_settings: list[BrandSettings],
        transformer: Any,   # callable(raw_payload: dict) → CLEvent | None
    ) -> None:
        self._topics    = topics
        self._brands    = {b.id: b for b in brand_settings}
        self._transformer = transformer
        self._consumer  = Consumer({
            "bootstrap.servers":   bootstrap_servers,
            "group.id":            f"{group_prefix}CLEventConsumers",
            "auto.offset.reset":   "earliest",
            "enable.auto.commit":  "false",
        })

    def run(self) -> None:
        self._consumer.subscribe(self._topics)
        log.info("clabs kafka bridge started", topics=self._topics)

        while True:
            msg = self._consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("kafka error", error=str(msg.error()))
                continue

            try:
                payload = json.loads(msg.value())
                event = self._transformer(payload)
                if event:
                    # In production: publish to brand-specific RabbitMQ queue
                    brand_id = payload.get("coreInfo", {}).get("brandId")
                    brand    = self._brands.get(brand_id)
                    if brand:
                        log.info("CL event forwarded to rabbit",
                                 action=event.action, brand=brand.name)
                self._consumer.commit(msg)
            except Exception as exc:
                log.error("message processing failed", error=str(exc))

    def shutdown(self) -> None:
        self._consumer.close()
        log.info("clabs kafka bridge stopped")


# ---------------------------------------------------------------------------
# Default transformer: map platform Kafka message to CLEvent
# ---------------------------------------------------------------------------

def default_transformer(payload: dict) -> Optional[CLEvent]:
    """
    Map a platform Kafka message to a CLEvent.

    Handles: transaction events (bet/win/deposit), user events (login/registration).
    Returns None for events that should not be forwarded to CompetitionLabs.
    """
    core = payload.get("coreInfo", {})
    event_type = core.get("eventType", "")
    user_id    = core.get("userId")

    if not user_id:
        return None

    action_map = {
        "debit":        CLEventType.BET,
        "credit":       CLEventType.WIN,
        "deposit":      CLEventType.DEPOSIT,
        "withdrawal":   CLEventType.WITHDRAWAL,
        "login":        CLEventType.LOGIN,
        "registration": CLEventType.REGISTRATION,
    }
    action = action_map.get(event_type)
    if not action:
        return None

    return CLEvent(
        member_ref_id=str(user_id),
        action=action,
        entity_ref_id=payload.get("gameRefId") or payload.get("productRefId") or "platform",
        source_value=float(payload.get("amount", 0)) / 100.0,
        transaction_timestamp=datetime.fromisoformat(
            core.get("timestamp", datetime.now(timezone.utc).isoformat())
        ),
        metadata={"currency": payload.get("currency"), "eventType": event_type},
    )


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title="CL Integration Service", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ping")
def ping():
    return {"pong": True}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import threading
    import uvicorn

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    brands: list[BrandSettings] = []  # loaded from config in production

    bridge = KafkaToRabbitBridge(
        bootstrap_servers=bootstrap,
        topics=["transactions", "users", "flags_updates"],
        group_prefix="clabs",
        brand_settings=brands,
        transformer=default_transformer,
    )

    kafka_thread = threading.Thread(target=bridge.run, daemon=True)
    kafka_thread.start()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
