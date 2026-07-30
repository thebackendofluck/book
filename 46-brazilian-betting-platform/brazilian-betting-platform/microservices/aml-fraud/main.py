# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AML/Fraud Detection Service
============================
FastAPI microservice implementing Anti-Money Laundering and fraud detection
for a Brazilian betting platform operating under Lei 14.790/2023 and
Lei 9.613/1998 (Lei de Lavagem de Dinheiro).

Endpoints:
  POST /aml/analyze/{cpf}      — Real-time transaction risk analysis
  GET  /aml/risk/{cpf}         — CPF aggregated risk score (ML-enhanced)
  POST /aml/report/coaf        — Generate COAF suspicious activity report
  GET  /aml/graph/{cpf}        — CPF relationship graph from Neo4j (2-hop)
  POST /aml/pix/fraud-check    — PIX-specific fraud pattern detection
  POST /aml/score              — Direct ML scoring endpoint
  POST /aml/score/batch        — Batch ML scoring (≤ 500 transactions)
  GET  /health                 — Health check

COAF reporting thresholds (Resolução BCB 44/2020):
  - Cash transactions > R$ 10.000 per event
  - Aggregate > R$ 50.000 in 30 days
  - Any structuring / smurfing pattern regardless of amount
"""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator

import structlog
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Path, status
from fastapi.middleware.cors import CORSMiddleware

from coaf_reporter import COAFReporter
from database import create_tables, dispose_engine, get_neo4j
from fraud_scorer import (
    BatchScoringRequest,
    BatchScoringResult,
    ScoringResult,
    is_model_loaded,
    load_or_create_model,
    score_batch,
    score_one,
)
from graph_analyzer import GraphAnalyzer
from models import (
    AnalysisResult,
    BatchScoringRequest,
    BatchScoringResult,
    COAFReport,
    COAFReportRequest,
    GraphRelationship,
    HealthStatus,
    PIXFraudCheckRequest,
    PIXFraudResult,
    RiskLevel,
    RiskScore,
    ScoringResult,
    TransactionAnalysis,
    TransactionFeatures,
)
from pix_fraud_detector import PIXFraudDetector

# ── Structured logging ────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
log = structlog.get_logger(__name__)

# ── Service-level constants ───────────────────────────────────────────────────

SERVICE_NAME = "aml-fraud"
SERVICE_VERSION = "2.0.0"

# COAF thresholds (Resolução BCB 44/2020)
_THRESHOLD_WITHDRAWAL_BRL = Decimal("10000.00")
_THRESHOLD_LARGE_BRL = Decimal("50000.00")
_THRESHOLD_VERY_LARGE_BRL = Decimal("100000.00")
_THRESHOLD_ROUND_AMOUNT_MIN = Decimal("5000.00")
_SANCTIONED_COUNTRIES = frozenset({"IR", "KP", "CU", "SY", "BY"})


# ── Application state (singleton services) ────────────────────────────────────

_graph_analyzer: GraphAnalyzer | None = None
_pix_detector: PIXFraudDetector | None = None
_coaf_reporter: COAFReporter | None = None

# In-memory CPF risk cache (replace with Redis in production)
_risk_cache: dict[str, RiskScore] = {}


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _graph_analyzer, _pix_detector, _coaf_reporter

    log.info("aml_fraud.startup", service=SERVICE_NAME, version=SERVICE_VERSION)

    # Connect Neo4j
    neo4j = get_neo4j()
    await neo4j.connect()

    # Initialise services
    _graph_analyzer = GraphAnalyzer(neo4j)
    _pix_detector = PIXFraudDetector()
    _coaf_reporter = COAFReporter()

    # Warm up ML model
    load_or_create_model()

    # Create DB tables (dev convenience; use Alembic in production)
    try:
        await create_tables()
    except Exception as exc:
        log.warning("aml_fraud.db_init_failed", error=str(exc))

    log.info("aml_fraud.ready")
    yield

    # Shutdown
    log.info("aml_fraud.shutdown")
    await neo4j.close()
    await dispose_engine()
    log.info("aml_fraud.stopped")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="AML/Fraud Detection Service",
    description=(
        "Anti-Money Laundering and fraud detection for Brazilian betting platforms. "
        "Implements Lei 9.613/1998 (SAR reporting) and Resolução BCB 44/2020."
    ),
    version=SERVICE_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ── Dependency helpers ────────────────────────────────────────────────────────


def _get_graph() -> GraphAnalyzer:
    if _graph_analyzer is None:
        raise HTTPException(status_code=503, detail="Graph analyzer not initialised")
    return _graph_analyzer


def _get_pix() -> PIXFraudDetector:
    if _pix_detector is None:
        raise HTTPException(status_code=503, detail="PIX detector not initialised")
    return _pix_detector


def _get_coaf() -> COAFReporter:
    if _coaf_reporter is None:
        raise HTTPException(status_code=503, detail="COAF reporter not initialised")
    return _coaf_reporter


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthStatus, tags=["operations"])
async def health() -> HealthStatus:
    neo4j_up = _graph_analyzer is not None and _graph_analyzer._neo4j.is_healthy
    return HealthStatus(
        status="UP",
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        neo4j_up=neo4j_up,
        model_loaded=is_model_loaded(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── POST /aml/analyze/{cpf} ───────────────────────────────────────────────────


@app.post(
    "/aml/analyze/{cpf}",
    response_model=AnalysisResult,
    status_code=status.HTTP_200_OK,
    tags=["aml"],
    summary="Real-time transaction risk analysis",
)
async def analyze_transaction(
    cpf: str = Path(..., pattern=r"^\d{11}$", description="11-digit CPF"),
    body: TransactionAnalysis = ...,
    background_tasks: BackgroundTasks = ...,
) -> AnalysisResult:
    tx = body.transaction
    log.info(
        "aml.analyze",
        tx_id=tx.transaction_id,
        cpf=cpf,
        amount=str(tx.amount),
        tx_type=tx.transaction_type,
    )

    flags = _analyze_flags(tx.amount, tx.transaction_type.value, tx.metadata)
    score = _compute_rule_score(flags, tx.amount)
    level = _risk_level(score)
    blocked = level in (RiskLevel.CRITICAL, RiskLevel.HIGH)

    _update_risk_cache(cpf, score, flags, tx.amount)

    if body.include_graph_analysis and tx.counterparty_cpf:
        graph = _get_graph()
        background_tasks.add_task(
            graph.record_transaction,
            cpf,
            tx.counterparty_cpf,
            tx.amount,
            tx.transaction_id,
        )

    return AnalysisResult(
        transaction_id=tx.transaction_id,
        cpf=cpf,
        risk_score=score,
        risk_level=level,
        flags=flags,
        blocked=blocked,
        requires_review=score >= 0.5,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── GET /aml/risk/{cpf} ───────────────────────────────────────────────────────


@app.get(
    "/aml/risk/{cpf}",
    response_model=RiskScore,
    tags=["aml"],
    summary="CPF aggregated risk score",
)
async def get_risk_score(
    cpf: str = Path(..., pattern=r"^\d{11}$"),
) -> RiskScore:
    cached = _risk_cache.get(cpf)
    if cached:
        return cached
    return RiskScore(
        cpf=cpf,
        score=0.0,
        level=RiskLevel.LOW,
        flags=[],
        last_updated=datetime.now(timezone.utc).isoformat(),
        transaction_count=0,
        total_volume=Decimal("0"),
        account_age_days=0,
    )


# ── POST /aml/report/coaf ─────────────────────────────────────────────────────


@app.post(
    "/aml/report/coaf",
    response_model=COAFReport,
    status_code=status.HTTP_201_CREATED,
    tags=["coaf"],
    summary="Generate COAF suspicious activity report (Lei 9.613/1998)",
)
async def generate_coaf_report(body: COAFReportRequest) -> COAFReport:
    reporter = _get_coaf()
    log.info(
        "aml.coaf_report_requested",
        cpf=body.cpf,
        reason=body.report_reason,
        urgency=body.urgency,
    )
    return await reporter.generate_report(body)


# ── GET /aml/graph/{cpf} ─────────────────────────────────────────────────────


@app.get(
    "/aml/graph/{cpf}",
    response_model=GraphRelationship,
    tags=["graph"],
    summary="CPF relationship graph (2-hop Neo4j traversal)",
)
async def get_cpf_graph(
    cpf: str = Path(..., pattern=r"^\d{11}$"),
) -> GraphRelationship:
    graph = _get_graph()
    try:
        return await graph.build_graph(cpf)
    except Exception as exc:
        log.error("aml.graph_failed", cpf=cpf, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Graph analysis unavailable: {exc}",
        ) from exc


# ── POST /aml/pix/fraud-check ─────────────────────────────────────────────────


@app.post(
    "/aml/pix/fraud-check",
    response_model=PIXFraudResult,
    tags=["pix"],
    summary="PIX-specific fraud pattern detection",
)
async def pix_fraud_check(body: PIXFraudCheckRequest) -> PIXFraudResult:
    detector = _get_pix()
    log.info(
        "aml.pix_check",
        sender=body.sender_cpf,
        receiver=body.receiver_cpf,
        pix_key=body.pix_key,
        amount=str(body.amount),
    )
    result = detector.check(body)
    return result


# ── POST /aml/score ───────────────────────────────────────────────────────────


@app.post(
    "/aml/score",
    response_model=ScoringResult,
    tags=["ml"],
    summary="ML fraud probability score for a single transaction",
)
async def score_transaction(features: TransactionFeatures) -> ScoringResult:
    try:
        return score_one(features)
    except Exception as exc:
        log.exception("aml.score_failed", tx_id=features.transaction_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── POST /aml/score/batch ─────────────────────────────────────────────────────


@app.post(
    "/aml/score/batch",
    response_model=BatchScoringResult,
    tags=["ml"],
    summary="Batch ML scoring (≤ 500 transactions)",
)
async def score_batch_endpoint(body: BatchScoringRequest) -> BatchScoringResult:
    try:
        return score_batch(body)
    except Exception as exc:
        log.exception("aml.batch_score_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Internal analysis helpers ─────────────────────────────────────────────────


def _analyze_flags(
    amount: Decimal,
    transaction_type: str,
    metadata: dict[str, str],
) -> list[str]:
    """Rule-based flag extraction aligned with Resolução BCB 44/2020."""
    flags: list[str] = []

    if amount > _THRESHOLD_WITHDRAWAL_BRL and transaction_type == "WITHDRAWAL":
        flags.append("THRESHOLD_WITHDRAWAL")

    if amount > _THRESHOLD_LARGE_BRL:
        flags.append("LARGE_TRANSACTION")

    if transaction_type == "DEPOSIT" and metadata.get("source") == "UNKNOWN":
        flags.append("UNKNOWN_SOURCE")

    country = metadata.get("country", "")
    if country in _SANCTIONED_COUNTRIES:
        flags.append("SANCTIONED_COUNTRY")

    # Round-amount structuring indicator
    if (
        amount > _THRESHOLD_ROUND_AMOUNT_MIN
        and amount % Decimal("1000") == Decimal("0")
    ):
        flags.append("ROUND_AMOUNT")

    return flags


def _compute_rule_score(flags: list[str], amount: Decimal) -> float:
    """Convert flags + amount into a 0–1 risk score."""
    flag_score = min(0.8, len(flags) * 0.2)
    if amount > _THRESHOLD_VERY_LARGE_BRL:
        amount_score = 0.2
    elif amount > _THRESHOLD_LARGE_BRL:
        amount_score = 0.1
    elif amount > _THRESHOLD_WITHDRAWAL_BRL:
        amount_score = 0.05
    else:
        amount_score = 0.0
    return min(1.0, flag_score + amount_score)


def _risk_level(score: float) -> RiskLevel:
    if score >= 0.8:
        return RiskLevel.CRITICAL
    if score >= 0.6:
        return RiskLevel.HIGH
    if score >= 0.3:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _update_risk_cache(
    cpf: str, score: float, flags: list[str], amount: Decimal
) -> None:
    """Exponential smoothing update of the in-memory risk cache."""
    existing = _risk_cache.get(cpf)
    if existing is None:
        existing = RiskScore(
            cpf=cpf,
            score=0.0,
            level=RiskLevel.LOW,
            flags=[],
            last_updated=datetime.now(timezone.utc).isoformat(),
            transaction_count=0,
            total_volume=Decimal("0"),
            account_age_days=0,
        )

    # Exponential smoothing: new score weighted towards worst-case
    new_score = max(existing.score * 0.9 + score * 0.1, score)
    merged_flags = list(dict.fromkeys(existing.flags + flags))[:20]

    _risk_cache[cpf] = RiskScore(
        cpf=cpf,
        score=round(new_score, 6),
        level=_risk_level(new_score),
        flags=merged_flags,
        last_updated=datetime.now(timezone.utc).isoformat(),
        transaction_count=existing.transaction_count + 1,
        total_volume=existing.total_volume + amount,
        account_age_days=existing.account_age_days,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        workers=2,
        log_level="info",
        access_log=True,
    )
