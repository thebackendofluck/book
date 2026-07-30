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
gameservice.kafka.topics — Topic Registry
==========================================

Centralises all Kafka topic names used by the acmetocasino platform.
Having a single registry avoids typos, simplifies topic-creation scripts,
and makes it easy to grep for every consumer/producer of a given topic.

Usage
-----
::

    from acmetocasino.gameservice.kafka.topics import Topics
    from acmetocasino.gameservice.kafka.events import RoundCompletedEvent

    topic = Topics.for_event(event)          # "acmetocasino.game.rounds"
    topic = Topics.COMPLIANCE                # "acmetocasino.compliance.events"

Naming convention
-----------------
``acmetocasino.<domain>.<entity>``

All topics use the ``acmetocasino.`` prefix so that a shared Kafka cluster
can host multiple platform tenants without key collisions.
"""

from __future__ import annotations

from acmetocasino.gameservice.kafka.events import (
    BalanceChangedEvent,
    ComplianceViolationEvent,
    GameEvent,
    RoundCompletedEvent,
    RoundStartedEvent,
    SessionLaunchedEvent,
    SupplierErrorEvent,
    TransactionProcessedEvent,
)


class Topics:
    """Static topic-name constants + event-to-topic routing helper.

    All topic names are strings so they can be used directly with any
    Kafka client without intermediate conversion.
    """

    #: Full session lifecycle events (launch, close, timeout).
    GAME_SESSIONS: str = "acmetocasino.game.sessions"

    #: Round start / completion events.
    GAME_ROUNDS: str = "acmetocasino.game.rounds"

    #: Every wallet operation (debit, credit, rollback, adjust).
    TRANSACTIONS: str = "acmetocasino.game.transactions"

    #: Player balance mutations (before/after snapshot).
    BALANCE_CHANGES: str = "acmetocasino.game.balance"

    #: Compliance rule violations and responsible-gambling triggers.
    COMPLIANCE: str = "acmetocasino.compliance.events"

    #: Supplier availability and error-rate signals.
    SUPPLIER_HEALTH: str = "acmetocasino.supplier.health"

    #: Aggregated player activity for CRM and analytics.
    PLAYER_ACTIVITY: str = "acmetocasino.player.activity"

    #: Immutable audit trail — every event is also mirrored here.
    AUDIT_TRAIL: str = "acmetocasino.audit.trail"

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    _EVENT_TYPE_MAP: dict[str, str] = {
        "session.launched": GAME_SESSIONS,
        "round.started": GAME_ROUNDS,
        "round.completed": GAME_ROUNDS,
        "transaction.processed": TRANSACTIONS,
        "balance.changed": BALANCE_CHANGES,
        "compliance.violation": COMPLIANCE,
        "supplier.error": SUPPLIER_HEALTH,
    }

    # Map Python types for callers that pass typed instances.
    _CLASS_MAP: dict[type, str] = {
        SessionLaunchedEvent: GAME_SESSIONS,
        RoundStartedEvent: GAME_ROUNDS,
        RoundCompletedEvent: GAME_ROUNDS,
        TransactionProcessedEvent: TRANSACTIONS,
        BalanceChangedEvent: BALANCE_CHANGES,
        ComplianceViolationEvent: COMPLIANCE,
        SupplierErrorEvent: SUPPLIER_HEALTH,
    }

    @classmethod
    def for_event(cls, event: GameEvent) -> str:
        """Return the canonical topic for *event*.

        Resolution order:

        1. Exact Python class match (fastest, no string comparison).
        2. ``event.event_type`` string lookup (handles custom sub-classes).
        3. Falls back to :attr:`AUDIT_TRAIL` rather than raising, so unknown
           events are still persisted and don't block the hot path.

        Parameters
        ----------
        event:
            Any :class:`~acmetocasino.gameservice.kafka.events.GameEvent`
            instance.

        Returns
        -------
        str
            Kafka topic name.
        """
        # Prefer exact class match (O(1) dict lookup).
        topic = cls._CLASS_MAP.get(type(event))
        if topic is not None:
            return topic

        # Fall back to event_type string (handles dynamic / custom sub-classes).
        topic = cls._EVENT_TYPE_MAP.get(event.event_type)
        if topic is not None:
            return topic

        # Unknown event — route to audit trail so nothing is silently dropped.
        return cls.AUDIT_TRAIL

    @classmethod
    def all_topics(cls) -> list[str]:
        """Return a deduplicated list of every topic name.

        Useful for administrative scripts that need to create or inspect all
        platform topics.
        """
        return [
            cls.GAME_SESSIONS,
            cls.GAME_ROUNDS,
            cls.TRANSACTIONS,
            cls.BALANCE_CHANGES,
            cls.COMPLIANCE,
            cls.SUPPLIER_HEALTH,
            cls.PLAYER_ACTIVITY,
            cls.AUDIT_TRAIL,
        ]


__all__ = ["Topics"]
