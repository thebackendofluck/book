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
AcmeToCasino Fraud Detection API — Kafka Consumer

Consumes wallet and game events from the Kafka event bus, runs each event
through the fraud rules engine, indexes results in Elasticsearch, and
publishes high-risk alerts back to a Kafka output topic for downstream
consumers (case management, notifications, game service).

Topic layout (mirrors Chapter 19 architecture):
  Input topics:
    wallet.events          — deposit, withdrawal, refund events from the wallet svc
    game.events            — bet, win events from the game engine
    user.lifecycle         — registration, login, KYC-status-change events

  Output topics:
    fraud.alerts           — FraudAlert payloads for downstream consumers
    fraud.account.actions  — account freeze / enhanced-monitoring commands

The consumer runs in a dedicated asyncio task started by the FastAPI lifespan
manager.  It can be scaled horizontally by adding consumer group members —
Kafka's partition assignment ensures each event is processed exactly once
across the group.

Compliance references:
  - PCI DSS Req. 10.2: Log all access and actions on cardholder data.
    Every consumed event is logged with correlation_id before processing.
  - FATF R.10: Ongoing transaction monitoring — this consumer IS the
    real-time monitoring system described in FATF guidance.
  - AMLD6 Article 18(2): Continuous, automated monitoring of customer
    transactions. Consumer restart resilience (committed offset tracking)
    ensures no events are silently dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError

from .elasticsearch_client import ElasticsearchClient
from .models import (
    AlertStatus,
    AnalyzeTransactionRequest,
    FraudAlert,
    FraudEvent,
    Jurisdiction,
    RiskLevel,
)
from .rules_engine import RuleContext, RulesRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Risk threshold above which an alert is created and published
ALERT_THRESHOLD = 0.50

# Risk threshold above which an automated account freeze action is triggered
AUTO_FREEZE_THRESHOLD = 0.90

# Input topics consumed by this service
INPUT_TOPICS = [
    "wallet.events",
    "game.events",
    "user.lifecycle",
]

# Output topics produced by this service
ALERT_TOPIC = "fraud.alerts"
ACCOUNT_ACTION_TOPIC = "fraud.account.actions"

# Consumer group — all instances of this service share the same group
CONSUMER_GROUP = "fraud-detection-service"


# ---------------------------------------------------------------------------
# Event normaliser
# ---------------------------------------------------------------------------

def _normalise_wallet_event(raw: Dict[str, Any]) -> Optional[AnalyzeTransactionRequest]:
    """
    Map a raw wallet.events Kafka message to an AnalyzeTransactionRequest.

    Wallet events follow the CoreEventInfo / DepositInfo schema from Chapter 19:
      {
        "traceId":       "uuid",
        "eventType":     "deposit",
        "userId":        12345,
        "brandId":       1,
        "jurisdiction":  "MGA",
        "country":       "MT",
        "currency":      "EUR",
        "amount":        10000,          # minor units
        "depositNumber": 3,
        "paymentMethod": "card",
        "ipAddress":     "...",
        "deviceFp":      "...",
        "userAgent":     "..."
      }

    Returns None if the event does not contain enough data to score.
    """
    try:
        event_type = raw.get("eventType", "")
        if event_type not in ("deposit", "withdrawal", "refund"):
            return None

        return AnalyzeTransactionRequest(
            correlation_id=raw.get("traceId") or str(uuid4()),
            player_id=str(raw["userId"]),
            brand_id=int(raw.get("brandId", 0)),
            jurisdiction=Jurisdiction(raw.get("jurisdiction", "UNKNOWN")),
            transaction_type=event_type,
            amount=float(raw["amount"]),
            currency=raw.get("currency", "EUR"),
            payment_method=raw.get("paymentMethod"),
            deposit_number=raw.get("depositNumber"),
            ip_address=raw.get("ipAddress"),
            country_code=raw.get("country"),
            device_fingerprint=raw.get("deviceFp"),
            user_agent=raw.get("userAgent"),
            metadata={
                "source_topic": "wallet.events",
                "raw_event_type": event_type,
            },
        )
    except (KeyError, ValueError) as exc:
        logger.warning(
            "Failed to normalise wallet event",
            extra={"error": str(exc), "raw_keys": list(raw.keys())},
        )
        return None


