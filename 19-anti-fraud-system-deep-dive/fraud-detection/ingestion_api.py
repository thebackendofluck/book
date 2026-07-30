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
Real-Time Fraud Detection -- Data Ingestion Service

FastAPI service that receives transaction, user-behavior, and game events,
validates them, enriches metadata, and publishes to Kafka topics for
downstream feature engineering and model scoring.

Reference implementation for Chapter 41: Anti-Fraud System Deep Dive.
"""

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace  # ty:ignore[unresolved-import]
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # ty:ignore[unresolved-import]
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field, validator  # ty:ignore[deprecated]

# Internal modules (would live alongside this file in production)
# from .config import Settings
# from .kafka_producer import KafkaEventProducer
# from .metrics import MetricsCollector
# from .validation import DataValidator
# from .enrichment import DataEnricher

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


# ---------------------------------------------------------------------------
# Pydantic models -- shared schema for every event type
# ---------------------------------------------------------------------------

class TransactionEvent(BaseModel):
    """A single financial transaction (deposit, withdrawal, bet, or win)."""

    event_id: Optional[str] = None
    player_id: str = Field(..., min_length=1, max_length=100)
    amount: float = Field(..., gt=0)
    currency: str = Field(default="USD", regex=r"^[A-Z]{3}$")
    transaction_type: str = Field(..., regex=r"^(deposit|withdrawal|bet|win)$")
    payment_method: Optional[str] = None
    game_type: Optional[str] = None
    game_session_id: Optional[str] = None
    external_transaction_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_fingerprint: Optional[str] = None
    location_data: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    @validator("event_id", pre=True, always=True)  # ty:ignore[deprecated]
    def set_event_id(cls, v):
        return v or str(uuid.uuid4())

    class Config:
        schema_extra = {
            "example": {
                "player_id": "player_123",
                "amount": 100.50,
                "currency": "USD",
                "transaction_type": "deposit",
                "payment_method": "credit_card",
                "ip_address": "192.168.1.100",
                "device_fingerprint": "abc123def456",
            }
        }


class UserEvent(BaseModel):
    """Behavioral event -- login, page view, click, game start/end."""

    event_id: Optional[str] = None
    player_id: str = Field(..., min_length=1, max_length=100)
    event_type: str = Field(
        ...,
        regex=r"^(login|logout|page_view|button_click|game_start|game_end)$",
    )
    session_id: Optional[str] = None
    page_url: Optional[str] = None
    element_id: Optional[str] = None
    game_type: Optional[str] = None
    game_session_id: Optional[str] = None
    duration_seconds: Optional[int] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_fingerprint: Optional[str] = None
    location_data: Optional[Dict[str, Any]] = None
    event_data: Optional[Dict[str, Any]] = None

    @validator("event_id", pre=True, always=True)  # ty:ignore[deprecated]
    def set_event_id(cls, v):
        return v or str(uuid.uuid4())


class GameEvent(BaseModel):
    """In-game event -- spins, bets, wins, bonuses, jackpots."""

    event_id: Optional[str] = None
    player_id: str = Field(..., min_length=1, max_length=100)
    game_type: str = Field(..., min_length=1, max_length=50)
    game_session_id: str = Field(..., min_length=1, max_length=100)
    event_type: str = Field(
        ...,
        regex=r"^(game_start|game_end|spin|bet|win|loss|bonus|jackpot)$",
    )
    bet_amount: Optional[float] = Field(None, ge=0)
    win_amount: Optional[float] = Field(None, ge=0)
    game_state: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    device_fingerprint: Optional[str] = None
    timestamp: Optional[datetime] = None

    @validator("event_id", pre=True, always=True)  # ty:ignore[deprecated]
    def set_event_id(cls, v):
        return v or str(uuid.uuid4())

    @validator("timestamp", pre=True, always=True)  # ty:ignore[deprecated]
    def set_timestamp(cls, v):
        return v or datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

# In production these would be initialised from environment / config:
# settings = Settings()
# kafka_producer: KafkaEventProducer = None
# metrics_collector = MetricsCollector()
# data_validator = DataValidator()
# data_enricher = DataEnricher()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start Kafka producer on boot, close on shutdown."""
    # global kafka_producer
    # kafka_producer = KafkaEventProducer(settings.kafka_bootstrap_servers)
    # await kafka_producer.start()
    logger.info("Ingestion service started")
    yield
    # if kafka_producer:
    #     await kafka_producer.close()
    logger.info("Ingestion service stopped")


