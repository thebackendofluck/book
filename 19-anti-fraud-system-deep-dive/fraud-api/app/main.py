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
AcmeToCasino Fraud Detection API — FastAPI Application

Exposes the fraud detection system as a REST API consumed by:
  - The operator compliance dashboard (Kibana + custom frontend)
  - The wallet service (POST /fraud/analyze — synchronous scoring path)
  - The KYC/AML case management system (GET /fraud/player/{id}/risk)
  - The game service (inline risk gating before game launch)

Endpoint catalogue:
  GET  /fraud/status                   System health + real-time metrics
  GET  /fraud/alerts                   Active fraud alerts (paginated, from ES)
  GET  /fraud/events                   Recent fraud events (paginated, from ES)
  POST /fraud/analyze                  Synchronous transaction risk scoring
  GET  /fraud/rules                    Active detection rules catalogue
  GET  /fraud/player/{player_id}/risk  Player risk profile

Structured logging:
  All log records include `correlation_id` from the incoming request where
  available, enabling cross-service log correlation in Kibana/Grafana Loki.
  This satisfies PCI DSS Req. 10.3.2 (log field: unique identifier).

OpenTelemetry tracing:
  The `correlation_id` from the wallet/game service is propagated as the
  W3C Trace Context trace-id when available, so Jaeger/Tempo can show the
  full distributed trace from game launch through fraud scoring.

Compliance references:
  - PCI DSS Req. 10.2: Log all access to cardholder data environments
  - PCI DSS Req. 6.4: Protect public-facing web applications (rate limiting,
    input validation via Pydantic)
  - AMLD6 Article 18: Real-time transaction monitoring API
  - FATF R.10: Ongoing customer due diligence — risk profile endpoint
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

import redis.asyncio as aioredis
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .elasticsearch_client import ElasticsearchClient
from .kafka_consumer import FraudKafkaConsumer
from .models import (
    AnalyzeTransactionRequest,
    DetectionRule,
    FraudAlertsResponse,
    FraudEventsResponse,
    Jurisdiction,
    PlayerRiskProfile,
    RiskLevel,
    RiskScore,
    SystemStatusResponse,
)
from .rules_engine import RuleContext, RulesRegistry

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration (from environment variables)
# ---------------------------------------------------------------------------

ES_HOSTS: List[str] = os.environ.get(
    "ELASTICSEARCH_HOSTS", "http://elasticsearch:9200"
).split(",")
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://redis:6379/0")
KAFKA_BOOTSTRAP: str = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
API_VERSION: str = os.environ.get("API_VERSION", "1.0.0")

# ---------------------------------------------------------------------------
# Application state — shared across request handlers
# ---------------------------------------------------------------------------

class AppState:
    es_client: ElasticsearchClient
    redis_client: Any
    rules_registry: RulesRegistry
    kafka_consumer: Optional[FraudKafkaConsumer]
    start_time: float


