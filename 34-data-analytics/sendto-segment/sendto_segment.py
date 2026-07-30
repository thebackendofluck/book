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
sendto_segment.py — Segment event forwarding service.

Mirrors SegmentEventProcessor.scala, MessageConsumer.scala, and EventHandler.scala.

Architecture:
  - Kafka consumer reads from multiple topics (lifecycle events, accounts events,
    game-launch events, round-played events)
  - Each message is parsed, the event type extracted, and forwarded to the
    Segment analytics API via HTTP (identify or track calls)
  - Retry with exponential backoff (max 4 attempts, 100ms base, factor 2)
  - On permanent failure: persist to failed_messages table and alert via OpsGenie
  - Manual offset commit only after successful processing

EventHandler routing:
  Lifecycle topic:  registration (non-US), kyc-approved (US), login,
                    deposited, locked, unlocked, deposit-failed
  Accounts topic:   credit→BetSettled, debit→BetPlaced or BetClawback,
                    withdraw, refund→BetResettlement, withdraw_accepted,
                    withdraw_rejected
  Game topic:       GameLaunchEvent → GameLaunchedTrack
  Round topic:      RoundPlayedEvent → RoundPlayedTrack

SegmentEventProcessor:
  - parseAndSend: parse JSON, route to handler, handle HTTP errors
  - processKafkaMessage: retry loop → persist failure → alert → commit offset
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx
import psycopg2
import structlog
from confluent_kafka import Consumer, KafkaError
from fastapi import FastAPI

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEGMENT_WRITE_KEY   = os.environ.get("SEGMENT_WRITE_KEY", "")
SEGMENT_API_URL     = os.environ.get("SEGMENT_API_URL", "https://api.segment.io/v1")
SEND_PII            = os.environ.get("SEND_PII", "false").lower() == "true"
USE_ACME_USER_ID    = os.environ.get("USE_ACME_USER_ID_AS_SEGMENT_USER", "true").lower() == "true"
CONSUMER_GROUP      = os.environ.get("CONSUMER_GROUP", "send-to-segment")
ERRORS_BACKOFF_HOURS = int(os.environ.get("ERRORS_BACKOFF_HOURS", "1"))

RETRY_MAX_ATTEMPTS  = 4
RETRY_BASE_MS       = 100
RETRY_FACTOR        = 2


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

@dataclass
class ConsumableMessage:
    value:     str
    key:       str
    topic:     str
    partition: int
    offset:    int

    @property
    def message_id(self) -> str:
        md5 = hashlib.md5(self.value.encode()).hexdigest()
        return f"{self.topic}/{self.key}/{self.partition}/{self.offset}/{md5}"


@dataclass
class FailedMessage:
    topic:      str
    key:        str
    partition:  int
    msg_offset: int
    message:    str
    error:      str
    next_try:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Processing result types (mirrors sealed trait EventProcessingResult)
# ---------------------------------------------------------------------------

class ProcessingResult:
    pass

class SuccessEventHandling(ProcessingResult):
    def __init__(self, event_type: str) -> None:
        self.event_type = event_type

class IgnoredMessage(ProcessingResult):
    def __init__(self, without_external_id: bool = False, without_consent: bool = False) -> None:
        self.without_external_id = without_external_id
        self.without_consent = without_consent

class ParsingException(ProcessingResult, Exception):
    def __init__(self, message: str, event_type: Optional[str] = None) -> None:
        super().__init__(message)
        self.event_type = event_type