app = FastAPI(
    title="Fraud Detection Data Ingestion Service",
    description="Real-time event ingestion for the anti-fraud pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=["http://localhost:3000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FastAPIInstrumentor.instrument_app(app)


# ---------------------------------------------------------------------------
# Health / readiness / metrics
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    # kafka_healthy = await kafka_producer.health_check() if kafka_producer else False
    kafka_healthy = True  # placeholder
    return JSONResponse(
        content={
            "status": "healthy" if kafka_healthy else "unhealthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
            "services": {"kafka": "healthy" if kafka_healthy else "unhealthy"},
        },
        status_code=200 if kafka_healthy else 503,
    )


@app.get("/ready")
async def readiness_check():
    return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics")
async def metrics():
    return generate_latest()


# ---------------------------------------------------------------------------
# Ingestion endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/transactions")
async def ingest_transaction(
    transaction: TransactionEvent,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Validate, enrich, and publish a single transaction event."""

    with tracer.start_as_current_span("ingest_transaction") as span:
        span.set_attribute("player_id", transaction.player_id)
        span.set_attribute("transaction_type", transaction.transaction_type)
        span.set_attribute("amount", transaction.amount)

        try:
            start_time = time.time()

            # 1. Validate
            # validation_result = data_validator.validate_transaction(transaction.dict())
            # if not validation_result["valid"]:
            #     raise HTTPException(status_code=400, detail=validation_result["errors"])

            # 2. Enrich (GeoIP, device info, risk tags)
            # enriched_data = await data_enricher.enrich_transaction(transaction.dict(), request)
            enriched_data = transaction.dict()  # ty:ignore[deprecated]

            enriched_data.update(
                {
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                    "source": "api",
                    "processing_version": "1.0.0",
                }
            )

            # 3. Publish to Kafka topic "transactions"
            # background_tasks.add_task(
            #     kafka_producer.send_event, "transactions", enriched_data,
            #     key=transaction.player_id,
            # )

            logger.info(
                "Transaction ingested",
                player_id=transaction.player_id,
                transaction_id=transaction.event_id,
                amount=transaction.amount,
            )

            return {
                "status": "accepted",
                "event_id": transaction.event_id,
                "ingested_at": enriched_data["ingested_at"],
            }

        except Exception as e:
            logger.error(
                "Failed to ingest transaction",
                player_id=transaction.player_id,
                error=str(e),
            )
            raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/v1/user-events")
async def ingest_user_event(
    user_event: UserEvent,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Validate and publish a user-behavior event."""

    with tracer.start_as_current_span("ingest_user_event") as span:
        span.set_attribute("player_id", user_event.player_id)
        span.set_attribute("event_type", user_event.event_type)

        try:
            enriched_data = user_event.dict()  # ty:ignore[deprecated]
            enriched_data.update(
                {
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                    "source": "api",
                    "processing_version": "1.0.0",
                }
            )

            # background_tasks.add_task(
            #     kafka_producer.send_event, "user-events", enriched_data,
            #     key=user_event.player_id,
            # )

            logger.info(
                "User event ingested",
                player_id=user_event.player_id,
                event_id=user_event.event_id,
                event_type=user_event.event_type,
            )

            return {
                "status": "accepted",
                "event_id": user_event.event_id,
                "ingested_at": enriched_data["ingested_at"],
            }

        except Exception as e:
            logger.error(
                "Failed to ingest user event",
                player_id=user_event.player_id,
                error=str(e),
            )
            raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/v1/game-events")
async def ingest_game_event(
    game_event: GameEvent,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Validate and publish a game event."""

    with tracer.start_as_current_span("ingest_game_event") as span:
        span.set_attribute("player_id", game_event.player_id)
        span.set_attribute("game_type", game_event.game_type)
        span.set_attribute("event_type", game_event.event_type)

        try:
            enriched_data = game_event.dict()  # ty:ignore[deprecated]
            enriched_data.update(
                {
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                    "source": "api",
                    "processing_version": "1.0.0",
                }
            )

            # background_tasks.add_task(
            #     kafka_producer.send_event, "game-events", enriched_data,
            #     key=game_event.player_id,
            # )

            logger.info(
                "Game event ingested",
                player_id=game_event.player_id,
                event_id=game_event.event_id,
                game_type=game_event.game_type,
            )

            return {
                "status": "accepted",
                "event_id": game_event.event_id,
                "ingested_at": enriched_data["ingested_at"],
            }

        except Exception as e:
            logger.error(
                "Failed to ingest game event",
                player_id=game_event.player_id,
                error=str(e),
            )
            raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/v1/bulk-events")
async def ingest_bulk_events(
    events: List[Dict[str, Any]],
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Ingest up to 1 000 events in a single request."""

    if len(events) > 1000:
        raise HTTPException(
            status_code=400, detail="Maximum 1000 events per request"
        )

    with tracer.start_as_current_span("ingest_bulk_events") as span:
        span.set_attribute("event_count", len(events))

        processed_events = []
        errors = []
        batch_id = str(uuid.uuid4())

        for i, event_data in enumerate(events):
            try:
                if "player_id" not in event_data:
                    errors.append({"index": i, "error": "Missing player_id"})
                    continue

                event_data.update(
                    {
                        "ingested_at": datetime.now(timezone.utc).isoformat(),
                        "source": "bulk_api",
                        "processing_version": "1.0.0",
                        "bulk_batch_id": batch_id,
                    }
                )

                topic_mapping = {
                    "transaction": "transactions",
                    "user_event": "user-events",
                    "game_event": "game-events",
                }
                topic = topic_mapping.get(
                    event_data.get("event_type", "unknown"), "unknown-events"
                )

                # background_tasks.add_task(
                #     kafka_producer.send_event, topic, event_data,
                #     key=event_data["player_id"],
                # )

                processed_events.append(
                    {
                        "index": i,
                        "event_id": event_data.get("event_id", f"bulk_{i}"),
                        "status": "accepted",
                    }
                )

            except Exception as e:
                errors.append({"index": i, "error": str(e)})

        result = {
            "status": "processed",
            "total_events": len(events),
            "processed_events": len(processed_events),
            "errors": len(errors),
            "processed": processed_events,
        }
        if errors:
            result["error_details"] = errors

        logger.info(
            "Bulk events processed",
            total=len(events),
            processed=len(processed_events),
            errors=len(errors),
        )
        return result


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "ingestion_api:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_config=None,
    )
