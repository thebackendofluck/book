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
gameservice.kafka — Domain Event Bus
=====================================

All game-service domain events flow through this package:

* **events.py**          — Pydantic event models for every domain transition.
* **topics.py**          — Kafka topic registry and event-to-topic routing.
* **event_publisher.py** — ``EventPublisher`` protocol + ``KafkaEventPublisher``.
* **memory_publisher.py** — ``InMemoryEventPublisher`` for tests and local dev.

Publishing an event
--------------------
::

    from acmetocasino.gameservice.kafka import KafkaEventPublisher, Topics
    from acmetocasino.gameservice.kafka.events import SessionLaunchedEvent

    publisher = KafkaEventPublisher(bootstrap_servers="localhost:9092")
    event = SessionLaunchedEvent(
        correlation_id="abc123",
        player_id="p-001",
        brand_id="brand-uk",
        jurisdiction="UKGC",
        game_id="book-of-dead",
        mode="real_money",
        channel="web",
    )
    publisher.publish(Topics.for_event(event), event)

All monetary fields use :class:`decimal.Decimal` for precision; all timestamps
are UTC-aware :class:`datetime.datetime` objects.
"""

from __future__ import annotations

from acmetocasino.gameservice.kafka.event_publisher import (
    EventPublisher,
    KafkaEventPublisher,
)
from acmetocasino.gameservice.kafka.memory_publisher import InMemoryEventPublisher
from acmetocasino.gameservice.kafka.topics import Topics

__all__ = [
    "EventPublisher",
    "InMemoryEventPublisher",
    "KafkaEventPublisher",
    "Topics",
]
