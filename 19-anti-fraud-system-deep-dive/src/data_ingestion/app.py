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
Fraud Detection Data Ingestion Service

This service handles real-time data ingestion from various sources including:
- Casino gaming platforms
- Payment gateways
- User behavior tracking
- External data feeds

The service provides REST APIs for data submission and manages data flow
to Kafka topics for downstream processing.
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from opentelemetry import trace  # ty:ignore[unresolved-import]
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # ty:ignore[unresolved-import]

from .config import Settings  # ty:ignore[unresolved-import]
from .kafka_producer import KafkaEventProducer  # ty:ignore[unresolved-import]
from .metrics import MetricsCollector  # ty:ignore[unresolved-import]
from .validation import DataValidator  # ty:ignore[unresolved-import]
from .enrichment import DataEnricher  # ty:ignore[unresolved-import]

# Configure structured logging
logger = structlog.get_logger(__name__)

# OpenTelemetry tracer
tracer = trace.get_tracer(__name__)

# Global instances
settings = Settings()
kafka_producer = None
metrics_collector = MetricsCollector()
data_validator = DataValidator()
data_enricher = DataEnricher()


def _get_or_create_kafka_producer() -> KafkaEventProducer:
    """Return the process-wide producer, creating a lazy instance for tests/local use."""

    global kafka_producer
    from . import kafka_producer as kafka_producer_module  # local import to honor runtime patching

    if kafka_producer is None or not getattr(kafka_producer, "is_connected", False):
        kafka_producer = kafka_producer_module.KafkaEventProducer(settings.kafka_bootstrap_servers)

    return kafka_producer


class TransactionEvent(BaseModel):
    """Transaction event model"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "player_id": "player_123",
                "amount": 100.50,
                "currency": "USD",
                "transaction_type": "deposit",
                "payment_method": "credit_card",
                "ip_address": "192.168.1.100",
                "device_fingerprint": "abc123def456"
            }
        }
    )

    event_id: Optional[str] = None
    player_id: str = Field(..., min_length=1, max_length=100)
    amount: float = Field(..., gt=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    transaction_type: str = Field(..., pattern=r"^(deposit|withdrawal|bet|win)$")
    payment_method: Optional[str] = None
    game_type: Optional[str] = None
    game_session_id: Optional[str] = None
    external_transaction_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_fingerprint: Optional[str] = None
    location_data: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    @field_validator('event_id', mode='before')
    @classmethod
    def set_event_id(cls, v):
        return v or str(uuid.uuid4())


class UserEvent(BaseModel):
    """User behavior event model"""

    event_id: Optional[str] = None
    player_id: str = Field(..., min_length=1, max_length=100)
    event_type: str = Field(..., pattern=r"^(login|logout|page_view|button_click|game_start|game_end)$")
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

    @field_validator('event_id', mode='before')
    @classmethod
    def set_event_id(cls, v):
        return v or str(uuid.uuid4())


class GameEvent(BaseModel):
    """Game event model"""

    event_id: Optional[str] = None
    player_id: str = Field(..., min_length=1, max_length=100)
    game_type: str = Field(..., min_length=1, max_length=50)
    game_session_id: str = Field(..., min_length=1, max_length=100)
    event_type: str = Field(..., pattern=r"^(game_start|game_end|spin|bet|win|loss|bonus|jackpot)$")
    bet_amount: Optional[float] = Field(None, ge=0)
    win_amount: Optional[float] = Field(None, ge=0)
    game_state: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    device_fingerprint: Optional[str] = None
    timestamp: Optional[datetime] = None

    @field_validator('event_id', mode='before')
    @classmethod
    def set_event_id(cls, v):
        return v or str(uuid.uuid4())

    @field_validator('timestamp', mode='before')
    @classmethod
    def set_timestamp(cls, v):
        return v or datetime.now(timezone.utc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""

    # Startup
    global kafka_producer
    kafka_producer = KafkaEventProducer(settings.kafka_bootstrap_servers)

    logger.info("Starting Fraud Detection Data Ingestion Service",
                kafka_servers=settings.kafka_bootstrap_servers,
                environment=settings.environment)

    yield

    # Shutdown
    if kafka_producer:
        await kafka_producer.close()

    logger.info("Shut down Fraud Detection Data Ingestion Service")


# Create FastAPI app
app = FastAPI(
    title="Fraud Detection Data Ingestion Service",
    description="Real-time data ingestion service for fraud detection system",
    version="1.0.0",
    lifespan=lifespan
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return 400 for request body validation failures to preserve API contract."""

    return JSONResponse(status_code=400, content={"detail": exc.errors()})

