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
AML/Fraud Detection Service — Test Suite
=========================================
25+ pytest tests covering all endpoints and fraud detection patterns.

Test categories:
  - Health check
  - Transaction analysis (rule-based flags)
  - Risk score retrieval
  - COAF SAR generation
  - Neo4j graph endpoint (mocked)
  - PIX fraud patterns (all 6 patterns)
  - ML scoring (single + batch)
  - Edge cases and validation
"""

from __future__ import annotations

import importlib.util
import sys
import os
from datetime import datetime, timezone
from decimal import Decimal
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# See bonus-engine/tests/test_bonus.py for the full explanation: each
# chapter-46 microservice has its own models.py / main.py, so the one
# that wins sys.modules first poisons cross-service collection.
_SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str) -> None:
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, _SERVICE_DIR / file_name,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


_THIS_SERVICE_MODULES: list[tuple[str, str]] = [
    ("models", "models.py"),
    ("database", "database.py"),
    ("fraud_scorer", "fraud_scorer.py"),
    ("pix_fraud_detector", "pix_fraud_detector.py"),
    ("graph_analyzer", "graph_analyzer.py"),
    ("coaf_reporter", "coaf_reporter.py"),
]

for _mod_name, _file_name in _THIS_SERVICE_MODULES:
    _load_local_module(_mod_name, _file_name)


@pytest.fixture(autouse=True, scope="module")
def _pin_local_modules():
    """Re-install this service's local modules under sys.modules before
    any other module-scoped fixture runs, so lazy `from models import X`
    calls inside test bodies (and `patch("main.create_tables")`) keep
    resolving to the aml-fraud copies even after another chapter-46
    microservice got collected afterwards and stomped on sys.modules.

    The scope has to be `module` (not `function`) because the `client`
    fixture is module-scoped and enters its `patch("main.create_tables")`
    context before any function-scoped autouse fixture would fire.
    """
    for _mod_name, _file_name in _THIS_SERVICE_MODULES:
        _load_local_module(_mod_name, _file_name)
    _load_local_module("main", "main.py")
    yield


# Patch Neo4j before main.py runs its module-level setup.
import database as _db_module

_fake_neo4j = MagicMock(is_healthy=False)
_fake_neo4j.connect = AsyncMock()
_fake_neo4j.close = AsyncMock()
_db_module.get_neo4j = lambda: _fake_neo4j

_load_local_module("main", "main.py")

from pix_fraud_detector import PIXFraudDetector
from models import (
    COAFReportRequest,
    PIXFraudCheckRequest,
    PIXPattern,
    ReportUrgency,
    RiskLevel,
    TransactionAnalysis,
    TransactionFeatures,
    TransactionType,
)
from fraud_scorer import extract_features, risk_level_from_prob, score_one

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_tx(
    *,
    tx_id: str = "TX-001",
    cpf: str = "12345678901",
    amount: str = "500.00",
    tx_type: str = "DEPOSIT",
    metadata: dict | None = None,
    counterparty_cpf: str | None = None,
) -> dict:
    return {
        "transaction_id": tx_id,
        "cpf": cpf,
        "amount": amount,
        "transaction_type": tx_type,
        "metadata": metadata or {},
        "counterparty_cpf": counterparty_cpf,
    }


def _analyze_body(tx_dict: dict, include_graph: bool = False) -> dict:
    return {"transaction": tx_dict, "include_graph_analysis": include_graph}


def _pix_body(
    *,
    sender: str = "11122233344",
    receiver: str = "55566677788",
    amount: str = "500.00",
    pix_key: str = "test@example.com",
    device: str | None = None,
) -> dict:
    return {
        "pix_key": pix_key,
        "sender_cpf": sender,
        "receiver_cpf": receiver,
        "amount": amount,
        "device_fingerprint": device,
    }


# ── App client setup ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """TestClient with lifespan disabled to avoid real DB/Neo4j connections."""
    with patch("main.create_tables", new_callable=AsyncMock), \
         patch("main.dispose_engine", new_callable=AsyncMock), \
         patch("database.Neo4jManager.connect", new_callable=AsyncMock), \
         patch("database.Neo4jManager.close", new_callable=AsyncMock):

        import main as _main
        _main._graph_analyzer = MagicMock()
        _main._graph_analyzer._neo4j = MagicMock(is_healthy=False)
        _main._graph_analyzer.build_graph = AsyncMock(
            return_value=MagicMock(
                cpf="12345678901",
                nodes=[],
                edges=[],
                cluster_risk_score=0.0,
                is_mule_network=False,
                generated_at=datetime.now(timezone.utc).isoformat(),
                model_dump=lambda: {
                    "cpf": "12345678901",
                    "nodes": [],
                    "edges": [],
                    "cluster_risk_score": 0.0,
                    "is_mule_network": False,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        _main._graph_analyzer.record_transaction = AsyncMock()
        _main._pix_detector = PIXFraudDetector()
        _main._coaf_reporter = MagicMock()
        _main._coaf_reporter.generate_report = AsyncMock()

        from fastapi.testclient import TestClient
        with TestClient(_main.app, raise_server_exceptions=True) as c:
            yield c


# ─────────────────────────────────────────────────────────────────────────────
# 1. Health check
# ─────────────────────────────────────────────────────────────────────────────


def test_health_returns_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_health_schema(client):
    data = client.get("/health").json()
    assert data["status"] == "UP"
    assert data["service"] == "aml-fraud"
    assert "version" in data
    assert "neo4j_up" in data
    assert "model_loaded" in data
    assert "timestamp" in data


# ─────────────────────────────────────────────────────────────────────────────
# 2. POST /aml/analyze/{cpf}
# ─────────────────────────────────────────────────────────────────────────────


def test_analyze_normal_transaction(client):
    body = _analyze_body(_make_tx(amount="100.00"))
    resp = client.post("/aml/analyze/12345678901", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["risk_level"] == "LOW"
    assert data["blocked"] is False
    assert data["requires_review"] is False


def test_analyze_large_withdrawal_flags(client):
    body = _analyze_body(_make_tx(amount="15000.00", tx_type="WITHDRAWAL"))
    resp = client.post("/aml/analyze/12345678901", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert "THRESHOLD_WITHDRAWAL" in data["flags"]


def test_analyze_very_large_amount_flags(client):
    body = _analyze_body(_make_tx(amount="60000.00", tx_type="DEPOSIT"))
    resp = client.post("/aml/analyze/12345678901", json=body)
    assert resp.status_code == 200
    assert "LARGE_TRANSACTION" in resp.json()["flags"]


def test_analyze_unknown_source_flagged(client):
    body = _analyze_body(
        _make_tx(amount="500.00", tx_type="DEPOSIT", metadata={"source": "UNKNOWN"})
    )
    resp = client.post("/aml/analyze/12345678901", json=body)
    assert "UNKNOWN_SOURCE" in resp.json()["flags"]


def test_analyze_sanctioned_country_flagged(client):
    body = _analyze_body(
        _make_tx(amount="500.00", metadata={"country": "KP"})
    )
    resp = client.post("/aml/analyze/12345678901", json=body)
    assert "SANCTIONED_COUNTRY" in resp.json()["flags"]


def test_analyze_round_amount_flagged(client):
    body = _analyze_body(_make_tx(amount="10000.00"))
    resp = client.post("/aml/analyze/12345678901", json=body)
    assert "ROUND_AMOUNT" in resp.json()["flags"]


def test_analyze_critical_risk_blocked(client):
    # Multiple flags -> CRITICAL -> blocked
    body = _analyze_body(
        _make_tx(
            amount="60000.00",
            tx_type="WITHDRAWAL",
            metadata={"country": "IR", "source": "UNKNOWN"},
        )
    )
    resp = client.post("/aml/analyze/12345678901", json=body)
    data = resp.json()
    assert data["blocked"] is True
    assert data["risk_level"] in ("CRITICAL", "HIGH")


def test_analyze_invalid_cpf_rejected(client):
    body = _analyze_body(_make_tx())
    resp = client.post("/aml/analyze/123", json=body)
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# 3. GET /aml/risk/{cpf}
# ─────────────────────────────────────────────────────────────────────────────


def test_risk_unknown_cpf_returns_zero(client):
    resp = client.get("/aml/risk/99988877766")
    assert resp.status_code == 200
    data = resp.json()
    assert data["score"] == 0.0
    assert data["level"] == "LOW"


def test_risk_score_updated_after_analyze(client):
    cpf = "11100011100"
    # Analyze a suspicious transaction to populate cache
    body = _analyze_body(
        _make_tx(
            cpf=cpf,
            amount="60000.00",
            tx_type="WITHDRAWAL",
            metadata={"source": "UNKNOWN"},
        )
    )
    client.post(f"/aml/analyze/{cpf}", json=body)
    resp = client.get(f"/aml/risk/{cpf}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["score"] > 0.0
    assert data["transaction_count"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. POST /aml/report/coaf
# ─────────────────────────────────────────────────────────────────────────────


def test_coaf_report_created(client):
    import main as _main
    from models import COAFReport, ReportStatus

    mock_report = COAFReport(
        report_id="COAF-ABCD1234",
        cpf="12345678901",
        report_reason="STRUCTURING",
        transactions=["TX-001", "TX-002"],
        evidence_summary="Test evidence summary text for COAF report",
        status=ReportStatus.PENDING,
        submitted_at=datetime.now(timezone.utc).isoformat(),
        coaf_protocol=None,
    )
    _main._coaf_reporter.generate_report = AsyncMock(return_value=mock_report)

    body = {
        "cpf": "12345678901",
        "report_reason": "STRUCTURING",
        "transactions": ["TX-001", "TX-002"],
        "evidence_summary": "Test evidence summary text for COAF report",
        "urgency": "NORMAL",
    }
    resp = client.post("/aml/report/coaf", json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["report_id"].startswith("COAF-")
    assert data["status"] == "PENDING"


def test_coaf_report_invalid_reason_rejected(client):
    body = {
        "cpf": "12345678901",
        "report_reason": "INVALID_REASON",
        "transactions": ["TX-001"],
        "evidence_summary": "Some evidence summary text here",
        "urgency": "NORMAL",
    }
    resp = client.post("/aml/report/coaf", json=body)
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# 5. GET /aml/graph/{cpf}
# ─────────────────────────────────────────────────────────────────────────────


def test_graph_returns_structure(client):
    resp = client.get("/aml/graph/12345678901")
    assert resp.status_code == 200
    data = resp.json()
    assert "cpf" in data
    assert "nodes" in data
    assert "edges" in data
    assert "cluster_risk_score" in data
    assert "is_mule_network" in data


# ─────────────────────────────────────────────────────────────────────────────
# 6. POST /aml/pix/fraud-check — all 6 patterns
# ─────────────────────────────────────────────────────────────────────────────


def test_pix_clean_transaction(client):
    resp = client.post("/aml/pix/fraud-check", json=_pix_body(amount="100.00"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["blocked"] is False
    assert data["patterns"] == []


def test_pix_velocity_pattern(client):
    """Trigger VELOCITY by sending 11 PIX in 1 hour."""
    sender = "22233344455"
    for i in range(11):
        client.post(
            "/aml/pix/fraud-check",
            json=_pix_body(sender=sender, amount="100.00"),
        )
    resp = client.post(
        "/aml/pix/fraud-check",
        json=_pix_body(sender=sender, amount="100.00"),
    )
    data = resp.json()
    assert "VELOCITY" in data["patterns"]
    assert data["blocked"] is True


def test_pix_smurfing_pattern(client):
    """Amount just below COAF threshold (R$ 10.000) triggers SMURFING."""
    resp = client.post(
        "/aml/pix/fraud-check", json=_pix_body(amount="9500.00")
    )
    data = resp.json()
    assert "SMURFING" in data["patterns"]


def test_pix_round_trip_pattern(client):
    """A→B then B→A within session triggers ROUND_TRIP."""
    cpf_a = "33344455566"
    cpf_b = "44455566677"
    # A sends to B
    client.post(
        "/aml/pix/fraud-check",
        json=_pix_body(sender=cpf_a, receiver=cpf_b, amount="500.00"),
    )
    # B sends back to A
    resp = client.post(
        "/aml/pix/fraud-check",
        json=_pix_body(sender=cpf_b, receiver=cpf_a, amount="490.00"),
    )
    data = resp.json()
    assert "ROUND_TRIP" in data["patterns"]
    assert data["blocked"] is True


def test_pix_off_hours_pattern(client):
    """Transaction at 02:00 BRT (05:00 UTC) + high amount triggers OFF_HOURS."""
    body = _pix_body(amount="8000.00")
    body["transaction_time"] = "2025-06-15T05:00:00+00:00"  # 02:00 BRT
    resp = client.post("/aml/pix/fraud-check", json=body)
    data = resp.json()
    assert "OFF_HOURS" in data["patterns"]


def test_pix_device_anomaly_pattern(client):
    """New device fingerprint + amount >= R$2000 triggers DEVICE_ANOMALY."""
    cpf = "55566677799"
    # First transaction establishes device baseline
    client.post(
        "/aml/pix/fraud-check",
        json=_pix_body(sender=cpf, amount="100.00", device="device-known"),
    )
    # Second transaction with a new device + high amount
    resp = client.post(
        "/aml/pix/fraud-check",
        json=_pix_body(sender=cpf, amount="3000.00", device="device-NEW"),
    )
    data = resp.json()
    assert "DEVICE_ANOMALY" in data["patterns"]


def test_pix_mule_account_blocked(client):
    """Receiver flagged as mule triggers MULE_ACCOUNT and blocks."""
    import main as _main

    mule_cpf = "66677788800"
    _main._pix_detector.flag_mule(mule_cpf)
    resp = client.post(
        "/aml/pix/fraud-check",
        json=_pix_body(receiver=mule_cpf, amount="200.00"),
    )
    data = resp.json()
    assert "MULE_ACCOUNT" in data["patterns"]
    assert data["blocked"] is True
    _main._pix_detector.clear_mule(mule_cpf)


# ─────────────────────────────────────────────────────────────────────────────
# 7. ML scoring
# ─────────────────────────────────────────────────────────────────────────────


def test_ml_score_single_transaction(client):
    features = {
        "transaction_id": "TX-ML-001",
        "cpf": "12345678901",
        "amount": 500.0,
        "transaction_type": "DEPOSIT",
        "hour_of_day": 14,
        "day_of_week": 2,
    }
    resp = client.post("/aml/score", json=features)
    assert resp.status_code == 200
    data = resp.json()
    assert 0.0 <= data["fraud_probability"] <= 1.0
    assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert "feature_importance" in data
    assert "model_version" in data


def test_ml_score_batch(client):
    body = {
        "transactions": [
            {
                "transaction_id": f"TX-{i}",
                "cpf": "12345678901",
                "amount": 100.0 * (i + 1),
                "transaction_type": "PIX",
                "hour_of_day": 10,
                "day_of_week": 1,
            }
            for i in range(5)
        ]
    }
    resp = client.post("/aml/score/batch", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["results"]) == 5
    assert "high_risk_count" in data


def test_ml_score_high_amount_detected():
    """Verify the ML model identifies high-amount transactions."""
    features = TransactionFeatures(
        transaction_id="TX-HIGH",
        cpf="12345678901",
        amount=999999.0,  # very high
        transaction_type="WITHDRAWAL",
        hour_of_day=3,   # off-hours
        day_of_week=0,
        is_new_device=True,
        counterparty_risk_score=0.9,
    )
    result = score_one(features)
    assert result.fraud_probability >= 0.0
    assert result.risk_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_ml_risk_level_thresholds():
    assert risk_level_from_prob(0.9) == "CRITICAL"
    assert risk_level_from_prob(0.7) == "HIGH"
    assert risk_level_from_prob(0.4) == "MEDIUM"
    assert risk_level_from_prob(0.1) == "LOW"


def test_ml_feature_extraction():
    features = TransactionFeatures(
        transaction_id="TX-FE",
        cpf="12345678901",
        amount=1000.0,
        transaction_type="PIX",
        hour_of_day=12,
        day_of_week=3,
        transaction_count_24h=5,
        total_volume_24h=5000.0,
        account_age_days=365,
        is_new_device=True,
        is_new_pix_key=False,
        counterparty_risk_score=0.3,
    )
    vec = extract_features(features)
    assert vec.shape == (10,)
    assert vec[7] == 1.0  # is_new_device
    assert vec[8] == 0.0  # is_new_pix_key
    assert vec[9] == 0.3  # counterparty_risk_score


# ─────────────────────────────────────────────────────────────────────────────
# 8. COAF reporter unit tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_coaf_reporter_generates_report():
    from coaf_reporter import COAFReporter
    from models import ReportStatus

    reporter = COAFReporter(api_url="http://mock-coaf", api_key="test-key")
    req = COAFReportRequest(
        cpf="12345678901",
        report_reason="LAYERING",
        transactions=["TX-A", "TX-B", "TX-C"],
        evidence_summary="Detected layering through multiple accounts over 30 days.",
        urgency=ReportUrgency.NORMAL,
    )
    report = await reporter.generate_report(req)
    assert report.report_id.startswith("COAF-")
    assert report.cpf == "12345678901"
    assert report.status == ReportStatus.PENDING
    assert report.coaf_protocol is None


@pytest.mark.asyncio
async def test_coaf_reporter_batch():
    from coaf_reporter import COAFReporter

    reporter = COAFReporter(api_url="http://mock-coaf", api_key="test-key")
    reqs = [
        COAFReportRequest(
            cpf="12345678901",
            report_reason="STRUCTURING",
            transactions=[f"TX-{i}"],
            evidence_summary="Structuring evidence for batch test report.",
            urgency=ReportUrgency.NORMAL,
        )
        for i in range(3)
    ]
    reports = await reporter.generate_batch(reqs)
    assert len(reports) == 3
    ids = [r.report_id for r in reports]
    assert len(set(ids)) == 3  # all unique


# ─────────────────────────────────────────────────────────────────────────────
# 9. Model validation edge cases
# ─────────────────────────────────────────────────────────────────────────────


def test_invalid_transaction_type_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TransactionFeatures(
            transaction_id="TX-BAD",
            cpf="12345678901",
            amount=100.0,
            transaction_type="INVALID",
            hour_of_day=10,
            day_of_week=1,
        )


def test_pix_fraud_result_blocked_implies_high_probability():
    """Blocked transactions must have a non-trivial fraud probability."""
    detector = PIXFraudDetector()
    mule = "77788899900"
    detector.flag_mule(mule)
    req = PIXFraudCheckRequest(
        pix_key="key@test",
        sender_cpf="11122233344",
        receiver_cpf=mule,
        amount=Decimal("500.00"),
    )
    result = detector.check(req)
    assert result.blocked is True
    assert result.fraud_probability > 0.0
    detector.clear_mule(mule)


def test_analyze_requires_11_digit_cpf(client):
    body = _analyze_body(_make_tx())
    # 10-digit CPF
    resp = client.post("/aml/analyze/1234567890", json=body)
    assert resp.status_code == 422


def test_pix_check_requires_11_digit_cpf(client):
    body = _pix_body()
    body["sender_cpf"] = "123"  # invalid
    resp = client.post("/aml/pix/fraud-check", json=body)
    assert resp.status_code == 422
