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
gameservice.kafka.memory_publisher — In-Memory Event Publisher
===============================================================

``InMemoryEventPublisher`` stores published events in per-topic
:class:`~collections.deque` instances.  It satisfies the
:class:`~acmetocasino.gameservice.kafka.event_publisher.EventPublisher`
protocol and is the recommended ``EventPublisher`` implementation for:

* Unit and integration tests (no Kafka broker required).
* Local development and smoke-testing.
* Book examples where infrastructure complexity would obscure the domain logic.

Usage
-----
::

    from acmetocasino.gameservice.kafka import InMemoryEventPublisher, Topics
    from acmetocasino.gameservice.kafka.events import SessionLaunchedEvent

    publisher = InMemoryEventPublisher()
    publisher.publish(Topics.GAME_SESSIONS, event)

    events = publisher.get_events(Topics.GAME_SESSIONS)
    assert len(events) == 1
    assert events[0].player_id == "p-001"

    # Inspect counts without materialising the full list
    count = publisher.event_count(Topics.GAME_SESSIONS)

    # Reset between test cases
    publisher.clear()

Thread-safety
-------------
:class:`collections.deque` operations are thread-safe in CPython (GIL-
protected), so ``InMemoryEventPublisher`` is safe for concurrent use within a
single process.  For multi-process scenarios (e.g. parallel pytest workers)
use separate publisher instances per process.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Iterator

from acmetocasino.gameservice.kafka.events import GameEvent

logger = logging.getLogger(__name__)

# Default maximum number of events stored per topic.  Prevents unbounded
# memory growth in long-running local sessions.
_DEFAULT_MAX_PER_TOPIC: int = 10_000