class UnsuccessfulHttpCallException(ProcessingResult, Exception):
    def __init__(self, message: str, status_code: int, event_type: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.event_type  = event_type
        # Mark as retriable
        self.retriable   = True

class ProcessingRuntimeException(ProcessingResult, Exception):
    def __init__(self, message: str, event_type: Optional[str] = None) -> None:
        super().__init__(message)
        self.event_type = event_type


# ---------------------------------------------------------------------------
# Failed message repository (mirrors FailedMessageRepository)
# ---------------------------------------------------------------------------

class FailedMessageRepository:
    """Persists unhandleable messages to PostgreSQL for later retry."""

    def __init__(self, dsn: str) -> None:
        self._dsn  = dsn
        self._conn = None

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._dsn)
        return self._conn

    def save(self, msg: FailedMessage) -> None:
        sql = """
            INSERT INTO sendtosegment.failed_messages
                (topic, key, partition, msg_offset, message, error, next_try)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    msg.topic, msg.key, msg.partition,
                    msg.msg_offset, msg.message, msg.error,
                    msg.next_try,
                ))


# ---------------------------------------------------------------------------
# Consent gate (mirrors the exclude_from_marketing / marketing_preferences
# gate in mobivate-feed/player_cdp_feed.py)
# ---------------------------------------------------------------------------
# player_cdp_feed.py only extracts players who joined
# user_marketing_preferences/marketing_preferences and pass
# exclude_from_marketing = false before its SQL export ever runs. This
# consumer had no equivalent: every identify()/track() call went straight to
# Segment — a third-party marketing/analytics processor — for every event,
# regardless of the player's marketing/analytics consent state. Segment
# sits in exactly the category that gate is meant to control, so every
# forward here must pass the same check first.

class ConsentRepository:
    """Looks up whether a player has granted marketing/analytics consent."""

    def __init__(self, dsn: str) -> None:
        self._dsn  = dsn
        self._conn = None

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._dsn)
        return self._conn

    def has_consent(self, user_id: str) -> bool:
        sql = """
            SELECT (u.exclude_from_marketing = false)
                   AND COALESCE(mp.marketing_enabled, false)
            FROM casino_core.users u
            LEFT JOIN casino_core.user_marketing_preferences ump ON ump.user_id = u.id
            LEFT JOIN casino_core.marketing_preferences mp ON mp.id = ump.brand_marketing_prefs
            WHERE u.id = %s
        """
        try:
            with self._connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (user_id,))
                    row = cur.fetchone()
                    return bool(row and row[0])
        except Exception as exc:
            # Fail closed: if consent cannot be verified, do not forward.
            log.error("consent lookup failed — event will not be forwarded",
                      user_id=user_id, error=str(exc))
            return False


# ---------------------------------------------------------------------------
# Segment API client
# ---------------------------------------------------------------------------

class SegmentApi:
    """HTTP client for the Segment analytics API (identify + track endpoints)."""

    def __init__(self, write_key: str, base_url: str) -> None:
        self._write_key = write_key
        self._base_url  = base_url

    def identify(self, user_id: str, traits: dict, message_id: str, timestamp: str) -> httpx.Response:
        payload = {
            "userId":    user_id,
            "traits":    traits,
            "messageId": message_id,
            "timestamp": timestamp,
        }
        return self._post("identify", payload)

    def track(self, user_id: str, event: str, properties: dict,
              message_id: str, timestamp: str) -> httpx.Response:
        payload = {
            "userId":     user_id,
            "event":      event,
            "properties": properties,
            "messageId":  message_id,
            "timestamp":  timestamp,
        }
        return self._post("track", payload)

    def _post(self, endpoint: str, payload: dict) -> httpx.Response:
        with httpx.Client(timeout=10) as client:
            return client.post(
                f"{self._base_url}/{endpoint}",
                json=payload,
                auth=(self._write_key, ""),
            )


# ---------------------------------------------------------------------------
# Event handler (mirrors EventHandler.scala)
# ---------------------------------------------------------------------------

class EventHandler:
    """
    Routes parsed Kafka messages to the appropriate Segment API call.

    Lifecycle events:  registration (non-US) → identify
                       kyc-approved (US)     → identify + kycApproved track
                       login, deposited, locked, unlocked, deposit-failed → track
    Accounts events:   bet placed/settled/clawback, withdraw, resettlement → track
    Game events:       gameLaunched, roundPlayed → track
    """

    def __init__(self, segment_api: SegmentApi, consent_repo: ConsentRepository) -> None:
        self._api     = segment_api
        self._consent = consent_repo

    def _segment_user_id(self, core: dict) -> Optional[str]:
        if USE_ACME_USER_ID:
            uid = core.get("userId")
            return str(uid) if uid is not None else None
        return core.get("externalUserId") or core.get("maybeExternalId")

    def process_lifecycle_event(self, msg: ConsumableMessage, data: dict) -> ProcessingResult:
        core       = data.get("coreInfo", {})
        event_type = core.get("eventType", "")
        user_id    = self._segment_user_id(core)
        timestamp  = core.get("timestamp", datetime.now(timezone.utc).isoformat())

        if not user_id:
            return IgnoredMessage(without_external_id=True)

        if not self._consent.has_consent(user_id):
            return IgnoredMessage(without_consent=True)

        if event_type == "kyc-approved" and core.get("country") == "US":
            traits = data if SEND_PII else {}
            resp   = self._api.identify(user_id, traits, msg.message_id, timestamp)
            if resp.is_error:
                return UnsuccessfulHttpCallException(
                    f"identify failed: {resp.status_code}", resp.status_code, event_type
                )
            resp2 = self._api.track(user_id, "kyc_approved", data, msg.message_id, timestamp)
            if resp2.is_error:
                return UnsuccessfulHttpCallException(
                    f"track failed: {resp2.status_code}", resp2.status_code, event_type
                )
            return SuccessEventHandling(event_type)

        if event_type == "registration" and core.get("country") != "US":
            traits = data if SEND_PII else {}
            resp   = self._api.identify(user_id, traits, msg.message_id, timestamp)
            if resp.is_error:
                return UnsuccessfulHttpCallException(
                    f"identify failed: {resp.status_code}", resp.status_code, event_type
                )
            return SuccessEventHandling(event_type)

        track_events = {"login", "deposited", "locked", "unlocked", "deposit-failed"}
        if event_type in track_events:
            resp = self._api.track(user_id, event_type, data, msg.message_id, timestamp)
            if resp.is_error:
                return UnsuccessfulHttpCallException(
                    f"track failed: {resp.status_code}", resp.status_code, event_type
                )
            return SuccessEventHandling(event_type)

        return IgnoredMessage()

    def process_accounts_event(self, msg: ConsumableMessage, data: dict) -> ProcessingResult:
        core       = data.get("coreInfo", {})
        event_type = core.get("eventType", "")
        user_id    = self._segment_user_id(core)
        timestamp  = core.get("timestamp", datetime.now(timezone.utc).isoformat())

        if not user_id:
            return IgnoredMessage(without_external_id=True)

        if not self._consent.has_consent(user_id):
            return IgnoredMessage(without_consent=True)

        # Determine effective event name
        if event_type == "credit" and data.get("betSettled"):
            track_name = "bet_settled"
        elif event_type == "debit" and data.get("betClawback"):
            track_name = "bet_clawback"
        elif event_type == "debit" and data.get("betPlaced"):
            track_name = "bet_placed"
        elif event_type == "withdraw":
            track_name = "withdraw"
        elif event_type == "refund" and data.get("betResettlement"):
            track_name = "bet_resettlement"
        elif event_type in ("withdraw_accepted", "withdraw_rejected") and data.get("paymentMethod"):
            track_name = event_type
        else:
            return IgnoredMessage()

        resp = self._api.track(user_id, track_name, data, msg.message_id, timestamp)
        if resp.is_error:
            return UnsuccessfulHttpCallException(
                f"track failed: {resp.status_code}", resp.status_code, event_type
            )
        return SuccessEventHandling(event_type)

    def process_game_event(self, msg: ConsumableMessage, data: dict) -> ProcessingResult:
        core      = data.get("coreInfo", {})
        user_id   = self._segment_user_id(core) or msg.key
        timestamp = core.get("timestamp", datetime.now(timezone.utc).isoformat())

        if not user_id:
            return IgnoredMessage(without_external_id=True)

        if not self._consent.has_consent(user_id):
            return IgnoredMessage(without_consent=True)

        resp = self._api.track(user_id, "game_launched", data, msg.message_id, timestamp)
        if resp.is_error:
            return UnsuccessfulHttpCallException(
                f"track failed: {resp.status_code}", resp.status_code, "game_launched"
            )
        return SuccessEventHandling("game_launched")

    def process_round_played_event(self, msg: ConsumableMessage, data: dict) -> ProcessingResult:
        core      = data.get("coreInfo", {})
        user_id   = self._segment_user_id(core) or msg.key
        timestamp = core.get("timestamp", datetime.now(timezone.utc).isoformat())

        if not user_id:
            return IgnoredMessage(without_external_id=True)

        if not self._consent.has_consent(user_id):
            return IgnoredMessage(without_consent=True)

        resp = self._api.track(user_id, "round_played", data, msg.message_id, timestamp)
        if resp.is_error:
            return UnsuccessfulHttpCallException(
                f"track failed: {resp.status_code}", resp.status_code, "round_played"
            )
        return SuccessEventHandling("round_played")


# ---------------------------------------------------------------------------
# SegmentEventProcessor (mirrors SegmentEventProcessor.scala)
# ---------------------------------------------------------------------------

HandlerFn = Callable[[ConsumableMessage, dict], ProcessingResult]


class SegmentEventProcessor:
    """
    Wraps a topic-specific handler with:
      - JSON parsing
      - Retry with exponential backoff (max 4, 100ms base, ×2 factor)
      - Failure persistence to DB
      - OpsGenie alerting on permanent failure
      - Manual offset commit on success
    """

    def __init__(
        self,
        topic:              str,
        handler:            HandlerFn,
        failed_repo:        FailedMessageRepository,
        log_every:          int = 1000,
    ) -> None:
        self._topic      = topic
        self._handler    = handler
        self._failed     = failed_repo
        self._log_every  = log_every
        self._counter    = 0

    def parse_and_send(self, msg: ConsumableMessage) -> ProcessingResult:
        try:
            data = json.loads(msg.value)
        except json.JSONDecodeError as exc:
            return ParsingException(f"JSON parse failed: {exc}")

        event_type = (data.get("coreInfo") or {}).get("eventType", "unknown")

        if self._counter % self._log_every == 0:
            log.info("processing message", topic=self._topic, event_type=event_type)

        try:
            return self._handler(msg, data)
        except Exception as exc:
            return ProcessingRuntimeException(f"handler error: {exc}")

    def process_kafka_message(self, raw_msg, consumer) -> None:
        """
        Process one Kafka message with exponential backoff retry.
        Commits offset only after success.
        Persists to DB and alerts on permanent failure.
        """
        msg = ConsumableMessage(
            value=raw_msg.value().decode("utf-8", errors="replace"),
            key=raw_msg.key() or "",
            topic=raw_msg.topic(),
            partition=raw_msg.partition(),
            offset=raw_msg.offset(),
        )

        result = self._retry(msg)

        if isinstance(result, (UnsuccessfulHttpCallException,)):
            # All retries exhausted — save and alert
            log.error(
                "message permanently failed",
                topic=self._topic,
                offset=msg.offset,
                error=str(result),
            )
            self._save_failed(msg, str(result))
            self._alert(msg, result)
        elif isinstance(result, (ParsingException, ProcessingRuntimeException)):
            log.warning(
                "message skipped",
                topic=self._topic,
                reason=str(result),
            )

        consumer.commit(raw_msg)
        self._counter = (self._counter + 1) % (self._log_every * 10)

    def _retry(self, msg: ConsumableMessage) -> ProcessingResult:
        delay_ms = RETRY_BASE_MS
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            result = self.parse_and_send(msg)
            if isinstance(result, (UnsuccessfulHttpCallException,)):
                if attempt < RETRY_MAX_ATTEMPTS:
                    log.warning(
                        "retrying",
                        topic=self._topic,
                        attempt=attempt,
                        delay_ms=delay_ms,
                    )
                    time.sleep(delay_ms / 1000.0)
                    delay_ms *= RETRY_FACTOR
                else:
                    return result
            else:
                return result
        return result  # unreachable but satisfies type checker

    def _save_failed(self, msg: ConsumableMessage, error: str) -> None:
        from datetime import timedelta
        next_try = datetime.now(timezone.utc) + timedelta(hours=ERRORS_BACKOFF_HOURS)
        try:
            self._failed.save(FailedMessage(
                topic=msg.topic,
                key=msg.key,
                partition=msg.partition,
                msg_offset=msg.offset,
                message=msg.value,
                error=error,
                next_try=next_try,
            ))
        except Exception as exc:
            log.error("failed to persist failed message", error=str(exc))

    def _alert(self, msg: ConsumableMessage, error: Exception) -> None:
        # In production this calls OpsGenie API; here we log only
        log.error(
            "OpsGenie alert: message handling failed",
            topic=self._topic,
            message_id=msg.message_id,
            error=str(error),
        )


# ---------------------------------------------------------------------------
# Multi-topic consumer runner
# ---------------------------------------------------------------------------

class SendToSegmentRunner:
    """
    Runs one Kafka consumer per topic, each backed by a SegmentEventProcessor.
    Topics and handlers are configured from environment variables.
    """

    TOPIC_LIFECYCLE  = os.environ.get("TOPIC_LIFECYCLE",  "lifecycle-events")
    TOPIC_ACCOUNTS   = os.environ.get("TOPIC_ACCOUNTS",   "accounts-events")
    TOPIC_GAME       = os.environ.get("TOPIC_GAME",        "game-events")
    TOPIC_ROUND      = os.environ.get("TOPIC_ROUND",       "round-played-events")

    def __init__(
        self,
        bootstrap: str,
        failed_repo: FailedMessageRepository,
        consent_repo: ConsentRepository,
    ) -> None:
        self._bootstrap  = bootstrap
        self._failed     = failed_repo
        self._running    = True
        self._api        = SegmentApi(SEGMENT_WRITE_KEY, SEGMENT_API_URL)
        self._handler    = EventHandler(self._api, consent_repo)
        self._threads: list[threading.Thread] = []

    def _make_consumer(self, group_suffix: str) -> Consumer:
        return Consumer({
            "bootstrap.servers":  self._bootstrap,
            "group.id":           f"{CONSUMER_GROUP}-{group_suffix}",
            "auto.offset.reset":  "earliest",
            "enable.auto.commit": "false",
        })

    def _run_topic(self, topic: str, handler: HandlerFn) -> None:
        consumer  = self._make_consumer(topic)
        processor = SegmentEventProcessor(topic, handler, self._failed)
        consumer.subscribe([topic])
        log.info("segment consumer started", topic=topic)

        while self._running:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("kafka error", error=str(msg.error()))
                continue
            processor.process_kafka_message(msg, consumer)

        consumer.close()
        log.info("segment consumer stopped", topic=topic)

    def start(self) -> None:
        configs = [
            (self.TOPIC_LIFECYCLE, self._handler.process_lifecycle_event),
            (self.TOPIC_ACCOUNTS,  self._handler.process_accounts_event),
            (self.TOPIC_GAME,      self._handler.process_game_event),
            (self.TOPIC_ROUND,     self._handler.process_round_played_event),
        ]
        for topic, handler in configs:
            t = threading.Thread(
                target=self._run_topic, args=(topic, handler), daemon=True
            )
            t.start()
            self._threads.append(t)

    def shutdown(self) -> None:
        self._running = False
        for t in self._threads:
            t.join(timeout=10)
        log.info("all segment consumers stopped")


# ---------------------------------------------------------------------------
# FastAPI health endpoints
# ---------------------------------------------------------------------------

app = FastAPI(title="Send-To-Segment Service", version="1.0.0")


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
    import signal
    import uvicorn

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    dsn       = os.environ.get("DATABASE_URL", "postgresql://localhost/sendtosegment")

    failed_repo  = FailedMessageRepository(dsn)
    consent_repo = ConsentRepository(dsn)
    runner       = SendToSegmentRunner(bootstrap, failed_repo, consent_repo)

    def _shutdown(sig, frame):
        log.info("shutdown signal received")
        runner.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    runner.start()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