def _normalise_game_event(raw: Dict[str, Any]) -> Optional[AnalyzeTransactionRequest]:
    """
    Map a raw game.events Kafka message to an AnalyzeTransactionRequest.

    Game events include bet placements and wins — bet velocity is the primary
    bot-detection signal extracted here.
    """
    try:
        event_type = raw.get("eventType", "")
        if event_type not in ("bet", "win"):
            return None

        return AnalyzeTransactionRequest(
            correlation_id=raw.get("sessionId") or str(uuid4()),
            player_id=str(raw["playerId"]),
            brand_id=int(raw.get("brandId", 0)),
            jurisdiction=Jurisdiction(raw.get("jurisdiction", "UNKNOWN")),
            transaction_type=event_type,
            amount=float(raw["betAmount"]),
            currency=raw.get("currency", "EUR"),
            payment_method=None,
            ip_address=raw.get("ipAddress"),
            country_code=raw.get("country"),
            device_fingerprint=raw.get("deviceFp"),
            game_session_id=raw.get("sessionId"),
            metadata={
                "source_topic": "game.events",
                "game_type": raw.get("gameType"),
                "game_id": raw.get("gameId"),
            },
        )
    except (KeyError, ValueError) as exc:
        logger.warning(
            "Failed to normalise game event",
            extra={"error": str(exc)},
        )
        return None


# ---------------------------------------------------------------------------
# Redis player history loader
# ---------------------------------------------------------------------------

