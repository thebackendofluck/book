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
gameservice.kafka.event_publisher — EventPublisher Protocol + KafkaEventPublisher
==================================================================================

Defines the ``EventPublisher`` structural protocol (typing.Protocol) so any
component of the platform can depend on the abstraction rather than a concrete
Kafka client.  The ``KafkaEventPublisher`` provides the real production
implementation backed by ``confluent-kafka``.

Architecture notes
------------------
* **Partition by player_id** — All events for the same player are delivered
  to the same partition, preserving ordering guarantees for per-player
  consumers (e.g. responsible-gambling triggers, live balance streams).
* **JSON serialisation** — Events are serialised via Pydantic's
  ``model_dump(mode="json")`` so Decimal/datetime values are rendered as
  strings and ISO-8601 respectively, without precision loss.
* **Dead-letter queue (DLQ)** — After ``max_retries`` failed deliveries the
  event is forwarded to the ``<original_topic>.dlq`` topic.  The DLQ record
  preserves the original payload plus a ``dlq_reason`` header.
* **Idempotent producer** — ``enable.idempotence=true`` is set by default so
  Kafka deduplicates producer retries automatically.

Usage
-----
::

    from acmetocasino.gameservice.kafka import KafkaEventPublisher, Topics
    from acmetocasino.gameservice.kafka.events import RoundCompletedEvent

    publisher = KafkaEventPublisher(bootstrap_servers="kafka:9092")
    publisher.publish(Topics.GAME_ROUNDS, event)
    publisher.flush()
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    pass

from acmetocasino.gameservice.kafka.events import GameEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EventPublisher(Protocol):
    """Structural protocol for event-bus publishers.

    Any object that implements ``publish`` and ``publish_batch`` satisfies
    this protocol without explicit inheritance.  This makes swapping out
    ``KafkaEventPublisher`` for ``InMemoryEventPublisher`` in tests trivial.
    """

    def publish(self, topic: str, event: GameEvent) -> None:
        """Publish a single *event* to *topic*.

        Parameters
        ----------
        topic:
            Kafka topic name.  Use :class:`~acmetocasino.gameservice.kafka.topics.Topics`
            constants or :meth:`~acmetocasino.gameservice.kafka.topics.Topics.for_event`.
        event:
            Any :class:`~acmetocasino.gameservice.kafka.events.GameEvent` instance.
        """
        ...

    def publish_batch(self, topic: str, events: list[GameEvent]) -> None:
        """Publish multiple *events* to *topic* as an atomic batch.

        The default implementation is a loop over :meth:`publish`; concrete
        implementations may override this with a more efficient bulk path.

        Parameters
        ----------
        topic:
            Kafka topic name.
        events:
            Sequence of events, all routed to *topic*.
        """
        ...


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialize_event(event: GameEvent) -> bytes:
    """Serialise *event* to UTF-8 JSON bytes.

    Pydantic's ``model_dump(mode="json")`` handles:
    * ``Decimal`` → string (preserves precision)
    * ``datetime`` → ISO-8601 string
    * ``UUID`` → string

    Returns
    -------
    bytes
        UTF-8 encoded JSON payload ready to be sent as a Kafka message value.
    """
    payload = event.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _partition_key(event: GameEvent) -> bytes:
    """Derive the Kafka partition key from *event*.

    Partitioning by ``player_id`` ensures all events for a given player land
    on the same partition, preserving per-player ordering within a topic.

    Returns
    -------
    bytes
        UTF-8 encoded player_id.
    """
    return event.player_id.encode("utf-8")


# ---------------------------------------------------------------------------
# KafkaEventPublisher
# ---------------------------------------------------------------------------


