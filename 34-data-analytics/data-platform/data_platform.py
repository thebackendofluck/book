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
Enterprise Data Platform for iGaming

This module provides the core data platform implementation for high-volume
gambling platforms, integrating Kafka, ClickHouse, PostgreSQL, and Redis.

Features:
- Real-time event ingestion via Kafka
- Analytics storage with ClickHouse (OLAP)
- Operational data with PostgreSQL (OLTP)
- Caching with Redis
- Batch processing with configurable flush intervals
- Player profile management
- ML feature engineering

Performance Targets:
- Event ingestion: >100K events/sec
- Query latency P99: <100ms for analytics
- Cache hit rate: >95%

Usage:
    config = DataPipelineConfig(
        kafka_brokers=["localhost:9092"],
        clickhouse_host="localhost",
        postgres_url="postgresql://localhost/casino",
        redis_url="redis://localhost:6379"
    )

    platform = EnterpriseDataPlatform(config)
    await platform.initialize()

    # Ingest events
    event = DataEvent(
        event_id="evt_123",
        event_type="bet_placed",
        entity_type="player",
        entity_id="player_456",
        timestamp=datetime.now(timezone.utc),
        properties={"amount": 100.00, "game_id": "slots_777"},
        metadata={"source": "web", "session_id": "sess_789"}
    )
    await platform.ingest_event(event)

Dependencies:
    pip install aiokafka asyncpg redis clickhouse-driver
