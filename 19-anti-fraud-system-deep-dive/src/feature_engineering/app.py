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
Feature Engineering Service for Fraud Detection

This service processes raw event data and creates ML-ready features using Polars
for high-performance data processing and feature engineering.
"""

import os
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import polars as pl  # ty:ignore[unresolved-import]
import structlog
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import aiokafka  # ty:ignore[unresolved-import]
import redis.asyncio as redis
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from src.data_ingestion.metrics import MetricsCollector
from src.data_ingestion.config import settings

# Configure logging
logger = structlog.get_logger(__name__)

# Initialize FastAPI app

# Browser origins allowed to call this service. A wildcard combined with
# allow_credentials lets any site read authenticated responses, so the
# origins have to be named.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app = FastAPI(
    title="Fraud Detection - Feature Engineering Service",
    description="Real-time feature engineering for fraud detection",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize components
metrics_collector = MetricsCollector()

# Global variables for async components
kafka_consumer: Optional[aiokafka.AIOKafkaConsumer] = None
redis_client: Optional[redis.Redis] = None


class FeatureRequest(BaseModel):
    """Request model for feature engineering"""

    player_id: str = Field(..., description="Player identifier")
    event_type: str = Field(..., description="Type of event (transaction, user_event, game_event)")
    event_data: Dict[str, Any] = Field(..., description="Raw event data")
    context_window: Optional[int] = Field(24, description="Hours of historical context to consider")


class FeatureResponse(BaseModel):
    """Response model for feature engineering"""

    player_id: str
    features: Dict[str, Any]
    feature_version: str = "v1.0"
    processing_time_ms: float
    timestamp: str


class BatchFeatureRequest(BaseModel):
    """Request model for batch feature engineering"""

    events: List[Dict[str, Any]] = Field(..., description="List of events to process")
    batch_id: Optional[str] = Field(None, description="Optional batch identifier")


class BatchFeatureResponse(BaseModel):
    """Response model for batch feature engineering"""

    batch_id: str
    total_events: int
    processed_events: int
    features: List[Dict[str, Any]]
    processing_time_ms: float
    timestamp: str


@app.on_event("startup")  # ty:ignore[deprecated]
async def startup_event():
    """Initialize async components on startup"""

    global kafka_consumer, redis_client

    try:
        # Initialize Redis client
        redis_client = redis.Redis(
            host=settings.redis_url.replace("redis://", "").split(":")[0],
            port=int(settings.redis_url.split(":")[-1]),
            max_connections=settings.redis_max_connections,
            decode_responses=True
        )

        # Test Redis connection
        await redis_client.ping()  # ty:ignore[invalid-await]
        logger.info("Redis connection established")

        # Initialize Kafka consumer for real-time processing
        kafka_consumer = aiokafka.AIOKafkaConsumer(
            'transactions', 'user-events', 'game-events',
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id='feature-engineering-service',
            auto_offset_reset='latest',
            enable_auto_commit=True,
            value_deserializer=lambda m: json.loads(m.decode('utf-8'))
        )

        await kafka_consumer.start()
        logger.info("Kafka consumer started")

        # Start background processing
        asyncio.create_task(process_events_background())

    except Exception as e:
        logger.error("Failed to initialize components", error=str(e))
        raise


@app.on_event("shutdown")  # ty:ignore[deprecated]
async def shutdown_event():
    """Clean up async components on shutdown"""

    global kafka_consumer, redis_client

    if kafka_consumer:
        await kafka_consumer.stop()
        logger.info("Kafka consumer stopped")

    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed")


@app.get("/health")
async def health_check():
    """Health check endpoint"""

    global kafka_consumer, redis_client

    kafka_healthy = kafka_consumer is not None
    redis_healthy = redis_client is not None

    if redis_healthy:
        try:
            await redis_client.ping()  # ty:ignore[invalid-await, unresolved-attribute]
        except Exception: 
            redis_healthy = False

    status = "healthy" if kafka_healthy and redis_healthy else "unhealthy"

    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {
            "kafka": "healthy" if kafka_healthy else "unhealthy",
            "redis": "healthy" if redis_healthy else "unhealthy"
        }
    }


@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""

    global kafka_consumer, redis_client

    kafka_ready = kafka_consumer is not None
    redis_ready = redis_client is not None

    if redis_ready:
        try:
            await redis_client.ping()  # ty:ignore[invalid-await, unresolved-attribute]
        except Exception: 
            redis_ready = False

    ready = kafka_ready and redis_ready

    return {
        "status": "ready" if ready else "not ready",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""

    return generate_latest(), {"Content-Type": CONTENT_TYPE_LATEST}


@app.post("/api/v1/features", response_model=FeatureResponse)
async def create_features(request: FeatureRequest, background_tasks: BackgroundTasks):
    """Create features for a single player event"""

    start_time = datetime.now(timezone.utc)

    try:
        with metrics_collector.time_event_processing("feature_creation"):
            # Extract player data
            player_id = request.player_id
            event_type = request.event_type
            event_data = request.event_data
            context_window = request.context_window

            # Get historical context from Redis
            historical_data = await get_player_history(player_id, context_window)  # ty:ignore[invalid-argument-type]

            # Create features using Polars
            features = await create_player_features_polars(
                player_id, event_type, event_data, historical_data
            )

            # Store features in Redis for real-time access
            await store_player_features(player_id, features)

            processing_time = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

            # Update metrics
            metrics_collector.increment_counter("features_created_total", {"event_type": event_type})

            return FeatureResponse(
                player_id=player_id,
                features=features,
                processing_time_ms=processing_time,
                timestamp=datetime.now(timezone.utc).isoformat()
            )

    except Exception as e:
        logger.error("Error creating features", error=str(e), player_id=request.player_id)
        metrics_collector.increment_counter("feature_creation_errors_total", {"error_type": "processing"})
        raise HTTPException(status_code=500, detail=f"Feature creation failed: {str(e)}")


@app.post("/api/v1/features/batch", response_model=BatchFeatureResponse)
async def create_features_batch(request: BatchFeatureRequest, background_tasks: BackgroundTasks):
    """Create features for a batch of events"""

    start_time = datetime.now(timezone.utc)
    batch_id = request.batch_id or f"batch_{int(start_time.timestamp())}"

    try:
        with metrics_collector.time_event_processing("batch_feature_creation"):
            events = request.events
            processed_features = []

            # Group events by player for efficient processing
            player_events = {}
            for event in events:
                player_id = event.get("player_id")
                if player_id:
                    if player_id not in player_events:
                        player_events[player_id] = []
                    player_events[player_id].append(event)

            # Process each player's events
            for player_id, player_event_list in player_events.items():
                try:
                    # Get historical context
                    historical_data = await get_player_history(player_id, 24)

                    # Create features for all events of this player
                    player_features = await create_player_features_batch_polars(
                        player_id, player_event_list, historical_data
                    )

                    processed_features.extend(player_features)

                    # Store features
                    await store_player_features(player_id, player_features[-1] if player_features else {})

                except Exception as e:
                    logger.error("Error processing player batch",
                               error=str(e), player_id=player_id)
                    continue

            processing_time = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

            # Update metrics
            metrics_collector.increment_counter("batch_features_created_total",
                                              {"batch_size": str(len(events))})

            return BatchFeatureResponse(
                batch_id=batch_id,
                total_events=len(events),
                processed_events=len(processed_features),
                features=processed_features,
                processing_time_ms=processing_time,
                timestamp=datetime.now(timezone.utc).isoformat()
            )

    except Exception as e:
        logger.error("Error creating batch features", error=str(e), batch_id=batch_id)
        metrics_collector.increment_counter("batch_feature_creation_errors_total",
                                          {"error_type": "processing"})
        raise HTTPException(status_code=500, detail=f"Batch feature creation failed: {str(e)}")


@app.get("/api/v1/features/{player_id}")
async def get_player_features(player_id: str):
    """Retrieve stored features for a player"""

    try:
        features = await get_stored_player_features(player_id)

        if not features:
            raise HTTPException(status_code=404, detail="Features not found for player")

        return {
            "player_id": player_id,
            "features": features,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error retrieving player features", error=str(e), player_id=player_id)
        raise HTTPException(status_code=500, detail="Failed to retrieve features")


async def process_events_background():
    """Background task to process events from Kafka"""

    global kafka_consumer

    if not kafka_consumer:
        logger.error("Kafka consumer not initialized")
        return

    logger.info("Starting background event processing")

    try:
        async for message in kafka_consumer:
            try:
                event_data = message.value
                event_type = message.topic

                # Extract player_id based on event type
                player_id = None
                if event_type == "transactions":
                    player_id = event_data.get("player_id")
                elif event_type in ["user-events", "game-events"]:
                    player_id = event_data.get("player_id")

                if player_id:
                    # Process event and create features
                    with metrics_collector.time_event_processing("background_processing"):
                        historical_data = await get_player_history(player_id, 24)

                        features = await create_player_features_polars(
                            player_id, event_type.rstrip('s'), event_data, historical_data
                        )

                        await store_player_features(player_id, features)

                        metrics_collector.increment_counter("background_events_processed_total",
                                                          {"event_type": event_type})

            except Exception as e:
                logger.error("Error processing background event", error=str(e))
                metrics_collector.increment_counter("background_processing_errors_total",
                                                  {"error_type": "processing"})

    except Exception as e:
        logger.error("Background processing failed", error=str(e))


async def get_player_history(player_id: str, hours: int = 24) -> List[Dict[str, Any]]:
    """Retrieve player history from Redis"""

    global redis_client

    if not redis_client:
        return []

    try:
        # Get recent events from Redis (assuming they're stored as a list)
        key = f"player_events:{player_id}"
        events_json = await redis_client.lrange(key, 0, 100)  # Last 100 events  # ty:ignore[invalid-await]

        events = []
        cutoff_time = datetime.now(timezone.utc).timestamp() - (hours * 3600)

        for event_json in events_json:
            try:
                event = json.loads(event_json)
                event_time = datetime.fromisoformat(event.get("timestamp", "")).timestamp()

                if event_time >= cutoff_time:
                    events.append(event)
            except Exception: 
                continue

        return events

    except Exception as e:
        logger.error("Error retrieving player history", error=str(e), player_id=player_id)
        return []


async def store_player_features(player_id: str, features: Dict[str, Any]):
    """Store player features in Redis"""

    global redis_client

    if not redis_client:
        return

    try:
        key = f"player_features:{player_id}"
        features_json = json.dumps({
            **features,
            "stored_at": datetime.now(timezone.utc).isoformat()
        })

        await redis_client.set(key, features_json, ex=3600)  # Expire in 1 hour

    except Exception as e:
        logger.error("Error storing player features", error=str(e), player_id=player_id)


async def get_stored_player_features(player_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve stored player features from Redis"""

    global redis_client

    if not redis_client:
        return None

    try:
        key = f"player_features:{player_id}"
        features_json = await redis_client.get(key)

        if features_json:
            return json.loads(features_json)

        return None

    except Exception as e:
        logger.error("Error retrieving stored features", error=str(e), player_id=player_id)
        return None


