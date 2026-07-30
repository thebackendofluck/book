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
Payments service -- FastAPI application.

Exposes REST endpoints consumed by the cashier frontend:
  POST /deposit/make       -- initiate a deposit with PSP redirection
  GET  /deposit/methods    -- list available payment methods for the user
  GET  /deposit/history    -- user's deposit history
  POST /callback/proxy     -- PSP callback reverse-proxy

The gateway proxy route (POST /) forwards PSP callbacks to the correct
internal service instance based on the forwardTo embedded in merchantReturnData.
"""

from __future__ import annotations

import os
import urllib.parse
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .models import PaymentMethodVO, PaymentProvider, PaymentStatus, UserDetails
from .service import (
    DepositConsumer,
    DepositProcessor,
    GatewayProxyService,
    KafkaMessageProducer,
    PaymentDAO,
    PaymentProviders,
    PaymentService,
    PlatformServiceClient,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
PLATFORM_URL = os.getenv("PLATFORM_URL", "http://platform.internal:8080")
SERVICE_HOST = os.getenv("SERVICE_HOST", "0.0.0.0")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8080"))


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------

# These would be wired with real DB-backed implementations in production.
_kafka_producer = KafkaMessageProducer(KAFKA_BOOTSTRAP)
_payment_dao = PaymentDAO()  # stub -- swap with SQLAlchemy implementation
_payment_service = PaymentService(_payment_dao, _kafka_producer)
_platform_client = PlatformServiceClient()  # stub
_payment_providers = PaymentProviders()  # providers registered at startup
_deposit_processor = DepositProcessor(
    _platform_client, _payment_providers, _payment_dao, _payment_service
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("payments_service.starting")
    # Start Kafka consumer in the background
    import asyncio
    consumer = DepositConsumer(KAFKA_BOOTSTRAP, _payment_service, _payment_dao)
    task = asyncio.create_task(consumer.start())
    yield
    task.cancel()
    _kafka_producer.flush()
    log.info("payments_service.stopped")


app = FastAPI(title="Payments Service", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / Response DTOs
# ---------------------------------------------------------------------------

class MakePaymentRequest(BaseModel):
    user_id: int
    brand_id: int
    method: str
    amount: int
    currency: str
    ip_address: str


class DepositHistoryRequest(BaseModel):
    user_id: int
    days: int = 28


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/deposit/make")
async def make_payment(body: MakePaymentRequest, request: Request) -> JSONResponse:
    """
    Initiate a deposit for a user.

    Returns a redirect URL and parameters that the cashier frontend uses to
    send the player to the PSP's hosted payment page.
    """
    user = UserDetails(
        id=body.user_id,
        brand_id=body.brand_id,
        currency=body.currency,
        country="GB",  # resolved from user profile in production
    )
    # In production, payment methods come from the DB (PaymentMethodDAO)
    payment_methods: dict[str, PaymentMethodVO] = {}

    try:
        result = await _deposit_processor.make_payment(
            user_details=user,
            method_name=body.method,
            amount=body.amount,
            currency=body.currency,
            ip_address=body.ip_address,
            payment_methods=payment_methods,
        )
        return JSONResponse(result.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/deposit/methods")
async def payment_methods(user_id: int, brand_id: int) -> JSONResponse:
    """
    List available payment methods for this user / brand combination.
    Order is determined by brand+country priority chain.
    """
    # Loaded from PaymentMethodDAO in production
    methods: list[dict[str, Any]] = []
    return JSONResponse({"methods": methods})


@app.get("/deposit/history")
async def deposit_history(user_id: int, days: int = 28) -> JSONResponse:
    """Return completed deposits for the user over the last N days."""
    payments = await _payment_dao.find_by_id(user_id)  # stub
    return JSONResponse({"payments": []})


@app.post("/callback/proxy")
async def callback_proxy(request: Request) -> Response:
    """
    Reverse proxy for PSP callbacks.

    Parses merchantReturnData from the callback body to extract the forwardTo
    parameter, then streams the request to the internal service instance.
    """
    body = await request.body()
    body_str = body.decode("utf-8")
    form_data = dict(urllib.parse.parse_qsl(body_str))

    try:
        target_host = GatewayProxyService.extract_forward_target(
            {k: [v] for k, v in form_data.items()}
        )
    except ValueError as exc:
        log.error("proxy.bad_request", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    target_url = f"http://{target_host}:8080/"

    async with httpx.AsyncClient(follow_redirects=False) as client:
        try:
            proxy_response = await client.request(
                method=request.method,
                url=target_url,
                headers=dict(request.headers),
                content=body,
                params=dict(request.query_params),
            )
        except httpx.RequestError as exc:
            log.error("proxy.forward_error", target=target_url, error=str(exc))
            return Response(status_code=502)

    return Response(
        content=proxy_response.content,
        status_code=proxy_response.status_code,
        headers=dict(proxy_response.headers),
        media_type=proxy_response.headers.get("content-type"),
    )


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}