class KafkaEventPublisher:
    """Production Kafka producer backed by ``confluent-kafka``.

    Parameters
    ----------
    bootstrap_servers:
        Comma-separated ``host:port`` list of Kafka brokers.
    max_retries:
        Number of delivery retries before the event is routed to the DLQ.
        Defaults to 3.
    dlq_suffix:
        Suffix appended to the original topic name to form the DLQ topic.
        Defaults to ``".dlq"``.
    extra_config:
        Additional confluent-kafka producer configuration overrides.  Keys
        use confluent-kafka's native dotted format (e.g.
        ``{"compression.type": "lz4"}``).

    Notes
    -----
    ``confluent-kafka`` is listed as an optional dependency.  An
    ``ImportError`` is raised at construction time if it is not installed,
    rather than at module import time, so the rest of the package remains
    importable in environments where Kafka is not present (e.g. unit tests
    that use ``InMemoryEventPublisher``).
    """

    def __init__(
        self,
        bootstrap_servers: str,
        *,
        max_retries: int = 3,
        dlq_suffix: str = ".dlq",
        extra_config: dict[str, object] | None = None,
    ) -> None:
        try:
            from confluent_kafka import Producer  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "confluent-kafka is required to use KafkaEventPublisher. "
                "Install it with: pip install confluent-kafka"
            ) from exc

        self._max_retries = max_retries
        self._dlq_suffix = dlq_suffix

        config: dict[str, object] = {
            "bootstrap.servers": bootstrap_servers,
            "enable.idempotence": True,
            "acks": "all",
            "retries": max_retries,
            "retry.backoff.ms": 200,
            "linger.ms": 5,
            "batch.size": 65536,
            "compression.type": "snappy",
        }
        if extra_config:
            config.update(extra_config)

        self._producer: object = Producer(config)
        logger.info(
            "KafkaEventPublisher initialised",
            extra={"bootstrap_servers": bootstrap_servers},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _delivery_callback(
        self, err: object | None, msg: object, *, dlq_topic: str, payload: bytes
    ) -> None:
        """Called by confluent-kafka on each delivery acknowledgement.

        On failure after all broker-side retries, the message is forwarded to
        the DLQ.  This callback runs on the producer's internal poll thread.
        """
        if err is None:
            return

        # err is a KafkaError; we use object typing to avoid hard import
        err_str = str(err)
        logger.error(
            "Kafka delivery failed; routing to DLQ",
            extra={"error": err_str, "dlq_topic": dlq_topic},
        )
        self._send_to_dlq(dlq_topic, payload, reason=err_str)

    def _send_to_dlq(self, dlq_topic: str, payload: bytes, *, reason: str) -> None:
        """Forward *payload* to *dlq_topic* with a ``dlq_reason`` header."""
        try:
            from confluent_kafka import KafkaException  # type: ignore[import-untyped]

            self._producer.produce(  # type: ignore[attr-defined]
                topic=dlq_topic,
                value=payload,
                headers={"dlq_reason": reason.encode("utf-8")},
            )
            self._producer.poll(0)  # type: ignore[attr-defined]
        except KafkaException:
            # Last resort: log and swallow so we don't block the hot path.
            logger.exception(
                "Failed to send event to DLQ; event will be lost",
                extra={"dlq_topic": dlq_topic},
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(self, topic: str, event: GameEvent) -> None:
        """Serialise and publish a single *event* to *topic*.

        The call is non-blocking; the underlying producer queues the message
        and delivers it asynchronously.  Call :meth:`flush` to wait for all
        in-flight messages to be acknowledged.

        Parameters
        ----------
        topic:
            Kafka topic name.
        event:
            Domain event to publish.
        """
        from confluent_kafka import KafkaException  # type: ignore[import-untyped]

        payload = _serialize_event(event)
        key = _partition_key(event)
        dlq_topic = topic + self._dlq_suffix

        try:
            self._producer.produce(  # type: ignore[attr-defined]
                topic=topic,
                key=key,
                value=payload,
                on_delivery=lambda err, msg: self._delivery_callback(
                    err, msg, dlq_topic=dlq_topic, payload=payload
                ),
            )
            # Non-blocking poll to trigger delivery callbacks for previously
            # produced messages and free internal buffer space.
            self._producer.poll(0)  # type: ignore[attr-defined]
        except KafkaException:
            logger.exception(
                "Failed to enqueue event; routing directly to DLQ",
                extra={"topic": topic, "event_type": event.event_type},
            )
            self._send_to_dlq(dlq_topic, payload, reason="enqueue_failed")

    def publish_batch(self, topic: str, events: list[GameEvent]) -> None:
        """Publish multiple *events* to *topic*.

        Events are enqueued in order.  A single :meth:`flush` is performed
        after all events are queued, blocking until all deliveries are
        acknowledged or the ``poll.timeout`` expires.

        Parameters
        ----------
        topic:
            Kafka topic name.
        events:
            Ordered list of events to publish.
        """
        for event in events:
            self.publish(topic, event)
        self.flush()

    def flush(self, timeout: float = 10.0) -> int:
        """Block until all in-flight messages are delivered.

        Parameters
        ----------
        timeout:
            Maximum seconds to wait.  Defaults to 10.

        Returns
        -------
        int
            Number of messages still pending after the timeout (0 = all
            delivered successfully).
        """
        remaining: int = self._producer.flush(timeout)  # type: ignore[attr-defined]
        if remaining > 0:
            logger.warning(
                "flush() timed out with messages still pending",
                extra={"pending_count": remaining},
            )
        return remaining

    def close(self) -> None:
        """Flush and shut down the producer.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        self.flush()
        logger.info("KafkaEventPublisher closed")


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "EventPublisher",
    "KafkaEventPublisher",
]