async def create_player_features_polars(
    player_id: str,
    event_type: str,
    event_data: Dict[str, Any],
    historical_data: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Create features using Polars for efficient processing"""

    try:
        # Convert historical data to Polars DataFrame
        if historical_data:
            df_history = pl.DataFrame(historical_data)
        else:
            df_history = pl.DataFrame()

        # Create current event DataFrame
        current_event = {
            "player_id": player_id,
            "event_type": event_type,
            "timestamp": event_data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            **event_data
        }

        df_current = pl.DataFrame([current_event])

        # Apply feature engineering based on event type
        if event_type == "transaction":
            features = await create_transaction_features(df_current, df_history)
        elif event_type == "user_event":
            features = await create_user_event_features(df_current, df_history)
        elif event_type == "game_event":
            features = await create_game_event_features(df_current, df_history)
        else:
            features = {}

        return features

    except Exception as e:
        logger.error("Error creating Polars features", error=str(e), player_id=player_id)
        return {}


async def create_transaction_features(df_current: pl.DataFrame, df_history: pl.DataFrame) -> Dict[str, Any]:
    """Create transaction-based features"""

    features = {}

    try:
        # Current transaction features
        current_amount = df_current.select("amount").item()
        features["current_amount"] = current_amount

        # Historical transaction features
        if not df_history.is_empty():
            # Filter for transaction events
            txn_history = df_history.filter(pl.col("event_type") == "transaction")

            if not txn_history.is_empty():
                # Amount statistics
                amount_stats = txn_history.select([
                    pl.col("amount").mean().alias("avg_amount"),
                    pl.col("amount").std().alias("amount_std"),
                    pl.col("amount").max().alias("max_amount"),
                    pl.col("amount").min().alias("min_amount"),
                    pl.col("amount").count().alias("total_transactions")
                ])

                features.update(amount_stats.to_dicts()[0])

                # Amount deviation from mean
                avg_amount = features.get("avg_amount", 0)
                features["amount_deviation"] = current_amount - avg_amount
                features["amount_zscore"] = features["amount_deviation"] / features.get("amount_std", 1)

                # Transaction frequency (per hour)
                time_range_hours = 24  # Assuming 24-hour history
                features["txn_frequency_per_hour"] = features["total_transactions"] / time_range_hours

                # Recent transaction count (last 1 hour)
                recent_txns = txn_history.filter(
                    pl.col("timestamp") >= (datetime.now(timezone.utc).timestamp() - 3600)
                )
                features["recent_txn_count_1h"] = recent_txns.height

        # Risk indicators
        features["high_amount_flag"] = 1 if current_amount > 1000 else 0
        features["unusual_amount_flag"] = 1 if abs(features.get("amount_zscore", 0)) > 2 else 0

    except Exception as e:
        logger.error("Error creating transaction features", error=str(e))

    return features


async def create_user_event_features(df_current: pl.DataFrame, df_history: pl.DataFrame) -> Dict[str, Any]:
    """Create user event-based features"""

    features = {}

    try:
        current_event_type = df_current.select("event_type").item()

        # Session features
        if not df_history.is_empty():
            # Count different event types
            event_counts = df_history.group_by("event_type").count()
            for row in event_counts.iter_rows():
                features[f"{row[0]}_count"] = row[1]

            # Session duration if available
            if "session_id" in df_current.columns:
                session_events = df_history.filter(
                    pl.col("session_id") == df_current.select("session_id").item()
                )

                if not session_events.is_empty():
                    timestamps = session_events.select("timestamp").to_series()
                    if len(timestamps) > 1:
                        # Calculate session duration
                        start_time = timestamps.min()
                        end_time = timestamps.max()
                        features["current_session_duration"] = end_time - start_time

            # Behavioral patterns
            features["login_frequency"] = features.get("login_count", 0) / 24  # per hour
            features["page_view_frequency"] = features.get("page_view_count", 0) / 24

        # Current event indicators
        features[f"is_{current_event_type}"] = 1

        # Risk indicators
        rapid_clicking = features.get("button_click_count", 0) > 50  # More than 50 clicks in 24h
        features["rapid_clicking_flag"] = 1 if rapid_clicking else 0

    except Exception as e:
        logger.error("Error creating user event features", error=str(e))

    return features


async def create_game_event_features(df_current: pl.DataFrame, df_history: pl.DataFrame) -> Dict[str, Any]:
    """Create game event-based features"""

    features = {}

    try:
        # Current game features
        if "bet_amount" in df_current.columns:
            current_bet = df_current.select("bet_amount").item()
            features["current_bet_amount"] = current_bet

        if "win_amount" in df_current.columns:
            current_win = df_current.select("win_amount").item()
            features["current_win_amount"] = current_win

        # Historical game features
        if not df_history.is_empty():
            game_history = df_history.filter(pl.col("event_type") == "game_event")

            if not game_history.is_empty():
                # Betting patterns
                if "bet_amount" in game_history.columns:
                    bet_stats = game_history.select([
                        pl.col("bet_amount").mean().alias("avg_bet"),
                        pl.col("bet_amount").std().alias("bet_std"),
                        pl.col("bet_amount").max().alias("max_bet"),
                        pl.col("bet_amount").count().alias("total_bets")
                    ])
                    features.update(bet_stats.to_dicts()[0])

                # Win/loss patterns
                if "win_amount" in game_history.columns:
                    win_stats = game_history.select([
                        pl.col("win_amount").mean().alias("avg_win"),
                        pl.col("win_amount").sum().alias("total_win"),
                        pl.col("win_amount").count().alias("total_wins")
                    ])
                    features.update(win_stats.to_dicts()[0])

                    # Calculate win rate
                    total_games = features.get("total_bets", 0)
                    total_wins = features.get("total_wins", 0)
                    features["win_rate"] = total_wins / total_games if total_games > 0 else 0

        # Risk indicators
        current_bet = features.get("current_bet_amount", 0)
        avg_bet = features.get("avg_bet", 0)
        features["unusual_bet_flag"] = 1 if current_bet > avg_bet * 3 else 0

        win_rate = features.get("win_rate", 0)
        features["suspicious_win_rate_flag"] = 1 if win_rate > 0.8 else 0  # Winning too often

    except Exception as e:
        logger.error("Error creating game event features", error=str(e))

    return features


async def create_player_features_batch_polars(
    player_id: str,
    events: List[Dict[str, Any]],
    historical_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Create features for a batch of player events"""

    features_list = []

    try:
        # Convert events to DataFrame
        df_events = pl.DataFrame(events)

        # Combine with historical data
        if historical_data:
            df_history = pl.DataFrame(historical_data)
            df_combined = pl.concat([df_history, df_events])
        else:
            df_combined = df_events

        # Process each event
        for event in events:
            event_type = event.get("event_type", "transaction")

            # Create single-event DataFrame
            df_current = pl.DataFrame([event])

            # Generate features
            if event_type == "transaction":
                features = await create_transaction_features(df_current, df_combined)
            elif event_type == "user_event":
                features = await create_user_event_features(df_current, df_combined)
            elif event_type == "game_event":
                features = await create_game_event_features(df_current, df_combined)
            else:
                features = {}

            features["player_id"] = player_id
            features["event_type"] = event_type
            features["event_timestamp"] = event.get("timestamp")

            features_list.append(features)

    except Exception as e:
        logger.error("Error creating batch features", error=str(e), player_id=player_id)

    return features_list


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=settings.host,
        port=8081,  # Different port from ingestion service
        reload=True if settings.environment == "development" else False,
        log_level=settings.log_level.lower()
    )