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
main.py – FastAPI service for the risk-matrix / risk-scoring service.

Exposes:
  POST /score          – trigger scoring for a player event
  GET  /profile/{uid}  – retrieve the current PlayerRiskProfile
  GET  /matrices       – list all configured risk matrices
  GET  /health         – liveness check
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .models import (
    EventType,
    MatrixLevel,
    MatrixScoreType,
    PlayerRiskProfile,
    ScoreTriggerRequest,
    ScoreTriggerResponse,
    UserMatrixScore,
)
from .matrix_engine import (
    FlagsService,
    MatrixScoreRepository,
    UserEventsService,
    score_event,
)
from .player_profiler import (
    PlayerProfiler,
    ProfileStore,
    classify_overall_risk,
)
from .scorer import MessageSender, ScoreRepository
from .service_wiring import env_flag, is_test_env, load_service

import logging

import structlog

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO"))
    )
)
log = structlog.get_logger(__name__)

app = FastAPI(
    title="Risk Scoring Service",
    description=(
        "Multi-factor risk matrix scoring for player protection and AML compliance. "
        "Evaluates deposit, withdrawal, limit-change, and interaction events "
        "against configurable scoring rules per jurisdiction."
    ),
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Stub implementations (replace with real DB/service adapters in production)
# ---------------------------------------------------------------------------


class _StubUserEventsService(UserEventsService):
    def count_deposits(self, u, f, t): return 0
    def deposit_total(self, u, f, t): return 0
    def count_declined_deposits(self, u, f, t): return 0
    def count_failed_deposits(self, u, f, t): return 0
    def count_deposit_limit_changes(self, u, f, t, g): return 0
    def count_deposit_limit_increases(self, u, f, t): return 0
    def payment_options_created(self, u, f, t): return 0
    def payments_options_created_after_failed_deposit(self, u, f, t): return 0
    def depositing_days(self, u, f, t): return 0
    def count_reversed_withdrawals(self, u, f, t): return 0
    def reversed_withdrawals_total(self, u, f, t): return 0
    def count_interactions_by_types(self, u, g, types, f, t): return 0
    def count_timeouts(self, u, g, f, t): return 0


class _StubFlagsService(FlagsService):
    def user_has_flag(self, user_id, flag_id): return False


class _StubMatrixScoreRepository(MatrixScoreRepository, ScoreRepository):
    def get_scores_by_type_for_user(self, user_id, score_type): return 0
    def get_current_user_level(self, user_id, matrix_id): return None
    def add_user_score(self, score): pass


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------
# Service wiring refuses to run on stubs in production -- see
# service_wiring.py. The stub services above return 0/None/False for
# everything; a real adapter must be configured via env var, or this module
# refuses to import outside of an explicitly-allowed dev/test environment.

_ALLOW_STUBS = env_flag("RISK_SCORING_ALLOW_STUBS")
_IS_TEST_ENV = is_test_env()

_profile_store = ProfileStore()
_profiler = PlayerProfiler(
    profile_store=_profile_store,
    matrix_levels={},  # populated from matrix config in production
)
_score_types: List[MatrixScoreType] = []   # loaded from DB at startup
_user_events: UserEventsService = load_service(
    "RISK_USER_EVENTS_IMPL", UserEventsService, _StubUserEventsService,
    allow_stubs=_ALLOW_STUBS, is_test_env=_IS_TEST_ENV,
)
_flags: FlagsService = load_service(
    "RISK_FLAGS_IMPL", FlagsService, _StubFlagsService,
    allow_stubs=_ALLOW_STUBS, is_test_env=_IS_TEST_ENV,
)
_score_repo: MatrixScoreRepository = load_service(
    "RISK_SCORE_REPO_IMPL", MatrixScoreRepository, _StubMatrixScoreRepository,
    allow_stubs=_ALLOW_STUBS, is_test_env=_IS_TEST_ENV,
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> Dict:
    return {"status": "ok", "service": "risk-scoring"}


# ---------------------------------------------------------------------------
# Scoring endpoint
# ---------------------------------------------------------------------------


@app.post("/score", response_model=ScoreTriggerResponse, tags=["scoring"])
async def trigger_score(body: ScoreTriggerRequest) -> ScoreTriggerResponse:
    """
    Trigger risk matrix scoring for a specific player event.

    The engine evaluates all applicable MatrixScoreType rules for the
    given event_type and jurisdiction, persists any matched scores, and
    returns which matrices were updated.
    """
    try:
        matched_matrices = score_event(
            user_id=body.user_id,
            global_id=body.global_id,
            event_type=body.event_type.value,
            jurisdiction=body.jurisdiction,
            score_types=_score_types,
            score_repo=_score_repo,
            user_events=_user_events,
            flags=_flags,
            data=body.data,
        )
    except Exception as exc:
        log.exception("Scoring failed for user %s: %s", body.user_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    profile = _profile_store.get(body.user_id)
    return ScoreTriggerResponse(
        user_id=body.user_id,
        matrices_updated=list(set(matched_matrices)),
        profile=profile,
    )


# ---------------------------------------------------------------------------
# Profile endpoints
# ---------------------------------------------------------------------------


@app.get("/profile/{user_id}", response_model=PlayerRiskProfile, tags=["profiles"])
async def get_player_profile(user_id: int) -> PlayerRiskProfile:
    """Return the current risk profile for a player."""
    profile = _profile_store.get(user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


class ProfileSummary(BaseModel):
    user_id: int
    overall_risk: str
    scores: Dict[str, int]
    levels: Dict[str, int]
    risk_flags: List[str]
    last_calculated: Optional[datetime]


@app.get("/profile/{user_id}/summary", response_model=ProfileSummary, tags=["profiles"])
async def get_profile_summary(user_id: int) -> ProfileSummary:
    """Return a concise risk summary for the compliance dashboard."""
    profile = _profile_store.get(user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ProfileSummary(
        user_id=profile.user_id,
        overall_risk=classify_overall_risk(profile),
        scores=profile.scores,
        levels=profile.levels,
        risk_flags=profile.risk_flags,
        last_calculated=profile.last_calculated,
    )


# ---------------------------------------------------------------------------
# Matrix configuration endpoint
# ---------------------------------------------------------------------------


@app.get("/matrices", tags=["configuration"])
async def list_matrices() -> Dict[str, Any]:
    """Return a summary of the loaded matrix score type configuration."""
    by_matrix: Dict[str, List[Dict]] = {}
    for st in _score_types:
        by_matrix.setdefault(st.score_matrix_id, []).append({
            "id": st.id,
            "label": st.label,
            "calculate_on": st.calculate_on,
            "condition": st.condition,
            "score_value": st.score_value,
            "jurisdiction": st.jurisdiction,
        })
    return {"matrices": by_matrix, "total_rules": len(_score_types)}
