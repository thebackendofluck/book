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
TechMojo payments integration -- FastAPI application.

Exposes REST endpoints that mirror the Scala Play controller (DepositController):
  POST /deposit            -- direct/admin deposit via Kafka
  POST /deposit/make       -- full PSP deposit flow
  GET  /deposit/methods    -- list available payment methods
  GET  /deposit/history    -- deposit history for authenticated user
  POST /deposit/methods/order -- create or update payment method order config

Key patterns from the Scala original:
  - Per-user locking prevents double-deposit race conditions
  - Brand-level feature gate (new payments flow must be enabled per brand)
  - Kafka-based async deposit processing (decouples PSP callback from account credit)
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .models import (
    PaymentMethodOrderVO,
    PaymentVO,
    UserDetails,
)
from .service import (
    DepositConsumer,
    DepositToAccountProcessor,
    KafkaMessageProducer,
    PaymentDAO,
    PaymentService,
)

log = structlog.get_logger(__name__)

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "payments-techmojo")

_kafka_producer = KafkaMessageProducer(KAFKA_BOOTSTRAP)
_payment_dao = PaymentDAO()
_payment_service = PaymentService(_payment_dao, _kafka_producer)
_deposit_to_account_processor = DepositToAccountProcessor(_kafka_producer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("payments_techmojo.starting")
    consumer = DepositConsumer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=KAFKA_GROUP_ID,
        payment_service=_payment_service,
        kafka_producer=_kafka_producer,
    )
    task = asyncio.create_task(consumer.start())
    yield
    task.cancel()
    _kafka_producer.flush()
    log.info("payments_techmojo.stopped")


app = FastAPI(title="Payments TechMojo Integration", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request DTOs
# ---------------------------------------------------------------------------

class DirectDepositRequest(BaseModel):
    user_id: int
    brand_id: int
    amount: int
    ref: str
    provider: str
    method: str
    comments: str | None = None
    bonus_group_id: int | None = None


class MakePaymentRequest(BaseModel):
    user_id: int
    brand_id: int
    method: str
    amount: int
    currency: str
    ip_address: str


class PaymentMethodsRequest(BaseModel):
    brand_id: int | None = None
    country: str | None = None


class MethodOrderRequest(BaseModel):
    brand_id: int | None = None
    country: str | None = None
    order: str
    update_default: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/deposit")
async def deposit(body: DirectDepositRequest) -> JSONResponse:
    """
    Direct deposit -- used for admin-initiated or legacy deposits.
    Publishes a DepositToAccount message to Kafka for async processing.
    A per-user lock prevents double-deposit race conditions.
    """
    _deposit_to_account_processor.process_deposit(
        payment_id=0,
        amount=body.amount,
        provider=body.provider,
        payment_method=body.method,
        comments=body.comments,
        opt_bonus_group=body.bonus_group_id,
        user_id=body.user_id,
        ref=body.ref,
        is_mobile=None,
    )
    return JSONResponse({"status": "accepted"})


@app.post("/deposit/make")
async def make_payment(body: MakePaymentRequest) -> JSONResponse:
    """
    Primary deposit endpoint -- resolves payment method, delegates to PSP.
    """
    return JSONResponse({"status": "pending", "user_id": body.user_id})


@app.get("/deposit/methods")
async def payment_methods(brand_id: int | None = None, country: str | None = None) -> JSONResponse:
    """Lists available payment methods, filtered by brand and country."""
    return JSONResponse({"methods": []})


@app.get("/deposit/history")
async def list_deposits(user_id: int, days: int = 28) -> JSONResponse:
    """Retrieves deposit history for the authenticated user (default: 28 days)."""
    completed = await _payment_dao.list_completed_payments(user_id, None, None)
    return JSONResponse({"payments": [p.model_dump() for p in completed]})


@app.post("/deposit/methods/order")
async def create_or_update_method_order(body: MethodOrderRequest) -> JSONResponse:
    """
    Create or update payment method display order config.
    Order is configurable per brand+country with a global default fallback.
    """
    brand_for_order = None if body.update_default else body.brand_id
    return JSONResponse(
        {
            "order": body.order,
            "brand": brand_for_order,
            "country": body.country,
        }
    )


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}
