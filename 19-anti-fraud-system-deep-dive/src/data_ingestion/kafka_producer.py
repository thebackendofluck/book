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
Kafka producer for event streaming in the fraud detection system
"""

import asyncio
import json
from typing import Any, Dict, Optional

import structlog
from aiokafka import AIOKafkaProducer  # ty:ignore[unresolved-import]
from aiokafka.errors import KafkaError  # ty:ignore[unresolved-import]

from .config import settings  # ty:ignore[unresolved-import]

logger = structlog.get_logger(__name__)


class KafkaEventProducer:
    """Asynchronous Kafka producer for fraud detection events"""

    def __init__(self, bootstrap_servers: str):
        self.bootstrap_servers = bootstrap_servers
        self.producer: Optional[AIOKafkaProducer] = None
        self.is_connected = False

    async def start(self):
        """Start the Kafka producer"""

        try:
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                key_serializer=lambda k: str(k).encode('utf-8') if k else None,
                acks='all',  # Wait for all replicas
                retries=3,
                max_in_flight_requests_per_connection=1,  # Ensure ordering
                enable_idempotence=True,  # Exactly-once semantics
                transactional_id='fraud-detection-producer' if settings.is_production else None,
                compression_type='gzip'
            )

            await self.producer.start()
            self.is_connected = True

            logger.info("Kafka producer started successfully",
                       bootstrap_servers=self.bootstrap_servers)

        except Exception as e:
            logger.error("Failed to start Kafka producer", error=str(e))
            raise

    async def stop(self):
        """Stop the Kafka producer"""

        if self.producer:
            try:
                await self.producer.stop()
                self.is_connected = False
                logger.info("Kafka producer stopped")
            except Exception as e:
                logger.error("Error stopping Kafka producer", error=str(e))

    async def send_event(self, topic: str, event_data: Dict[str, Any],
                        key: Optional[str] = None, partition: Optional[int] = None):
        """
        Send event to Kafka topic

        Args:
            topic: Kafka topic name
            event_data: Event data dictionary
            key: Partition key (usually player_id)
            partition: Specific partition (optional)
        """

        if not self.producer or not self.is_connected:
            logger.error("Kafka producer not connected")
            raise Exception("Kafka producer not connected")

        try:
            # Send message
            future = await self.producer.send(
                topic=topic,
                value=event_data,
                key=key,
                partition=partition
            )

            # Wait for acknowledgment
            record_metadata = await future

            logger.debug("Event sent to Kafka",
                        topic=topic,
                        partition=record_metadata.partition,
                        offset=record_metadata.offset,
                        key=key)

        except KafkaError as e:
            logger.error("Kafka error sending event",
                        topic=topic,
                        key=key,
                        error=str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error sending event to Kafka",
                        topic=topic,
                        key=key,
                        error=str(e))
            raise

    async def send_events_batch(self, topic: str, events: list,
                               key_func=None):
        """
        Send multiple events in batch

        Args:
            topic: Kafka topic name
            events: List of event dictionaries
            key_func: Function to extract key from event
        """

        if not self.producer or not self.is_connected:
            logger.error("Kafka producer not connected")
            raise Exception("Kafka producer not connected")

        try:
            # Prepare batch
            batch = []
            for event in events:
                key = key_func(event) if key_func else None
                batch.append({
                    'topic': topic,
                    'value': event,
                    'key': key
                })

            # Send batch
            futures = await self.producer.send_batch(batch)

            # Wait for all acknowledgments
            results = await asyncio.gather(*futures, return_exceptions=True)

            success_count = sum(1 for r in results if not isinstance(r, Exception))
            error_count = len(results) - success_count

            logger.info("Batch sent to Kafka",
                       topic=topic,
                       total_events=len(events),
                       successful=success_count,
                       errors=error_count)

            if error_count > 0:
                logger.warning("Some events failed to send",
                             errors=[str(r) for r in results if isinstance(r, Exception)])

        except Exception as e:
            logger.error("Error sending batch to Kafka",
                        topic=topic,
                        error=str(e))
            raise

    async def health_check(self) -> bool:
        """Check if Kafka producer is healthy"""

        if not self.producer:
            return False

        try:
            # Try to get cluster metadata
            cluster = await self.producer._get_cluster()
            return cluster is not None
        except Exception:
            return False

    async def get_topic_metadata(self, topic: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a topic"""

        if not self.producer:
            return None

        try:
            cluster = await self.producer._get_cluster()
            partitions = await cluster.partitions_for_topic(topic)

            if partitions:
                return {
                    'topic': topic,
                    'partitions': len(partitions),
                    'partition_ids': list(partitions.keys())
                }
            else:
                return None

        except Exception as e:
            logger.error("Error getting topic metadata",
                        topic=topic,
                        error=str(e))
            return None

    async def __aenter__(self):
        """Async context manager entry"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.stop()


# Global producer instance
_kafka_producer: Optional[KafkaEventProducer] = None


async def get_kafka_producer() -> KafkaEventProducer:
    """Get or create global Kafka producer instance"""

    global _kafka_producer

    if _kafka_producer is None:
        _kafka_producer = KafkaEventProducer(settings.kafka_bootstrap_servers)
        await _kafka_producer.start()

    return _kafka_producer


async def close_kafka_producer():
    """Close global Kafka producer instance"""

    global _kafka_producer

    if _kafka_producer:
        await _kafka_producer.stop()
        _kafka_producer = None