"""

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Protocol
import asyncio
import json
import logging


class KafkaProducerProtocol(Protocol):
    """Protocol for Kafka producer interface."""

    async def start(self) -> None: ...
    async def send_and_wait(self, topic: str, value: dict[str, Any]) -> None: ...
    async def stop(self) -> None: ...


class ClickHouseClientProtocol(Protocol):
    """Protocol for ClickHouse client interface."""

    def execute(self, query: str, params: Any = None) -> list[Any]: ...


class RedisClientProtocol(Protocol):
    """Protocol for Redis client interface."""

    async def setex(self, key: str, ttl: int, value: str) -> None: ...
    async def zadd(self, key: str, mapping: dict[str, float]) -> None: ...
    async def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> None: ...


class AsyncContextManager(Protocol):
    """Protocol for async context manager."""

    async def __aenter__(self) -> Any: ...
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None: ...


class PostgresPoolProtocol(Protocol):
    """Protocol for PostgreSQL connection pool interface."""

    def acquire(self) -> AsyncContextManager: ...
    async def close(self) -> None: ...


@dataclass
class DataEvent:
    """Data event model for the platform."""

    event_id: str
    event_type: str
    entity_type: str
    entity_id: str
    timestamp: datetime
    properties: dict[str, Any]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "timestamp": self.timestamp.isoformat(),
            "properties": self.properties,
            "metadata": self.metadata,
        }


@dataclass
class DataPipelineConfig:
    """Configuration for the data pipeline."""

    kafka_brokers: list[str]
    clickhouse_host: str
    postgres_url: str
    redis_url: str
    batch_size: int = 1000
    flush_interval_seconds: int = 30
    clickhouse_database: str = "casino_analytics"
    event_cache_ttl_seconds: int = 3600
    event_history_hours: int = 24


class EnterpriseDataPlatform:
    """
    Enterprise-grade data platform for iGaming.

    Provides unified data ingestion, storage, and analytics capabilities
    for high-volume gambling platforms.

    Features:
    - Multi-source event ingestion
    - Real-time and batch processing
    - Player profile management
    - Analytics and reporting
    - ML feature engineering
    """

    def __init__(self, config: DataPipelineConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Initialize connections (set during initialize())
        self.kafka_producer: Optional[KafkaProducerProtocol] = None
        self.clickhouse_client: Optional[ClickHouseClientProtocol] = None
        self.postgres_pool: Optional[PostgresPoolProtocol] = None
        self.redis_client: Any = None

        # Buffers for batch processing
        self.event_buffer: list[DataEvent] = []
        self.metric_buffer: list[dict[str, Any]] = []

        # State
        self._initialized = False
        self._batch_processor_task: Optional[asyncio.Task[None]] = None

    async def initialize(self) -> None:
        """Initialize all data platform connections."""
        try:
            # Import actual implementations
            import aiokafka  # ty:ignore[unresolved-import]
            import asyncpg  # ty:ignore[unresolved-import]
            import redis.asyncio as aioredis
            from clickhouse_driver import Client as ClickHouseClient  # ty:ignore[unresolved-import]

            # Kafka producer for event streaming
            self.kafka_producer = aiokafka.AIOKafkaProducer(
                bootstrap_servers=self.config.kafka_brokers,
                value_serializer=lambda v: json.dumps(v, default=str).encode(),
            )
            await self.kafka_producer.start()

            # ClickHouse for analytics
            self.clickhouse_client = ClickHouseClient(
                host=self.config.clickhouse_host,
                database=self.config.clickhouse_database,
            )

            # PostgreSQL for operational data
            self.postgres_pool = await asyncpg.create_pool(
                self.config.postgres_url,
                min_size=5,
                max_size=20,
            )

            # Redis for caching and real-time data
            self.redis_client = aioredis.from_url(self.config.redis_url)

            # Initialize database schemas
            await self._initialize_schemas()

            # Start background batch processing
            self._batch_processor_task = asyncio.create_task(self._batch_processor())

            self._initialized = True
            self.logger.info("Enterprise Data Platform initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize data platform: {e}")
            raise

    async def _initialize_schemas(self) -> None:
        """Initialize database schemas for analytics."""
        if not self.clickhouse_client or not self.postgres_pool:
            raise RuntimeError("Connections not initialized")

        # ClickHouse tables for real-time analytics
        clickhouse_schemas = [
            """
            CREATE TABLE IF NOT EXISTS casino_events (
                event_id String,
                event_type String,
                entity_type String,
                entity_id String,
                timestamp DateTime,
                properties String,
                metadata String,
                ingested_at DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMM(timestamp)
            ORDER BY (entity_type, entity_id, timestamp)
            """,
            """
            CREATE TABLE IF NOT EXISTS player_metrics (
                player_id String,
                metric_name String,
                metric_value Float64,
                timestamp DateTime,
                game_type String,
                jurisdiction String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMM(timestamp)
            ORDER BY (player_id, metric_name, timestamp)
            """,
            """
            CREATE TABLE IF NOT EXISTS game_events (
                game_id String,
                player_id String,
                event_type String,
                amount Decimal(18,8),
                currency String,
                timestamp DateTime,
                game_state String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMM(timestamp)
            ORDER BY (game_id, player_id, timestamp)
            """,
        ]

        for schema in clickhouse_schemas:
            try:
                self.clickhouse_client.execute(schema)
            except Exception as e:
                self.logger.error(f"Failed to create ClickHouse table: {e}")

        # PostgreSQL tables for operational data
        postgres_schemas = [
            """
            CREATE TABLE IF NOT EXISTS player_profiles (
                player_id VARCHAR(50) PRIMARY KEY,
                email VARCHAR(255) UNIQUE,
                registration_date TIMESTAMP,
                last_login TIMESTAMP,
                total_deposits DECIMAL(15,2) DEFAULT 0,
                total_withdrawals DECIMAL(15,2) DEFAULT 0,
                lifetime_value DECIMAL(15,2) DEFAULT 0,
                risk_score DECIMAL(3,2),
                segments TEXT[],
                preferences JSONB,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS game_sessions (
                session_id VARCHAR(100) PRIMARY KEY,
                player_id VARCHAR(50),
                game_id VARCHAR(50),
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                total_bet DECIMAL(15,2),
                total_win DECIMAL(15,2),
                currency VARCHAR(3),
                jurisdiction VARCHAR(2),
                device_type VARCHAR(20),
                created_at TIMESTAMP DEFAULT NOW()
            )
            """,
        ]

        async with self.postgres_pool.acquire() as conn:
            for schema in postgres_schemas:
                try:
                    await conn.execute(schema)
                except Exception as e:
                    self.logger.error(f"Failed to create PostgreSQL table: {e}")

    async def ingest_event(self, event: DataEvent) -> bool:
        """
        Ingest data event into the platform.

        Args:
            event: The data event to ingest

        Returns:
            True if ingestion successful, False otherwise
        """
        if not self._initialized:
            raise RuntimeError("Platform not initialized. Call initialize() first.")

        try:
            # Add to buffer for batch processing
            self.event_buffer.append(event)

            # Publish to Kafka for real-time processing
            if self.kafka_producer:
                await self.kafka_producer.send_and_wait("casino-events", asdict(event))

            # Cache recent events in Redis
            await self._cache_event(event)

            # Check if buffer should be flushed
            if len(self.event_buffer) >= self.config.batch_size:
                await self._flush_event_buffer()

            return True

        except Exception as e:
            self.logger.error(f"Failed to ingest event {event.event_id}: {e}")
            return False

    async def _cache_event(self, event: DataEvent) -> None:
        """Cache event in Redis for real-time access."""
        if not self.redis_client:
            return

        cache_key = f"event:{event.entity_type}:{event.entity_id}:latest"

        # Store latest event
        await self.redis_client.setex(
            cache_key,
            self.config.event_cache_ttl_seconds,
            json.dumps(asdict(event), default=str),
        )

        # Add to time-series for recent activity
        ts_key = f"events:{event.entity_type}:{event.entity_id}"
        await self.redis_client.zadd(
            ts_key,
            {event.event_id: int(event.timestamp.timestamp())},
        )

        # Trim to keep only recent events
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.config.event_history_hours)
        await self.redis_client.zremrangebyscore(ts_key, 0, int(cutoff.timestamp()))

    async def _batch_processor(self) -> None:
        """Background task for batch processing."""
        while True:
            try:
                await asyncio.sleep(self.config.flush_interval_seconds)

                if self.event_buffer:
                    await self._flush_event_buffer()

                if self.metric_buffer:
                    await self._flush_metric_buffer()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Batch processor error: {e}")

    async def _flush_event_buffer(self) -> None:
        """Flush accumulated events to ClickHouse."""
        if not self.event_buffer or not self.clickhouse_client:
            return

        try:
            # Prepare data for ClickHouse
            clickhouse_data = []
            for event in self.event_buffer:
                clickhouse_data.append(
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "entity_type": event.entity_type,
                        "entity_id": event.entity_id,
                        "timestamp": event.timestamp,
                        "properties": json.dumps(event.properties),
                        "metadata": json.dumps(event.metadata),
                    }
                )

            # Bulk insert into ClickHouse
            self.clickhouse_client.execute(
                "INSERT INTO casino_events VALUES",
                clickhouse_data,
            )

            # Also update PostgreSQL for operational queries
            if self.postgres_pool:
                async with self.postgres_pool.acquire() as conn:
                    for event in self.event_buffer:
                        if event.entity_type == "player" and event.event_type in [
                            "deposit",
                            "withdrawal",
                        ]:
                            await self._update_player_profile(conn, event)

            event_count = len(clickhouse_data)
            self.event_buffer.clear()

            self.logger.info(f"Flushed {event_count} events to storage")

        except Exception as e:
            self.logger.error(f"Failed to flush event buffer: {e}")

    async def _flush_metric_buffer(self) -> None:
        """Flush accumulated metrics to ClickHouse."""
        if not self.metric_buffer or not self.clickhouse_client:
            return

        try:
            self.clickhouse_client.execute(
                "INSERT INTO player_metrics VALUES",
                self.metric_buffer,
            )

            metric_count = len(self.metric_buffer)
            self.metric_buffer.clear()

            self.logger.info(f"Flushed {metric_count} metrics to storage")

        except Exception as e:
            self.logger.error(f"Failed to flush metric buffer: {e}")

    async def _update_player_profile(self, conn: Any, event: DataEvent) -> None:
        """Update player profile based on financial events."""
        if event.event_type == "deposit":
            amount = float(event.properties.get("amount", 0))
            await conn.execute(
                """
                UPDATE player_profiles
                SET total_deposits = total_deposits + $1,
                    lifetime_value = lifetime_value + $1,
                    last_login = $2,
                    updated_at = NOW()
                WHERE player_id = $3
            """,
                amount,
                event.timestamp,
                event.entity_id,
            )

        elif event.event_type == "withdrawal":
            amount = float(event.properties.get("amount", 0))
            await conn.execute(
                """
                UPDATE player_profiles
                SET total_withdrawals = total_withdrawals + $1,
                    updated_at = NOW()
                WHERE player_id = $2
            """,
                amount,
                event.entity_id,
            )

    async def get_player_analytics(
        self,
        player_id: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """
        Get comprehensive player analytics.

        Args:
            player_id: The player ID to analyze
            days: Number of days to analyze

        Returns:
            Dictionary containing player analytics
        """
        if not self._initialized:
            raise RuntimeError("Platform not initialized")

        try:
            # Get player profile from PostgreSQL
            if not self.postgres_pool:
                return {"error": "Database not connected"}

            async with self.postgres_pool.acquire() as conn:
                profile = await conn.fetchrow(
                    "SELECT * FROM player_profiles WHERE player_id = $1",
                    player_id,
                )

            if not profile:
                return {"error": "Player not found"}

            # Get recent activity from ClickHouse
            if not self.clickhouse_client:
                return {"error": "Analytics database not connected"}

            recent_activity = self.clickhouse_client.execute(
                """
                SELECT
                    event_type,
                    count(*) as event_count,
                    sum(CASE WHEN JSONExtractFloat(properties, 'amount') > 0
                             THEN JSONExtractFloat(properties, 'amount') ELSE 0 END) as total_amount
                FROM casino_events
                WHERE entity_id = %(player_id)s
                  AND timestamp >= now() - INTERVAL %(days)s DAY
                GROUP BY event_type
                ORDER BY event_count DESC
            """,
                {"player_id": player_id, "days": days},
            )

            # Get gaming metrics
            gaming_metrics = self.clickhouse_client.execute(
                """
                SELECT
                    game_type,
                    count(*) as sessions,
                    sum(amount) as total_bet,
                    avg(amount) as avg_bet,
                    sum(CASE WHEN event_type = 'win' THEN amount ELSE 0 END) as total_win
                FROM game_events
                WHERE player_id = %(player_id)s
                  AND timestamp >= now() - INTERVAL %(days)s DAY
                GROUP BY game_type
            """,
                {"player_id": player_id, "days": days},
            )

            # Calculate derived metrics
            total_bets = sum(
                float(row[2]) for row in recent_activity if row[0] == "bet_placed"
            )
            total_wins = sum(float(row[2]) for row in recent_activity if row[0] == "win")
            win_rate = (total_wins / total_bets * 100) if total_bets > 0 else 0

            return {
                "profile": dict(profile),
                "recent_activity": [
                    {
                        "event_type": row[0],
                        "count": row[1],
                        "total_amount": float(row[2]),
                    }
                    for row in recent_activity
                ],
                "gaming_metrics": [
                    {
                        "game_type": row[0],
                        "sessions": row[1],
                        "total_bet": float(row[2]),
                        "avg_bet": float(row[3]),
                        "total_win": float(row[4]),
                    }
                    for row in gaming_metrics
                ],
                "derived_metrics": {
                    "win_rate": win_rate,
                    "avg_daily_bets": total_bets / days if days > 0 else 0,
                    "house_edge_experience": 100 - win_rate,
                    "engagement_score": self._calculate_engagement_score(recent_activity),
                },
                "period_days": days,
            }

        except Exception as e:
            self.logger.error(f"Failed to get player analytics: {e}")
            return {"error": str(e)}

    def _calculate_engagement_score(self, activity_data: list[Any]) -> float:
        """Calculate player engagement score."""
        # Simple engagement scoring based on activity diversity and frequency
        event_types = len(set(row[0] for row in activity_data))
        total_events = sum(int(row[1]) for row in activity_data)

        # Normalize to 0-100 scale
        engagement = min(100.0, (event_types * 10) + (total_events * 0.5))
        return engagement

    async def shutdown(self) -> None:
        """Gracefully shutdown the data platform."""
        self.logger.info("Shutting down Enterprise Data Platform...")

        # Cancel batch processor
        if self._batch_processor_task:
            self._batch_processor_task.cancel()
            try:
                await self._batch_processor_task
            except asyncio.CancelledError:
                pass

        # Flush remaining buffers
        await self._flush_event_buffer()
        await self._flush_metric_buffer()

        # Close connections
        if self.kafka_producer:
            await self.kafka_producer.stop()

        if self.postgres_pool:
            await self.postgres_pool.close()

        self._initialized = False
        self.logger.info("Enterprise Data Platform shutdown complete")


async def main() -> None:
    """Example usage of the Enterprise Data Platform."""
    logging.basicConfig(level=logging.INFO)

    config = DataPipelineConfig(
        kafka_brokers=["localhost:9092"],
        clickhouse_host="localhost",
        postgres_url="postgresql://localhost/casino",
        redis_url="redis://localhost:6379",
    )

    platform = EnterpriseDataPlatform(config)

    print("Enterprise Data Platform")
    print("=" * 50)
    print(f"Kafka Brokers: {config.kafka_brokers}")
    print(f"ClickHouse Host: {config.clickhouse_host}")
    print(f"Batch Size: {config.batch_size}")
    print(f"Flush Interval: {config.flush_interval_seconds}s")
    print()
    print("To use:")
    print("  await platform.initialize()")
    print("  await platform.ingest_event(event)")
    print("  analytics = await platform.get_player_analytics('player_123')")


if __name__ == "__main__":
    asyncio.run(main())
