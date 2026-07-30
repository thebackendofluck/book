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
affiliate_stats.py — Hourly affiliate stats aggregation service.

Mirrors HourlyStatsStream.scala, EventStream.scala, HourlyStatsConsumer.scala,
and ConsumedMessagesDAO.scala.

Architecture:
  - Kafka consumer reads from the 'transactions' topic
  - Events are rekeyed by userId and grouped into 1-hour tumbling windows
  - Only 'debit' events increment the bet count (credits/deposits/withdrawals ignored)
  - When a window closes (wall clock crosses the hour boundary), the aggregated
    InteractionMessage is written to PostgreSQL and emitted to 'hourly_stats' topic
  - A separate consumer reads from 'hourly_stats' and persists rows to DB

HourlyStatsAggregator:
  - userId: first seen in window
  - globalId: first seen in window
  - betCount: number of 'debit' events in window

InteractionMessage:
  - userId, globalId, betCount, startTime, endTime (window boundaries)
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import structlog
from confluent_kafka import Consumer, KafkaError, Producer

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

@dataclass
class HourlyStatsAggregator:
    """
    Accumulates stats for a single user within a 1-hour tumbling window.

    Only 'debit' events contribute to betCount — credits, deposits, and
    withdrawals are ignored for bet-count aggregation.
    """
    user_id:    Optional[int] = None
    global_id:  Optional[int] = None
    bet_count:  int           = 0

    def update(self, event: dict) -> None:
        core = event.get("coreInfo", {})
        if self.user_id is None:
            self.user_id = core.get("userId")
        if self.global_id is None:
            self.global_id = core.get("globalId")
        if core.get("eventType") == "debit":
            self.bet_count += 1


@dataclass
class WindowKey:
    user_id:    int
    window_start: datetime  # truncated to hour


@dataclass
class InteractionMessage:
    user_id:    int
    global_id:  Optional[int]
    bet_count:  int
    start_time: datetime
    end_time:   datetime


# ---------------------------------------------------------------------------
# Database access (mirrors ConsumedMessagesDAO.scala)
# ---------------------------------------------------------------------------

