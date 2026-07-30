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
AML/Fraud Detection Service — ML Fraud Scorer
=============================================
GradientBoosting-based fraud probability scorer integrated into the main service.

10 engineered features:
  1. amount_log                — log1p of transaction amount
  2. transaction_type_encoded  — ordinal encoding of transaction type
  3. hour_of_day               — hour (0-23) of the transaction
  4. day_of_week               — day (0=Mon, 6=Sun)
  5. transaction_count_24h     — volume of transactions in past 24 h
  6. total_volume_24h_log      — log1p of BRL volume in past 24 h
  7. account_age_days_log      — log1p of account age in days
  8. is_new_device             — 1 if device fingerprint is new for CPF
  9. is_new_pix_key            — 1 if PIX key was registered < 24 h ago
 10. counterparty_risk_score   — risk score (0–1) of the counterparty CPF

In production, replace the synthetic bootstrap model with a model trained on
labelled historical transaction data and persist it with joblib.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import structlog

from models import (
    BatchScoringRequest,
    BatchScoringResult,
    ScoringResult,
    TransactionFeatures,
)

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

FEATURE_NAMES: list[str] = [
    "amount_log",
    "transaction_type_encoded",
    "hour_of_day",
    "day_of_week",
    "transaction_count_24h",
    "total_volume_24h_log",
    "account_age_days_log",
    "is_new_device",
    "is_new_pix_key",
    "counterparty_risk_score",
]

_TYPE_ENCODING: dict[str, int] = {
    "DEPOSIT": 0,
    "WITHDRAWAL": 1,
    "BET": 2,
    "PIX": 3,
    "BONUS": 4,
}

MODEL_VERSION: str = "1.0.0"
MODEL_PATH: str = os.getenv("MODEL_PATH", "/app/models/fraud_model.joblib")

_model: Any = None  # lazy-loaded singleton


# ── Model loading ─────────────────────────────────────────────────────────────


def load_or_create_model() -> Any:
    """
    Load a trained model from MODEL_PATH, or create a bootstrapped GBC
    trained on synthetic data for demo/development purposes.
    """
    global _model
    if _model is not None:
        return _model

    if os.path.exists(MODEL_PATH):
        import joblib

        log.info("fraud_scorer.model_loaded", path=MODEL_PATH)
        _model = joblib.load(MODEL_PATH)
    else:
        log.warning(
            "fraud_scorer.model_not_found",
            path=MODEL_PATH,
            action="creating_bootstrap_model",
        )
        from sklearn.ensemble import GradientBoostingClassifier

        clf = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            random_state=42,
        )
        rng = np.random.default_rng(42)
        n_samples = 1000
        X = rng.random((n_samples, len(FEATURE_NAMES)))
        # Synthetic labels: high amount + new device + off-hours → fraud
        y = (
            (X[:, 0] > 0.85)  # high amount
            | (X[:, 7] > 0.5) & (X[:, 0] > 0.6)  # new device + high amount
        ).astype(int)
        clf.fit(X, y)
        _model = clf
        log.info("fraud_scorer.bootstrap_model_ready", n_samples=n_samples)

    return _model


def is_model_loaded() -> bool:
    return _model is not None or os.path.exists(MODEL_PATH)


# ── Feature engineering ───────────────────────────────────────────────────────


def extract_features(f: TransactionFeatures) -> np.ndarray:
    """Transform a TransactionFeatures instance into the 10-feature numpy vector."""
    return np.array(
        [
            np.log1p(f.amount),
            _TYPE_ENCODING.get(f.transaction_type, 0),
            f.hour_of_day,
            f.day_of_week,
            f.transaction_count_24h,
            np.log1p(f.total_volume_24h),
            np.log1p(f.account_age_days),
            int(f.is_new_device),
            int(f.is_new_pix_key),
            f.counterparty_risk_score,
        ],
        dtype=float,
    )


# ── Scoring logic ─────────────────────────────────────────────────────────────


def risk_level_from_prob(prob: float) -> str:
    if prob >= 0.8:
        return "CRITICAL"
    if prob >= 0.6:
        return "HIGH"
    if prob >= 0.3:
        return "MEDIUM"
    return "LOW"


def score_one(features: TransactionFeatures) -> ScoringResult:
    """Score a single transaction; returns a ScoringResult."""
    t0 = time.monotonic()
    model = load_or_create_model()
    X = extract_features(features).reshape(1, -1)
    prob = float(model.predict_proba(X)[0, 1])

    importances: dict[str, float] = {}
    if hasattr(model, "feature_importances_"):
        importances = {
            name: round(float(imp), 6)
            for name, imp in zip(FEATURE_NAMES, model.feature_importances_)
        }

    latency = (time.monotonic() - t0) * 1000
    level = risk_level_from_prob(prob)

    log.info(
        "fraud_scorer.scored",
        tx_id=features.transaction_id,
        cpf=features.cpf,
        prob=round(prob, 4),
        level=level,
        latency_ms=round(latency, 2),
    )

    return ScoringResult(
        transaction_id=features.transaction_id,
        cpf=features.cpf,
        fraud_probability=round(prob, 6),
        risk_level=level,
        feature_importance=importances,
        model_version=MODEL_VERSION,
        scored_at=datetime.now(timezone.utc).isoformat(),
        latency_ms=round(latency, 3),
    )


def score_batch(requests: BatchScoringRequest) -> BatchScoringResult:
    """Score a batch of transactions."""
    results = [score_one(f) for f in requests.transactions]
    high_risk = sum(1 for r in results if r.risk_level in ("HIGH", "CRITICAL"))
    return BatchScoringResult(
        results=results,
        total=len(results),
        high_risk_count=high_risk,
        scored_at=datetime.now(timezone.utc).isoformat(),
    )