class InMemoryEventPublisher:
    """Thread-safe in-memory event publisher for tests and local dev.

    Parameters
    ----------
    maxlen:
        Maximum number of events retained per topic.  Oldest events are
        silently evicted when the limit is reached (deque semantics).
        Set to ``None`` for an unbounded store.  Defaults to 10 000.

    Attributes
    ----------
    _store:
        Internal mapping of ``topic → deque[GameEvent]``.  Direct access is
        intentionally left public (no name-mangling) so test helpers can
        inspect or manipulate the store without ceremony.
    """

    def __init__(self, maxlen: int | None = _DEFAULT_MAX_PER_TOPIC) -> None:
        self._maxlen = maxlen
        self._store: dict[str, deque[GameEvent]] = {}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _topic_store(self, topic: str) -> deque[GameEvent]:
        """Return (and lazily create) the deque for *topic*."""
        if topic not in self._store:
            self._store[topic] = deque(maxlen=self._maxlen)
        return self._store[topic]

    # ------------------------------------------------------------------
    # EventPublisher protocol
    # ------------------------------------------------------------------

    def publish(self, topic: str, event: GameEvent) -> None:
        """Append *event* to the in-memory store for *topic*.

        Parameters
        ----------
        topic:
            Kafka topic name — any string is accepted; no validation is
            performed against the :class:`~acmetocasino.gameservice.kafka.topics.Topics`
            registry.
        event:
            Domain event to store.
        """
        self._topic_store(topic).append(event)
        logger.debug(
            "InMemoryEventPublisher: stored event",
            extra={
                "topic": topic,
                "event_type": event.event_type,
                "event_id": event.event_id,
            },
        )

    def publish_batch(self, topic: str, events: list[GameEvent]) -> None:
        """Append all *events* to the in-memory store for *topic*.

        Events are appended in the order they appear in *events*.

        Parameters
        ----------
        topic:
            Kafka topic name.
        events:
            Ordered list of events to store.
        """
        store = self._topic_store(topic)
        for event in events:
            store.append(event)
        logger.debug(
            "InMemoryEventPublisher: stored batch",
            extra={"topic": topic, "count": len(events)},
        )

    # ------------------------------------------------------------------
    # Test / inspection helpers
    # ------------------------------------------------------------------

    def get_events(self, topic: str) -> list[GameEvent]:
        """Return a snapshot list of all events stored for *topic*.

        The returned list is a copy; mutations do not affect the internal
        store.  Events are ordered oldest-first (insertion order).

        Parameters
        ----------
        topic:
            Kafka topic name.

        Returns
        -------
        list[GameEvent]
            All events currently in the store for *topic*.  Returns an empty
            list if the topic has received no events.
        """
        return list(self._topic_store(topic))

    def get_events_by_type(self, topic: str, event_type: str) -> list[GameEvent]:
        """Return events from *topic* filtered by *event_type*.

        Useful in tests that publish mixed events to one topic and want to
        assert on a specific event type.

        Parameters
        ----------
        topic:
            Kafka topic name.
        event_type:
            Value to match against :attr:`~acmetocasino.gameservice.kafka.events.GameEvent.event_type`.

        Returns
        -------
        list[GameEvent]
            Matching events in insertion order.
        """
        return [e for e in self._topic_store(topic) if e.event_type == event_type]

    def get_events_for_player(self, topic: str, player_id: str) -> list[GameEvent]:
        """Return events from *topic* for a specific *player_id*.

        Parameters
        ----------
        topic:
            Kafka topic name.
        player_id:
            Player identifier to filter by.

        Returns
        -------
        list[GameEvent]
            Matching events in insertion order.
        """
        return [e for e in self._topic_store(topic) if e.player_id == player_id]

    def event_count(self, topic: str) -> int:
        """Return the number of events stored for *topic*.

        More efficient than ``len(publisher.get_events(topic))`` because it
        reads the deque length directly without copying.

        Parameters
        ----------
        topic:
            Kafka topic name.

        Returns
        -------
        int
            Number of events.  Returns 0 if the topic has never received an
            event.
        """
        if topic not in self._store:
            return 0
        return len(self._store[topic])

    def total_event_count(self) -> int:
        """Return the total number of events stored across all topics.

        Returns
        -------
        int
            Sum of all per-topic event counts.
        """
        return sum(len(q) for q in self._store.values())

    def topics(self) -> list[str]:
        """Return the list of topics that have received at least one event.

        Returns
        -------
        list[str]
            Topic names in the order they first received an event.
        """
        return list(self._store.keys())

    def iter_events(self, topic: str) -> Iterator[GameEvent]:
        """Iterate over events for *topic* without copying the underlying deque.

        Useful for large stores where materialising a full list is wasteful.

        Parameters
        ----------
        topic:
            Kafka topic name.

        Yields
        ------
        GameEvent
            Events in insertion (oldest-first) order.
        """
        yield from self._topic_store(topic)

    def last_event(self, topic: str) -> GameEvent | None:
        """Return the most recently published event for *topic*, or ``None``.

        Parameters
        ----------
        topic:
            Kafka topic name.

        Returns
        -------
        GameEvent | None
            The most recent event, or ``None`` if the topic is empty.
        """
        store = self._topic_store(topic)
        if not store:
            return None
        return store[-1]

    def assert_published(
        self,
        topic: str,
        event_type: str,
        *,
        count: int | None = None,
        player_id: str | None = None,
    ) -> list[GameEvent]:
        """Assert that events of *event_type* were published to *topic*.

        Convenience helper for test assertions.  Raises ``AssertionError``
        with a descriptive message on failure.

        Parameters
        ----------
        topic:
            Kafka topic name.
        event_type:
            Event type string to match.
        count:
            If provided, assert exactly *count* matching events exist.
        player_id:
            If provided, further filter by player.

        Returns
        -------
        list[GameEvent]
            The matching events (useful for further inspection in tests).
        """
        events = self.get_events_by_type(topic, event_type)
        if player_id is not None:
            events = [e for e in events if e.player_id == player_id]

        if not events:
            raise AssertionError(
                f"Expected at least one '{event_type}' event on topic '{topic}'"
                + (f" for player '{player_id}'" if player_id else "")
                + f", but found none.  Topics with events: {self.topics()}"
            )

        if count is not None and len(events) != count:
            raise AssertionError(
                f"Expected {count} '{event_type}' event(s) on topic '{topic}'"
                + (f" for player '{player_id}'" if player_id else "")
                + f", but found {len(events)}."
            )

        return events

    def clear(self, topic: str | None = None) -> None:
        """Clear stored events.

        Parameters
        ----------
        topic:
            When provided, clear only the specified topic.  When ``None``
            (the default), clear *all* topics.
        """
        if topic is not None:
            if topic in self._store:
                self._store[topic].clear()
        else:
            self._store.clear()
        logger.debug(
            "InMemoryEventPublisher: cleared",
            extra={"topic": topic or "all"},
        )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = ["InMemoryEventPublisher"]