class ConsumedMessagesDAO:
    """
    Inserts aggregated hourly stats into the affiliate_stats.hourly_stats table.
    Also writes error audit rows when message handling fails.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Optional[Any] = None

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._dsn)
        return self._conn

    def create_hourly_stats_entry(
        self,
        user_id:    int,
        bet_count:  int,
        start_time: datetime,
        end_time:   datetime,
    ) -> None:
        sql = """
            INSERT INTO affiliate_stats.hourly_stats
                (user_id, bet_count, start_time, end_time)
            VALUES (%s, %s, %s, %s)
        """
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, bet_count, start_time, end_time))

    def audit_error(self, topic: str, message: str, error: str) -> None:
        sql = """
            INSERT INTO affiliate_stats.message_error_audit
                (topic_name, message, error_message, creation_date)
            VALUES (%s, %s, %s, current_timestamp)
        """
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (topic, message, error))


# ---------------------------------------------------------------------------
# Tumbling-window accumulator (mirrors Kafka Streams suppress-until-window-closes)
# ---------------------------------------------------------------------------

class HourlyWindowAccumulator:
    """
    In-process tumbling window accumulator.

    Kafka Streams' suppress-until-window-closes emits exactly one result per
    (userId, window) after the window end time passes.  We replicate this by
    tracking the current hour: when a new event arrives in a *later* hour,
    all completed windows from previous hours are flushed.

    Thread-safety: single consumer thread only — no locking needed.
    """

    WINDOW_HOURS = 1

    def __init__(self) -> None:
        # key: (user_id, window_start ISO hour string) → HourlyStatsAggregator
        self._windows: dict[tuple[int, str], HourlyStatsAggregator] = {}

    def _window_start(self, ts: datetime) -> datetime:
        """Truncate timestamp to the start of its hour."""
        return ts.replace(minute=0, second=0, microsecond=0)

    def update(self, event: dict) -> list[InteractionMessage]:
        """
        Feed one transaction event into the accumulator.
        Returns a (possibly empty) list of completed InteractionMessages.
        """
        core = event.get("coreInfo", {})
        user_id = core.get("userId")
        if user_id is None:
            return []

        raw_ts = core.get("timestamp")
        try:
            event_time = datetime.fromisoformat(raw_ts) if raw_ts else datetime.now(timezone.utc)
        except (ValueError, TypeError):
            event_time = datetime.now(timezone.utc)

        window_start = self._window_start(event_time)
        window_key   = (user_id, window_start.isoformat())

        if window_key not in self._windows:
            self._windows[window_key] = HourlyStatsAggregator()
        self._windows[window_key].update(event)

        # Flush windows whose end time has passed
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return self._flush_completed(now)

    def _flush_completed(self, now: datetime) -> list[InteractionMessage]:
        """Emit windows where window_end (start + 1h) ≤ now."""
        from datetime import timedelta

        completed: list[InteractionMessage] = []
        expired_keys = []

        for (user_id, window_start_iso), agg in self._windows.items():
            window_start = datetime.fromisoformat(window_start_iso)
            window_end   = window_start + timedelta(hours=self.WINDOW_HOURS)
            if now >= window_end:
                completed.append(
                    InteractionMessage(
                        user_id=agg.user_id or user_id,
                        global_id=agg.global_id,
                        bet_count=agg.bet_count,
                        start_time=window_start,
                        end_time=window_end,
                    )
                )
                expired_keys.append((user_id, window_start_iso))

        for key in expired_keys:
            del self._windows[key]

        return completed

    def flush_all(self) -> list[InteractionMessage]:
        """Flush all remaining windows on shutdown."""
        from datetime import timedelta
        result = []
        for (user_id, window_start_iso), agg in self._windows.items():
            window_start = datetime.fromisoformat(window_start_iso)
            window_end   = window_start + timedelta(hours=self.WINDOW_HOURS)
            result.append(
                InteractionMessage(
                    user_id=agg.user_id or user_id,
                    global_id=agg.global_id,
                    bet_count=agg.bet_count,
                    start_time=window_start,
                    end_time=window_end,
                )
            )
        self._windows.clear()
        return result


# ---------------------------------------------------------------------------
# Kafka producer for hourly_stats topic
# ---------------------------------------------------------------------------

class HourlyStatsProducer:
    """Publishes completed InteractionMessages to the hourly_stats Kafka topic."""

    TOPIC = "hourly_stats"

    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def send(self, msg: InteractionMessage) -> None:
        payload = json.dumps({
            "userId":    msg.user_id,
            "globalId":  msg.global_id,
            "betCount":  msg.bet_count,
            "startTime": msg.start_time.isoformat(),
            "endTime":   msg.end_time.isoformat(),
        })
        self._producer.produce(
            self.TOPIC,
            key=str(msg.user_id),
            value=payload,
        )
        self._producer.poll(0)

    def flush(self) -> None:
        self._producer.flush(timeout=10)


# ---------------------------------------------------------------------------
# Stream consumer (mirrors HourlyStatsStream — reads transactions topic)
# ---------------------------------------------------------------------------

class HourlyStatsStream:
    """
    Kafka consumer that reads from the transactions topic, applies tumbling-window
    aggregation, and produces completed windows to hourly_stats.

    Mirrors the Kafka Streams topology in HourlyStatsStream.scala:
      transactions -> rekey by userId -> tumbling window -> suppress -> hourly_stats
    """

    SOURCE_TOPIC = "transactions"
    GROUP        = f"HourlyStatsStream-{SOURCE_TOPIC}"

    def __init__(self, bootstrap_servers: str) -> None:
        self._consumer = Consumer({
            "bootstrap.servers":  bootstrap_servers,
            "group.id":           self.GROUP,
            "auto.offset.reset":  "earliest",
            "enable.auto.commit": "false",
        })
        self._producer    = HourlyStatsProducer(bootstrap_servers)
        self._accumulator = HourlyWindowAccumulator()
        self._running     = True

    def run(self) -> None:
        self._consumer.subscribe([self.SOURCE_TOPIC])
        log.info("hourly stats stream started", topic=self.SOURCE_TOPIC)

        while self._running:
            msg = self._consumer.poll(timeout=1.0)

            if msg is None:
                # Periodically flush completed windows even without new events
                self._emit_completed([])
                continue

            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("kafka error", error=str(msg.error()))
                continue

            try:
                event    = json.loads(msg.value())
                completed = self._accumulator.update(event)
                self._emit_completed(completed)
                self._consumer.commit(msg)
            except Exception as exc:
                log.error("stream processing failed", error=str(exc))

    def _emit_completed(self, messages: list[InteractionMessage]) -> None:
        for msg in messages:
            self._producer.send(msg)
            log.info(
                "hourly window emitted",
                user_id=msg.user_id,
                bet_count=msg.bet_count,
                start=msg.start_time.isoformat(),
            )

    def shutdown(self) -> None:
        self._running = False
        # Flush remaining windows
        remaining = self._accumulator.flush_all()
        self._emit_completed(remaining)
        self._producer.flush()
        self._consumer.close()
        log.info("hourly stats stream stopped")


# ---------------------------------------------------------------------------
# Hourly stats consumer (mirrors HourlyStatsConsumer.scala — reads hourly_stats)
# ---------------------------------------------------------------------------

class HourlyStatsConsumer:
    """
    Reads InteractionMessages from the hourly_stats topic and persists them
    to affiliate_stats.hourly_stats in PostgreSQL.

    Mirrors HourlyStatsConsumer.scala / ConsumedMessagesDAOImpl.scala.
    """

    TOPIC = "hourly_stats"
    GROUP = f"HourlyStatsConsumer-{TOPIC}"

    def __init__(self, bootstrap_servers: str, dao: ConsumedMessagesDAO) -> None:
        self._consumer = Consumer({
            "bootstrap.servers":  bootstrap_servers,
            "group.id":           self.GROUP,
            "auto.offset.reset":  "earliest",
            "enable.auto.commit": "false",
        })
        self._dao     = dao
        self._running = True

    def run(self) -> None:
        self._consumer.subscribe([self.TOPIC])
        log.info("hourly stats consumer started", topic=self.TOPIC)

        while self._running:
            msg = self._consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("kafka error", error=str(msg.error()))
                continue

            try:
                data       = json.loads(msg.value())
                user_id    = data["userId"]
                bet_count  = data.get("betCount", 0)
                start_time = datetime.fromisoformat(data["startTime"])
                end_time   = datetime.fromisoformat(data["endTime"])

                self._dao.create_hourly_stats_entry(
                    user_id, bet_count, start_time, end_time
                )
                log.info(
                    "hourly stats persisted",
                    user_id=user_id,
                    bet_count=bet_count,
                )
                self._consumer.commit(msg)
            except Exception as exc:
                log.error("hourly stats consumer error", error=str(exc))
                try:
                    self._dao.audit_error(
                        self.TOPIC,
                        msg.value().decode("utf-8", errors="replace"),
                        str(exc),
                    )
                except Exception:
                    pass

    def shutdown(self) -> None:
        self._running = False
        self._consumer.close()
        log.info("hourly stats consumer stopped")


# ---------------------------------------------------------------------------
# FastAPI health endpoint
# ---------------------------------------------------------------------------

from fastapi import FastAPI

app = FastAPI(title="Affiliate Stats Service", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ping")
def ping():
    return {"pong": True}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    dsn       = os.environ.get("DATABASE_URL", "postgresql://localhost/affiliatestats")

    dao      = ConsumedMessagesDAO(dsn)
    stream   = HourlyStatsStream(bootstrap)
    consumer = HourlyStatsConsumer(bootstrap, dao)

    stream_thread   = threading.Thread(target=stream.run, daemon=True)
    consumer_thread = threading.Thread(target=consumer.run, daemon=True)

    def _shutdown(sig, frame):
        log.info("shutdown signal received")
        stream.shutdown()
        consumer.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    stream_thread.start()
    consumer_thread.start()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