class PlayerHistoryLoader:
    """
    Fetches pre-aggregated player metrics from Redis to populate the
    RuleContext.player_history dictionary before rule evaluation.

    Redis key schema:
      player:{player_id}:deposit_count_1h      — INCR, TTL 3600s
      player:{player_id}:deposit_count_24h     — INCR, TTL 86400s
      player:{player_id}:deposit_amount_24h    — INCR (minor units), TTL 86400s
      player:{player_id}:bet_count_1m          — INCR, TTL 60s
      player:{player_id}:known_countries       — SADD (ISO codes)
      player:{player_id}:device_fps            — SADD (hashed fingerprints)
      device:{device_fp}:player_count          — SADD then SCARD
      device:{device_fp}:bonus_claimed         — SET 1 when bonus is claimed
      player:{player_id}:deposit_amounts_24h   — LPUSH, LTRIM 100, TTL 86400s
      player:{player_id}:card_bins_1h          — LPUSH, LTRIM 50, TTL 3600s
      player:{player_id}:last_login_country    — SET, TTL 86400s
      player:{player_id}:last_login_at         — SET (ISO datetime), TTL 86400s
      player:{player_id}:collusion_score       — SET float, TTL 86400s
      player:{player_id}:colluding_partner     — SET player_id, TTL 86400s
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def load(self, player_id: str, device_fp: Optional[str]) -> Dict[str, Any]:
        """
        Load all relevant metrics for a player from Redis in a single pipeline
        call to minimise round-trips.
        """
        if self._redis is None:
            # Graceful degradation: return empty history if Redis is unavailable.
            # Rules that require history will not fire, but the system stays up.
            logger.warning(
                "Redis unavailable — returning empty player history",
                extra={"player_id": player_id},
            )
            return {}

        prefix = f"player:{player_id}"
        device_prefix = f"device:{device_fp}" if device_fp else None

        pipe = self._redis.pipeline()

        pipe.get(f"{prefix}:deposit_count_1h")
        pipe.get(f"{prefix}:deposit_count_24h")
        pipe.get(f"{prefix}:deposit_amount_24h")
        pipe.get(f"{prefix}:bet_count_1m")
        pipe.smembers(f"{prefix}:known_countries")
        pipe.smembers(f"{prefix}:device_fps")
        pipe.lrange(f"{prefix}:deposit_amounts_24h", 0, 99)
        pipe.lrange(f"{prefix}:card_bins_1h", 0, 49)
        pipe.get(f"{prefix}:last_login_country")
        pipe.get(f"{prefix}:last_login_at")
        pipe.get(f"{prefix}:collusion_score")
        pipe.get(f"{prefix}:colluding_partner")

        if device_prefix:
            pipe.scard(f"{device_prefix}:players")
            pipe.get(f"{device_prefix}:bonus_claimed")

        results = await pipe.execute()

        history: Dict[str, Any] = {
            "deposit_count_1h":      int(results[0] or 0),
            "deposit_count_24h":     int(results[1] or 0),
            "deposit_amount_24h":    float(results[2] or 0),
            "bet_count_1m":          int(results[3] or 0),
            "known_countries":       [c.decode() if isinstance(c, bytes) else c
                                      for c in (results[4] or set())],
            "known_device_fps":      [f.decode() if isinstance(f, bytes) else f
                                      for f in (results[5] or set())],
            "deposit_amounts_24h":   [float(a) for a in (results[6] or [])],
            "card_bins_1h":          [b.decode() if isinstance(b, bytes) else b
                                      for b in (results[7] or [])],
            "last_login_country":    (results[8] or b"").decode() or None,
            "last_login_at":         (results[9] or b"").decode() or None,
            "collusion_score":       float(results[10] or 0),
            "colluding_partner_id":  (results[11] or b"").decode() or None,
        }

        if device_prefix:
            history["device_player_count"] = int(results[12] or 0)
            history["bonus_claimed_on_device"] = bool(results[13])

        return history

    async def update_after_event(
        self,
        player_id: str,
        device_fp: Optional[str],
        transaction_type: str,
        amount: float,
        country_code: Optional[str],
        payment_method: Optional[str],
        card_bin: Optional[str],
    ) -> None:
        """
        Update Redis aggregates after an event has been processed.

        This keeps the sliding-window counters current so the next event
        for the same player has accurate history.

        PCI DSS Req. 10.2.1: All actions on cardholder data are logged via
        the Kafka event stream; these Redis writes maintain the real-time
        scoring state.
        """
        if self._redis is None:
            return

        prefix = f"player:{player_id}"
        pipe = self._redis.pipeline()

        if transaction_type == "deposit":
            pipe.incr(f"{prefix}:deposit_count_1h")
            pipe.expire(f"{prefix}:deposit_count_1h", 3600)
            pipe.incr(f"{prefix}:deposit_count_24h")
            pipe.expire(f"{prefix}:deposit_count_24h", 86400)
            pipe.incrbyfloat(f"{prefix}:deposit_amount_24h", amount)
            pipe.expire(f"{prefix}:deposit_amount_24h", 86400)
            pipe.lpush(f"{prefix}:deposit_amounts_24h", amount)
            pipe.ltrim(f"{prefix}:deposit_amounts_24h", 0, 99)
            pipe.expire(f"{prefix}:deposit_amounts_24h", 86400)

            if card_bin and payment_method in ("card", "credit_card", "debit_card"):
                pipe.lpush(f"{prefix}:card_bins_1h", card_bin)
                pipe.ltrim(f"{prefix}:card_bins_1h", 0, 49)
                pipe.expire(f"{prefix}:card_bins_1h", 3600)

        elif transaction_type == "bet":
            pipe.incr(f"{prefix}:bet_count_1m")
            pipe.expire(f"{prefix}:bet_count_1m", 60)

        if country_code:
            pipe.sadd(f"{prefix}:known_countries", country_code)
            pipe.set(f"{prefix}:last_login_country", country_code)
            pipe.expire(f"{prefix}:last_login_country", 86400)
            pipe.set(f"{prefix}:last_login_at", datetime.now(timezone.utc).isoformat())
            pipe.expire(f"{prefix}:last_login_at", 86400)

        if device_fp:
            pipe.sadd(f"{prefix}:device_fps", device_fp)
            pipe.sadd(f"device:{device_fp}:players", player_id)

        await pipe.execute()


# ---------------------------------------------------------------------------
# Fraud Consumer
# ---------------------------------------------------------------------------

class FraudKafkaConsumer:
    """
    Async Kafka consumer that drives the real-time fraud detection pipeline.

    Lifecycle:
      1. start() — called from FastAPI lifespan; begins consuming
      2. _consume_loop() — runs until stop() is called
      3. stop() — called on application shutdown; commits offsets and closes

    Processing flow per message:
      1. Deserialise and normalise raw event
      2. Load player history from Redis
      3. Build RuleContext
      4. Run RulesRegistry.evaluate_all()
      5. Index FraudEvent to Elasticsearch
      6. If score >= ALERT_THRESHOLD: create FraudAlert, index it, publish to Kafka
      7. If score >= AUTO_FREEZE_THRESHOLD: publish account-freeze command
      8. Update Redis player history
    """

    def __init__(
        self,
        bootstrap_servers: str,
        es_client: ElasticsearchClient,
        rules_registry: RulesRegistry,
        redis_client: Any,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._es = es_client
        self._rules = rules_registry
        self._history_loader = PlayerHistoryLoader(redis_client)

        self._consumer: Optional[AIOKafkaConsumer] = None
        self._producer: Optional[AIOKafkaProducer] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Lightweight metrics (no Prometheus dependency in consumer process)
        self.messages_processed = 0
        self.alerts_generated = 0
        self.errors = 0

    async def start(self) -> None:
        """Start the Kafka consumer and producer, then launch the consume loop."""
        self._consumer = AIOKafkaConsumer(
            *INPUT_TOPICS,
            bootstrap_servers=self._bootstrap,
            group_id=CONSUMER_GROUP,
            auto_offset_reset="latest",
            enable_auto_commit=False,      # Manual commit for exactly-once semantics
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            max_poll_records=100,
            session_timeout_ms=30_000,
            heartbeat_interval_ms=10_000,
        )
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",               # Wait for all in-sync replicas (durability)
            enable_idempotence=True,  # Exactly-once producer semantics
        )

        await self._consumer.start()
        await self._producer.start()
        self._running = True

        self._task = asyncio.create_task(self._consume_loop())
        logger.info(
            "Fraud Kafka consumer started",
            extra={"topics": INPUT_TOPICS, "group": CONSUMER_GROUP},
        )

    async def stop(self) -> None:
        """Gracefully stop the consumer — commit offsets before closing."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._consumer:
            await self._consumer.commit()
            await self._consumer.stop()
        if self._producer:
            await self._producer.stop()

        logger.info(
            "Fraud Kafka consumer stopped",
            extra={
                "messages_processed": self.messages_processed,
                "alerts_generated": self.alerts_generated,
                "errors": self.errors,
            },
        )

    async def _consume_loop(self) -> None:
        """
        Main consume loop.  Polls Kafka in batches, processes each message,
        then commits offsets only after the batch is fully processed.

        This ensures no message is lost if the service crashes mid-batch —
        on restart, the consumer replays from the last committed offset.
        This is the AMLD6 Article 18(2) 'continuous monitoring' guarantee.
        """
        assert self._consumer is not None

        while self._running:
            try:
                # Batch poll — reduces per-message overhead
                msg_batch = await self._consumer.getmany(
                    timeout_ms=1_000, max_records=100
                )
                if not msg_batch:
                    continue

                for tp, messages in msg_batch.items():
                    for msg in messages:
                        if not isinstance(msg.value, dict):
                            continue
                        await self._process_message(
                            topic=tp.topic,
                            raw=msg.value,
                        )

                # Commit after the whole batch — ensures at-least-once delivery
                await self._consumer.commit()

            except KafkaError as exc:
                logger.error(
                    "Kafka consumer error",
                    extra={"error": str(exc)},
                )
                self.errors += 1
                await asyncio.sleep(1)  # Back-off before retrying

            except asyncio.CancelledError:
                break

            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Unexpected consumer error",
                    extra={"error": str(exc)},
                )
                self.errors += 1

    async def _process_message(self, topic: str, raw: Dict[str, Any]) -> None:
        """
        Process a single Kafka message end-to-end.

        PCI DSS Req. 10.2: Log correlation_id at ingestion — every message
        receives a correlation_id before any processing so the full pipeline
        is traceable even if downstream steps fail.
        """
        start_ns = time.perf_counter_ns()

        # Step 1: Normalise
        request: Optional[AnalyzeTransactionRequest] = None
        if topic == "wallet.events":
            request = _normalise_wallet_event(raw)
        elif topic == "game.events":
            request = _normalise_game_event(raw)
        else:
            # user.lifecycle and other topics — log and skip (no scoring needed)
            return

        if request is None:
            return

        logger.debug(
            "Processing fraud event",
            extra={
                "correlation_id": request.correlation_id,
                "player_id": request.player_id,
                "transaction_type": request.transaction_type,
                "topic": topic,
            },
        )

        try:
            # Step 2: Load player history from Redis
            history = await self._history_loader.load(
                player_id=request.player_id,
                device_fp=request.device_fingerprint,
            )

            # Step 3: Build RuleContext
            context = RuleContext(
                correlation_id=request.correlation_id,
                player_id=request.player_id,
                brand_id=request.brand_id,
                jurisdiction=request.jurisdiction,
                transaction_type=request.transaction_type,
                amount=request.amount,
                currency=request.currency,
                payment_method=request.payment_method,
                deposit_number=request.deposit_number,
                ip_address=request.ip_address,
                country_code=request.country_code,
                device_fingerprint=request.device_fingerprint,
                user_agent=request.user_agent,
                player_history=history,
                metadata=request.metadata,
            )

            # Step 4: Run rules engine
            rules_score, rule_results = self._rules.evaluate_all(context)

            fired_rule_ids = [r.rule_id for r in rule_results if r.fired]
            typologies = list({r.typology for r in rule_results if r.fired})
            model_scores = {"rules_engine": rules_score}

            # Step 5: Determine final risk level
            risk_level = _score_to_level(rules_score)

            # Step 6: Build and index FraudEvent
            elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
            event = FraudEvent(
                correlation_id=request.correlation_id,
                player_id=request.player_id,
                brand_id=request.brand_id,
                jurisdiction=request.jurisdiction,
                transaction_type=request.transaction_type,
                amount=request.amount,
                currency=request.currency,
                payment_method=request.payment_method,
                deposit_number=request.deposit_number,
                ip_address=request.ip_address,
                country_code=request.country_code,
                device_fingerprint=request.device_fingerprint,
                user_agent=request.user_agent,
                game_session_id=request.game_session_id,
                risk_score=rules_score,
                risk_level=risk_level,
                typologies=typologies,
                rule_hits=fired_rule_ids,
                model_scores=model_scores,
                metadata={
                    **request.metadata,
                    "scoring_latency_ms": round(elapsed_ms, 2),
                },
            )
            await self._es.index_fraud_event(event)

            # Step 7: Generate alert if score crosses investigation threshold
            if rules_score >= ALERT_THRESHOLD:
                await self._generate_alert(event, fired_rule_ids, rules_score)

            # Step 8: Update Redis history
            card_bin = _extract_card_bin(raw)
            await self._history_loader.update_after_event(
                player_id=request.player_id,
                device_fp=request.device_fingerprint,
                transaction_type=request.transaction_type,
                amount=request.amount,
                country_code=request.country_code,
                payment_method=request.payment_method,
                card_bin=card_bin,
            )

            self.messages_processed += 1

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Event processing failed",
                extra={
                    "correlation_id": request.correlation_id,
                    "player_id": request.player_id,
                    "error": str(exc),
                },
            )
            self.errors += 1

    async def _generate_alert(
        self,
        event: FraudEvent,
        fired_rules: List[str],
        score: float,
    ) -> None:
        """
        Create, index, and publish a FraudAlert for a high-risk event.

        Auto-freeze: if score >= AUTO_FREEZE_THRESHOLD an account-freeze
        command is also published to `fraud.account.actions`.  This satisfies
        the Chapter 19 'Critical (score > 0.9): Automated response within
        seconds' requirement.
        """
        automated_action = "none"
        if score >= AUTO_FREEZE_THRESHOLD:
            automated_action = "account_freeze"

        # AMLD6 / FATF R.20: flag whether this triggers an AML report obligation
        aml_required = any(
            t in event.typologies
            for t in ("structuring", "money_laundering", "collusion")
        )

        alert = FraudAlert(
            correlation_id=event.correlation_id,
            fraud_event_id=event.event_id,
            player_id=event.player_id,
            brand_id=event.brand_id,
            jurisdiction=event.jurisdiction,
            risk_score=score,
            risk_level=event.risk_level,
            typologies=event.typologies,
            summary=_build_alert_summary(event, fired_rules),
            status=AlertStatus.OPEN,
            automated_action=automated_action,
            aml_report_required=aml_required,
        )

        # Index to Elasticsearch
        await self._es.index_fraud_alert(alert)

        # Publish to Kafka fraud.alerts topic
        if self._producer:
            alert_payload = alert.model_dump(mode="json")
            await self._producer.send(ALERT_TOPIC, value=alert_payload)

            # Publish account action if automated freeze is warranted
            if automated_action == "account_freeze":
                await self._producer.send(
                    ACCOUNT_ACTION_TOPIC,
                    value={
                        "action": "freeze",
                        "player_id": event.player_id,
                        "brand_id": event.brand_id,
                        "alert_id": alert.alert_id,
                        "correlation_id": event.correlation_id,
                        "reason": "AUTO_FREEZE_SCORE_THRESHOLD",
                        "risk_score": score,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                logger.warning(
                    "Auto-freeze triggered",
                    extra={
                        "player_id": event.player_id,
                        "risk_score": score,
                        "alert_id": alert.alert_id,
                        "correlation_id": event.correlation_id,
                    },
                )

        self.alerts_generated += 1
        logger.info(
            "Fraud alert generated",
            extra={
                "alert_id": alert.alert_id,
                "risk_level": alert.risk_level,
                "player_id": alert.player_id,
                "aml_required": aml_required,
                "automated_action": automated_action,
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_to_level(score: float) -> RiskLevel:
    """Map a numeric risk score to the four-tier RiskLevel enum."""
    if score >= 0.90:
        return RiskLevel.CRITICAL
    if score >= 0.70:
        return RiskLevel.HIGH
    if score >= 0.50:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _extract_card_bin(raw: Dict[str, Any]) -> Optional[str]:
    """
    Extract the first 6 digits of a card number (BIN) from a raw event payload.

    The BIN is used by RULE-CRD-001 for card testing detection.
    We store only the BIN — never the full PAN — to comply with
    PCI DSS Req. 3.3 (mask PAN when displayed) and Req. 3.4 (render PAN
    unreadable anywhere it is stored).
    """
    card_number = raw.get("cardNumber") or raw.get("card_number", "")
    if card_number and len(card_number) >= 6:
        return card_number[:6]
    return raw.get("cardBin") or raw.get("card_bin")


def _build_alert_summary(event: FraudEvent, fired_rules: List[str]) -> str:
    """Build a human-readable alert summary for the analyst review queue."""
    typology_str = ", ".join(t.value for t in event.typologies) if event.typologies else "unknown"
    rule_str = ", ".join(fired_rules[:3])
    if len(fired_rules) > 3:
        rule_str += f" (+{len(fired_rules) - 3} more)"
    return (
        f"[{event.risk_level.value.upper()}] Player {event.player_id} — "
        f"{event.transaction_type} of {event.amount} {event.currency}. "
        f"Typologies: {typology_str}. Rules: {rule_str}. "
        f"Score: {event.risk_score:.2f}."
    )