# Add middleware
app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=["*"],  # TODO: Configure specific origins for production
    allow_credentials=False,  # Cannot use True with wildcard origins
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instrument with OpenTelemetry
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health_check():
    """Health check endpoint"""

    # Check Kafka connectivity
    kafka_healthy = await kafka_producer.health_check() if kafka_producer else True

    health_status = {
        "status": "healthy" if kafka_healthy else "unhealthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "services": {
            "kafka": "healthy" if kafka_healthy else "unhealthy"
        }
    }

    status_code = 200 if kafka_healthy else 503
    return JSONResponse(content=health_status, status_code=status_code)


@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""

    # More comprehensive checks can be added here
    return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return generate_latest()


@app.post("/api/v1/transactions")
async def ingest_transaction(
    transaction: TransactionEvent,
    request: Request,
    background_tasks: BackgroundTasks
):
    """Ingest transaction event"""

    with tracer.start_as_current_span("ingest_transaction") as span:
        span.set_attribute("player_id", transaction.player_id)
        span.set_attribute("transaction_type", transaction.transaction_type)
        span.set_attribute("amount", transaction.amount)

        try:
            # Validate data
            validation_result = data_validator.validate_transaction(transaction.model_dump())
            if not validation_result["valid"]:
                metrics_collector.increment_counter("data_validation_errors_total", {"type": "transaction"})
                raise HTTPException(status_code=400, detail=validation_result["errors"])

            # Enrich data
            enriched_data = await data_enricher.enrich_transaction(transaction.model_dump(), request)

            # Add processing metadata
            enriched_data.update({
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "source": "api",
                "processing_version": "1.0.0"
            })

            # Send to Kafka asynchronously
            producer = _get_or_create_kafka_producer()
            background_tasks.add_task(
                producer.send_event,
                "transactions",
                enriched_data,
                transaction.player_id
            )

            # Update metrics
            metrics_collector.increment_counter("events_ingested_total", {"type": "transaction"})
            metrics_collector.observe_histogram("event_processing_duration", 0.1, {"type": "transaction"})

            logger.info("Transaction ingested successfully",
                       player_id=transaction.player_id,
                       transaction_id=transaction.event_id,
                       amount=transaction.amount)

            return {
                "status": "accepted",
                "event_id": transaction.event_id,
                "ingested_at": enriched_data["ingested_at"]
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to ingest transaction",
                        player_id=transaction.player_id,
                        error=str(e))
            metrics_collector.increment_counter("ingestion_errors_total", {"type": "transaction"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/v1/user-events")
async def ingest_user_event(
    user_event: UserEvent,
    request: Request,
    background_tasks: BackgroundTasks
):
    """Ingest user behavior event"""

    with tracer.start_as_current_span("ingest_user_event") as span:
        span.set_attribute("player_id", user_event.player_id)
        span.set_attribute("event_type", user_event.event_type)

        try:
            # Validate data
            validation_result = data_validator.validate_user_event(user_event.model_dump())
            if not validation_result["valid"]:
                metrics_collector.increment_counter("data_validation_errors_total", {"type": "user_event"})
                raise HTTPException(status_code=400, detail=validation_result["errors"])

            # Enrich data
            enriched_data = await data_enricher.enrich_user_event(user_event.model_dump(), request)

            # Add processing metadata
            enriched_data.update({
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "source": "api",
                "processing_version": "1.0.0"
            })

            # Send to Kafka asynchronously
            producer = _get_or_create_kafka_producer()
            background_tasks.add_task(
                producer.send_event,
                "user-events",
                enriched_data,
                user_event.player_id
            )

            # Update metrics
            metrics_collector.increment_counter("events_ingested_total", {"type": "user_event"})

            logger.info("User event ingested successfully",
                       player_id=user_event.player_id,
                       event_id=user_event.event_id,
                       event_type=user_event.event_type)

            return {
                "status": "accepted",
                "event_id": user_event.event_id,
                "ingested_at": enriched_data["ingested_at"]
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to ingest user event",
                        player_id=user_event.player_id,
                        error=str(e))
            metrics_collector.increment_counter("ingestion_errors_total", {"type": "user_event"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/v1/game-events")
async def ingest_game_event(
    game_event: GameEvent,
    request: Request,
    background_tasks: BackgroundTasks
):
    """Ingest game event"""

    with tracer.start_as_current_span("ingest_game_event") as span:
        span.set_attribute("player_id", game_event.player_id)
        span.set_attribute("game_type", game_event.game_type)
        span.set_attribute("event_type", game_event.event_type)

        try:
            # Validate data
            validation_result = data_validator.validate_game_event(game_event.model_dump())
            if not validation_result["valid"]:
                metrics_collector.increment_counter("data_validation_errors_total", {"type": "game_event"})
                raise HTTPException(status_code=400, detail=validation_result["errors"])

            # Enrich data
            enriched_data = await data_enricher.enrich_game_event(game_event.model_dump(), request)

            # Add processing metadata
            enriched_data.update({
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "source": "api",
                "processing_version": "1.0.0"
            })

            # Send to Kafka asynchronously
            producer = _get_or_create_kafka_producer()
            background_tasks.add_task(
                producer.send_event,
                "game-events",
                enriched_data,
                game_event.player_id
            )

            # Update metrics
            metrics_collector.increment_counter("events_ingested_total", {"type": "game_event"})

            logger.info("Game event ingested successfully",
                       player_id=game_event.player_id,
                       event_id=game_event.event_id,
                       game_type=game_event.game_type)

            return {
                "status": "accepted",
                "event_id": game_event.event_id,
                "ingested_at": enriched_data["ingested_at"]
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to ingest game event",
                        player_id=game_event.player_id,
                        error=str(e))
            metrics_collector.increment_counter("ingestion_errors_total", {"type": "game_event"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/v1/bulk-events")
async def ingest_bulk_events(
    events: List[Dict[str, Any]],
    request: Request,
    background_tasks: BackgroundTasks
):
    """Ingest bulk events"""

    if len(events) > 1000:  # Limit bulk size
        raise HTTPException(status_code=400, detail="Maximum 1000 events per request")

    with tracer.start_as_current_span("ingest_bulk_events") as span:
        span.set_attribute("event_count", len(events))

        try:
            processed_events = []
            errors = []

            for i, event_data in enumerate(events):
                try:
                    event_type = event_data.get("event_type", "unknown")

                    # Basic validation
                    if "player_id" not in event_data:
                        errors.append({"index": i, "error": "Missing player_id"})
                        continue

                    # Add processing metadata
                    event_data.update({
                        "ingested_at": datetime.now(timezone.utc).isoformat(),
                        "source": "bulk_api",
                        "processing_version": "1.0.0",
                        "bulk_batch_id": str(uuid.uuid4())
                    })

                    # Determine topic based on event type
                    topic_mapping = {
                        "transaction": "transactions",
                        "user_event": "user-events",
                        "game_event": "game-events"
                    }

                    topic = topic_mapping.get(event_type, "unknown-events")

                    # Send to Kafka asynchronously
                    producer = _get_or_create_kafka_producer()
                    background_tasks.add_task(
                        producer.send_event,
                        topic,
                        event_data,
                        event_data["player_id"]
                    )

                    processed_events.append({
                        "index": i,
                        "event_id": event_data.get("event_id", f"bulk_{i}"),
                        "status": "accepted"
                    })

                except Exception as e:
                    errors.append({"index": i, "error": str(e)})

            # Update metrics
            metrics_collector.increment_counter("events_ingested_total",
                                              {"type": "bulk"}, len(processed_events))
            if errors:
                metrics_collector.increment_counter("ingestion_errors_total",
                                                  {"type": "bulk"}, len(errors))

            result = {
                "status": "processed",
                "total_events": len(events),
                "processed_events": len(processed_events),
                "errors": len(errors),
                "processed": processed_events
            }

            if errors:
                result["error_details"] = errors

            logger.info("Bulk events processed",
                       total=len(events),
                       processed=len(processed_events),
                       errors=len(errors))

            return result

        except Exception as e:
            logger.error("Failed to process bulk events", error=str(e))
            metrics_collector.increment_counter("ingestion_errors_total", {"type": "bulk"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/v1/stats")
async def get_ingestion_stats():
    """Get ingestion statistics"""

    stats = {
        "uptime": "stats_not_implemented",  # Would implement uptime tracking
        "events_ingested_today": metrics_collector.get_counter_value("events_ingested_total"),
        "errors_today": metrics_collector.get_counter_value("ingestion_errors_total"),
        "average_processing_time": metrics_collector.get_histogram_avg("event_processing_duration"),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return stats


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        reload=settings.environment == "development",
        log_config=None  # Use structlog configuration
    )