_state = AppState()


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage all long-lived resources for the application lifecycle.

    Startup order:
      1. Elasticsearch client + index templates
      2. Redis connection
      3. Rules registry
      4. Kafka consumer (background task)

    Shutdown order (reverse):
      4. Kafka consumer — commits offsets before closing
      3. (Rules registry is stateless)
      2. Redis — closes connection pool
      1. Elasticsearch — closes connection pool
    """
    _state.start_time = time.time()

    # 1. Elasticsearch
    _state.es_client = ElasticsearchClient(hosts=ES_HOSTS)
    try:
        await _state.es_client.setup_index_templates()
        logger.info("Elasticsearch index templates ready")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Elasticsearch setup incomplete — will retry on first request",
            extra={"error": str(exc)},
        )

    # 2. Redis
    try:
        _state.redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=False,
            max_connections=20,
        )
        await _state.redis_client.ping()
        logger.info("Redis connection established", extra={"url": REDIS_URL})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Redis unavailable at startup — scoring will use empty history",
            extra={"error": str(exc)},
        )
        _state.redis_client = None

    # 3. Rules registry
    _state.rules_registry = RulesRegistry()
    logger.info(
        "Rules registry ready",
        extra={"active_rules": _state.rules_registry.get_active_count()},
    )

    # 4. Kafka consumer
    try:
        _state.kafka_consumer = FraudKafkaConsumer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            es_client=_state.es_client,
            rules_registry=_state.rules_registry,
            redis_client=_state.redis_client,
        )
        await _state.kafka_consumer.start()
        logger.info("Kafka consumer started")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Kafka consumer failed to start — real-time ingestion disabled",
            extra={"error": str(exc)},
        )
        _state.kafka_consumer = None

    yield  # Application is running

    # Shutdown
    if _state.kafka_consumer:
        await _state.kafka_consumer.stop()
    if _state.redis_client:
        await _state.redis_client.aclose()
    await _state.es_client.close()
    logger.info("Fraud API shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AcmeToCasino Fraud Detection API",
    description=(
        "Real-time fraud detection and compliance monitoring API. "
        "Chapter 19 companion implementation — Anti-Fraud System Deep Dive."
    ),
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5601").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request middleware — structured logging + correlation ID
# ---------------------------------------------------------------------------

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: Any) -> Any:
    """
    Attach a correlation_id to every request for cross-service log tracing.

    The correlation_id is sourced from the X-Correlation-ID header if
    supplied by the upstream caller (wallet service, game service), or
    generated as a new UUIDv4.  This satisfies PCI DSS Req. 10.3.2.
    """
    correlation_id = (
        request.headers.get("X-Correlation-ID")
        or request.headers.get("X-Request-ID")
        or str(uuid.uuid4())
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else "unknown",
    )

    start = time.perf_counter_ns()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000

    logger.info(
        "HTTP request",
        extra={
            "status_code": response.status_code,
            "duration_ms": round(elapsed_ms, 2),
        },
    )
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/fraud/status",
    response_model=SystemStatusResponse,
    summary="System health and real-time metrics",
    tags=["System"],
)
async def get_status() -> SystemStatusResponse:
    """
    Return system health status and key operational metrics.

    Used by the Kibana operations dashboard and load balancer health checks.
    The `kafka_consumer_lag` field reflects how far behind real-time the
    consumer is — a growing lag indicates a capacity or performance problem.

    PCI DSS Req. 10.6.1: Review logs and security events daily — this endpoint
    surfaces the metrics that feed that daily review.
    """
    es_connected = await _state.es_client.ping()
    redis_connected = False
    if _state.redis_client:
        try:
            await _state.redis_client.ping()
            redis_connected = True
        except Exception:  # noqa: BLE001
            pass

    events_24h = 0
    alerts_24h = 0
    if es_connected:
        try:
            events_24h = await _state.es_client.get_events_count_24h()
            alerts_24h = await _state.es_client.get_alerts_count_24h()
        except Exception:  # noqa: BLE001
            pass

    consumer_lag: Optional[int] = None
    if _state.kafka_consumer:
        consumer_lag = _state.kafka_consumer.errors  # simplified proxy metric

    overall_status = "healthy"
    if not es_connected:
        overall_status = "degraded"
    if not es_connected and not redis_connected:
        overall_status = "unhealthy"

    return SystemStatusResponse(
        status=overall_status,
        version=API_VERSION,
        uptime_seconds=time.time() - _state.start_time,
        elasticsearch_connected=es_connected,
        redis_connected=redis_connected,
        kafka_consumer_lag=consumer_lag,
        events_indexed_24h=events_24h,
        alerts_generated_24h=alerts_24h,
        rules_active=_state.rules_registry.get_active_count(),
    )


@app.get(
    "/fraud/alerts",
    response_model=FraudAlertsResponse,
    summary="Active fraud alerts",
    tags=["Alerts"],
)
async def get_alerts(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    risk_level: Optional[str] = Query(
        default=None,
        description="Filter by risk level: critical | high | medium | low",
    ),
    status: Optional[str] = Query(
        default=None,
        description="Alert status (default: open). Values: open | under_review | escalated | resolved_tp | resolved_fp",
    ),
    jurisdiction: Optional[str] = Query(
        default=None,
        description="Filter by licensing jurisdiction (e.g. MGA, UKGC)",
    ),
) -> FraudAlertsResponse:
    """
    Return paginated active fraud alerts from Elasticsearch.

    Results are sorted by risk_score descending so the highest-severity
    alerts appear first in the analyst queue.

    PCI DSS Req. 10.6: Alerts must be reviewed at least daily.
    This endpoint is the primary interface for that review workflow.

    AMLD6 Article 18: Operators must be able to retrieve all suspicious
    activity within defined timeframes — this endpoint satisfies that
    requirement for the compliance dashboard.
    """
    try:
        return await _state.es_client.get_active_alerts(
            page=page,
            page_size=page_size,
            risk_level=risk_level,
            status=status,
            jurisdiction=jurisdiction,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to retrieve alerts", extra={"error": str(exc)})
        raise HTTPException(
            status_code=503,
            detail="Alert retrieval temporarily unavailable",
        ) from exc


@app.get(
    "/fraud/events",
    response_model=FraudEventsResponse,
    summary="Recent fraud events",
    tags=["Events"],
)
async def get_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    risk_level: Optional[str] = Query(default=None),
    player_id: Optional[str] = Query(default=None),
    jurisdiction: Optional[str] = Query(default=None),
    from_dt: Optional[datetime] = Query(
        default=None,
        description="Start of time range (ISO 8601, e.g. 2025-01-01T00:00:00Z)",
    ),
    to_dt: Optional[datetime] = Query(
        default=None,
        description="End of time range (ISO 8601)",
    ),
) -> FraudEventsResponse:
    """
    Return paginated fraud events from Elasticsearch.

    Supports time-range and risk-level filtering for the compliance
    dashboard event timeline.  All events include the `correlation_id`
    which can be used to trace back to the originating wallet transaction
    (FATF R.16 end-to-end traceability).
    """
    try:
        return await _state.es_client.get_recent_events(
            page=page,
            page_size=page_size,
            risk_level=risk_level,
            player_id=player_id,
            jurisdiction=jurisdiction,
            from_dt=from_dt,
            to_dt=to_dt,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to retrieve events", extra={"error": str(exc)})
        raise HTTPException(
            status_code=503,
            detail="Event retrieval temporarily unavailable",
        ) from exc


@app.post(
    "/fraud/analyze",
    response_model=RiskScore,
    summary="Synchronous transaction risk scoring",
    tags=["Scoring"],
    status_code=http_status.HTTP_200_OK,
)
async def analyze_transaction(request: AnalyzeTransactionRequest) -> RiskScore:
    """
    Score a transaction synchronously and return a risk score.

    This is the hot path called by the wallet service and game launcher
    before processing a transaction.  Target latency: < 50ms P99
    (Chapter 19 performance SLA).

    The response includes `recommended_action` which the caller must
    honour:
      - allow            — process transaction normally
      - block            — reject transaction, return error to player
      - hold_for_review  — hold transaction pending analyst review
      - require_2fa      — challenge player with step-up authentication
      - require_kyc_step_up — trigger enhanced due diligence flow

    The `feature_importances` field in the response satisfies the
    UKGC/MGA explainability obligation — operators must be able to
    explain to players and regulators why a decision was made.

    PCI DSS Req. 10.2.1: Log access to the scoring endpoint with the
    full context available in the request (handled by request middleware).
    """
    start_ns = time.perf_counter_ns()

    logger.info(
        "Fraud analysis requested",
        extra={
            "player_id": request.player_id,
            "transaction_type": request.transaction_type,
            "amount": request.amount,
            "jurisdiction": request.jurisdiction,
        },
    )

    # Load player history from Redis
    history: Dict[str, Any] = {}
    if _state.redis_client:
        try:
            from .kafka_consumer import PlayerHistoryLoader
            loader = PlayerHistoryLoader(_state.redis_client)
            history = await loader.load(request.player_id, request.device_fingerprint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Redis history fetch failed — scoring with empty history",
                extra={"error": str(exc)},
            )

    # Build rule context
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

    # Run rules engine
    rules_score, rule_results = _state.rules_registry.evaluate_all(context)
    fired = [r for r in rule_results if r.fired]

    # Build feature importances for explainability (UKGC/MGA obligation)
    feature_importances: Dict[str, float] = {}
    for r in fired:
        feature_importances[r.rule_id] = r.score_contribution

    # Determine risk level and recommended action
    risk_level = _score_to_level(rules_score)
    recommended_action = _recommend_action(risk_level, fired)
    block_reason: Optional[str] = None
    if recommended_action == "block":
        block_reason = _build_block_reason(fired)

    elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000

    # Async: index the scored event and raise alert if warranted
    # (do not await — caller should not wait for Elasticsearch write)
    import asyncio
    asyncio.create_task(
        _background_index_event(request, rules_score, risk_level, rule_results)
    )

    logger.info(
        "Fraud analysis complete",
        extra={
            "player_id": request.player_id,
            "risk_score": rules_score,
            "risk_level": risk_level,
            "recommended_action": recommended_action,
            "latency_ms": round(elapsed_ms, 2),
        },
    )

    return RiskScore(
        correlation_id=request.correlation_id,
        player_id=request.player_id,
        transaction_id=request.metadata.get("transaction_id"),
        risk_score=rules_score,
        risk_level=risk_level,
        typologies=[r.typology for r in fired],
        rule_hits=[r.rule_id for r in fired],
        recommended_action=recommended_action,
        block_reason=block_reason,
        feature_importances=feature_importances,
        processing_time_ms=round(elapsed_ms, 2),
    )


@app.get(
    "/fraud/rules",
    response_model=List[DetectionRule],
    summary="Active detection rules catalogue",
    tags=["Rules"],
)
async def get_rules(
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by rule status: active | shadow | disabled | testing",
    ),
    jurisdiction: Optional[str] = Query(
        default=None,
        description="Filter rules applicable to a specific jurisdiction",
    ),
) -> List[DetectionRule]:
    """
    Return the catalogue of all fraud detection rules.

    Exposes the rules engine configuration to the compliance dashboard so
    analysts can audit what rules are active and understand threshold values.

    UKGC/MGA explainability requirement: operators must document the logic
    applied to automated decisions affecting players.  This endpoint is the
    machine-readable form of that documentation.
    """
    rules = _state.rules_registry.get_all()

    if status_filter:
        rules = [r for r in rules if r.status.value == status_filter]
    if jurisdiction:
        try:
            j = Jurisdiction(jurisdiction)
            rules = [
                r for r in rules
                if not r.jurisdiction_scope or j in r.jurisdiction_scope
            ]
        except ValueError:
            pass  # Unknown jurisdiction — return all rules

    return rules


@app.get(
    "/fraud/player/{player_id}/risk",
    response_model=PlayerRiskProfile,
    summary="Player risk profile",
    tags=["Players"],
)
async def get_player_risk_profile(player_id: str) -> PlayerRiskProfile:
    """
    Return a comprehensive risk profile for a specific player.

    Used by:
      - KYC/AML case management (risk-based CDD trigger)
      - Responsible gambling system (problem gambling risk signals)
      - Payment processing (pre-authorisation risk gate)
      - Compliance officer review

    Profile components:
      - Current risk score and trend
      - KYC/AML status (PEP, sanctions, KYC tier)
      - Velocity metrics (24h / 7d / 30d deposits)
      - Fraud history (open and closed alerts)
      - Geo and device anomaly counts

    AMLD6 Article 18: Risk-based approach to customer due diligence —
    this profile is the data substrate for CDD decisions.
    FATF R.10: Ongoing monitoring — the profile reflects real-time
    transaction history, not a static snapshot.
    """
    # Fetch recent events from Elasticsearch
    try:
        recent_events = await _state.es_client.get_player_recent_events(
            player_id=player_id, limit=200
        )
        open_alert_count = await _state.es_client.get_player_open_alert_count(player_id)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "ES query failed for player risk profile",
            extra={"player_id": player_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=503,
            detail="Profile retrieval temporarily unavailable",
        ) from exc

    if not recent_events and open_alert_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No fraud data found for player {player_id}",
        )

    # Aggregate metrics from events
    deposit_events = [e for e in recent_events if e.transaction_type == "deposit"]
    now = datetime.now(timezone.utc)

    def _sum_deposits(hours: int) -> tuple[int, float]:
        cutoff = now.timestamp() - hours * 3600
        relevant = [
            e for e in deposit_events
            if e.created_at.timestamp() > cutoff
        ]
        return len(relevant), sum(e.amount for e in relevant)

    count_24h, amount_24h = _sum_deposits(24)
    count_7d, amount_7d = _sum_deposits(24 * 7)
    count_30d, amount_30d = _sum_deposits(24 * 30)

    # Current risk score = max score in last 24 hours (conservative)
    recent_24h = [
        e for e in recent_events
        if e.created_at.timestamp() > now.timestamp() - 86400
    ]
    current_score = max((e.risk_score for e in recent_24h), default=0.0)

    # 7-day average
    week_scores = [
        e.risk_score for e in recent_events
        if e.created_at.timestamp() > now.timestamp() - 7 * 86400
    ]
    avg_7d = sum(week_scores) / len(week_scores) if week_scores else 0.0
    trend = current_score - avg_7d

    # Known geo and device signals
    known_countries = list({
        e.country_code for e in recent_events if e.country_code
    })
    known_fps = list({
        e.device_fingerprint for e in recent_events if e.device_fingerprint
    })

    geo_anomaly_count = sum(
        1 for e in recent_events
        if "geo_anomaly" in [t.value for t in e.typologies]
        or "account_takeover" in [t.value for t in e.typologies]
    )
    device_anomaly_count = sum(
        1 for e in recent_events
        if "device_sharing" in [t.value for t in e.typologies]
    )

    # Determine AML risk category (AMLD6 risk-based approach)
    aml_category = _derive_aml_category(current_score, open_alert_count)

    jurisdiction = (
        recent_events[0].jurisdiction if recent_events else Jurisdiction.UNKNOWN
    )
    brand_id = recent_events[0].brand_id if recent_events else 0
    last_alert_at = recent_events[0].created_at if recent_events and open_alert_count else None

    return PlayerRiskProfile(
        player_id=player_id,
        brand_id=brand_id,
        jurisdiction=jurisdiction,
        current_risk_score=round(current_score, 4),
        current_risk_level=_score_to_level(current_score),
        risk_score_7d_trend=round(trend, 4),
        kyc_verified=False,   # populated by KYC service integration in production
        aml_risk_category=aml_category,
        deposit_count_24h=count_24h,
        deposit_amount_24h=amount_24h,
        deposit_count_7d=count_7d,
        deposit_amount_7d=amount_7d,
        deposit_count_30d=count_30d,
        deposit_amount_30d=amount_30d,
        open_alert_count=open_alert_count,
        total_alert_count=len(recent_events),
        last_alert_at=last_alert_at,
        known_countries=known_countries,
        known_device_fingerprints=[fp[:16] + "..." for fp in known_fps],
        geo_anomaly_count_30d=geo_anomaly_count,
        device_anomaly_count_30d=device_anomaly_count,
        account_frozen=False,  # populated by account service in production
        requires_enhanced_monitoring=current_score >= 0.50,
        data_sources=["elasticsearch:fraud-events-*", "redis:player-history"],
    )


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

async def _background_index_event(
    request: AnalyzeTransactionRequest,
    risk_score: float,
    risk_level: RiskLevel,
    rule_results: list,
) -> None:
    """
    Async background task: index a synchronously-scored event into ES and
    raise an alert if warranted.  Errors here are logged but do not fail
    the caller's response — the scoring result is already returned.
    """
    from .kafka_consumer import _build_alert_summary
    from .models import AlertStatus, FraudAlert, FraudEvent, FraudTypology

    try:
        fired = [r for r in rule_results if r.fired]
        typologies = list({r.typology for r in fired})

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
            risk_score=risk_score,
            risk_level=risk_level,
            typologies=typologies,
            rule_hits=[r.rule_id for r in fired],
            model_scores={"rules_engine": risk_score},
            metadata={**request.metadata, "source": "api_analyze"},
        )
        await _state.es_client.index_fraud_event(event)

        if risk_score >= 0.50:
            aml_required = any(
                t in (FraudTypology.STRUCTURING, FraudTypology.MONEY_LAUNDERING, FraudTypology.COLLUSION)
                for t in typologies
            )
            alert = FraudAlert(
                correlation_id=request.correlation_id,
                fraud_event_id=event.event_id,
                player_id=request.player_id,
                brand_id=request.brand_id,
                jurisdiction=request.jurisdiction,
                risk_score=risk_score,
                risk_level=risk_level,
                typologies=typologies,
                summary=_build_alert_summary(event, [r.rule_id for r in fired]),
                status=AlertStatus.OPEN,
                automated_action="account_freeze" if risk_score >= 0.90 else "none",
                aml_report_required=aml_required,
            )
            await _state.es_client.index_fraud_alert(alert)

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Background event indexing failed",
            extra={"correlation_id": request.correlation_id, "error": str(exc)},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_to_level(score: float) -> RiskLevel:
    if score >= 0.90:
        return RiskLevel.CRITICAL
    if score >= 0.70:
        return RiskLevel.HIGH
    if score >= 0.50:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _recommend_action(risk_level: RiskLevel, fired_rules: list) -> str:
    """
    Map risk level and fired rules to a recommended action for the caller.

    The mapping is intentionally conservative — when in doubt, hold for
    review rather than auto-blocking.  UKGC LCCP Social Responsibility
    Code 3.4.1 requires that automated decisions have human oversight.
    """
    if risk_level == RiskLevel.CRITICAL:
        return "block"
    if risk_level == RiskLevel.HIGH:
        # ATO signals should trigger step-up auth rather than hard block
        ato_rules = {"RULE-GEO-002", "RULE-GEO-001", "RULE-AMT-001"}
        if any(r.rule_id in ato_rules for r in fired_rules):
            return "require_2fa"
        return "hold_for_review"
    if risk_level == RiskLevel.MEDIUM:
        return "hold_for_review"
    return "allow"


def _build_block_reason(fired_rules: list) -> Optional[str]:
    """Build a machine-readable block reason code from fired rules."""
    if not fired_rules:
        return None
    primary = fired_rules[0]
    typology_code = primary.typology.value.upper().replace("_", "_")
    return f"FRAUD_{typology_code}_{primary.rule_id}"


def _derive_aml_category(risk_score: float, open_alerts: int) -> str:
    """
    Derive AMLD6-aligned AML risk category from risk score and alert count.

    Categories: low | standard | high | very_high
    Reference: AMLD6 Article 18 — risk-based approach to CDD.
    """
    if risk_score >= 0.70 or open_alerts >= 3:
        return "very_high"
    if risk_score >= 0.50 or open_alerts >= 1:
        return "high"
    if risk_score >= 0.20:
        return "standard"
    return "low"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        workers=1,           # Single worker — Kafka consumer uses asyncio
        loop="uvloop",
    )
