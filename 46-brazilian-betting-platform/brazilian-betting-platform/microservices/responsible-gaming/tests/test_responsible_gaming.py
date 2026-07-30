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
Responsible Gaming Service — Test Suite
========================================
25+ pytest tests covering:
  - LimitEngine: set, consume, exceed, cooling-off, reset
  - RiskScorer: signal scoring, level thresholds, combined pipeline
  - NationalRegistryClient: check, register, revoke
  - Model validation: LimitSetRequest, SelfExclusionRequest
  - FastAPI endpoints: health, limits, self-exclusion, alerts, report
  - Edge cases: permanent exclusion revocation, cooling-off blocking

Run with:
    pytest tests/test_responsible_gaming.py -v
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any, Dict, Optional, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# See bonus-engine/tests/test_bonus.py for the full explanation.
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
    ("limit_engine", "limit_engine.py"),
    ("risk_scorer", "risk_scorer.py"),
    ("national_registry", "national_registry.py"),
]

for _mod_name, _file_name in _THIS_SERVICE_MODULES:
    _load_local_module(_mod_name, _file_name)


@pytest.fixture(autouse=True, scope="module")
def _pin_local_modules():
    """Re-install this service's local modules before any other
    module-scoped fixture runs. Must be module-scoped because any
    `client` fixture using `patch("main.XYZ")` is module-scoped.
    """
    for _mod_name, _file_name in _THIS_SERVICE_MODULES:
        _load_local_module(_mod_name, _file_name)
    _load_local_module("main", "main.py")
    yield

from limit_engine import LimitEngine, LimitExceededError, LimitIncreaseBlockedError, _RedisStub
from risk_scorer import (
    PlayerSignals,
    RiskScorer,
    _score_alert_density,
    _score_deposit_velocity,
    _score_loss_chasing,
    _score_night_play,
    _score_self_exclusion_history,
    _score_session_duration,
    score_to_level,
)
from national_registry import (
    NationalRegistryClient,
    RevocationBlockedError,
)
from models import (
    LimitPeriod,
    LimitSetRequest,
    LimitType,
    SelfExclusionRequest,
    SelfExclusionType,
    RiskLevel,
    AlertType,
    BehavioralAlertRequest,
)


# ─────────────────────────────────────────────────────────────
# LimitEngine tests
# ─────────────────────────────────────────────────────────────


def _make_engine() -> LimitEngine:
    return LimitEngine(redis=_RedisStub())


class TestLimitEngine:
    @pytest.mark.asyncio
    async def test_set_new_limit_returns_immediate(self) -> None:
        engine = _make_engine()
        cpf = "a" * 64
        immediate, cooling = await engine.set_limit(cpf, "deposit", "daily", 1000.0)
        assert immediate is True
        assert cooling is None

    @pytest.mark.asyncio
    async def test_decrease_limit_is_immediate(self) -> None:
        engine = _make_engine()
        cpf = "b" * 64
        await engine.set_limit(cpf, "deposit", "daily", 1000.0)
        immediate, cooling = await engine.set_limit(cpf, "deposit", "daily", 500.0)
        assert immediate is True
        assert cooling is None

    @pytest.mark.asyncio
    async def test_increase_limit_returns_cooling_off(self) -> None:
        engine = _make_engine()
        cpf = "c" * 64
        await engine.set_limit(cpf, "deposit", "daily", 500.0)
        immediate, cooling = await engine.set_limit(cpf, "deposit", "daily", 1000.0)
        assert immediate is False
        assert cooling is not None
        assert cooling > datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_consume_within_limit_allowed(self) -> None:
        engine = _make_engine()
        cpf = "d" * 64
        await engine.set_limit(cpf, "deposit", "daily", 1000.0)
        result = await engine.check_and_consume(cpf, "deposit", "daily", 300.0)
        assert result["allowed"] is True
        assert result["used"] == pytest.approx(300.0)
        assert result["remaining"] == pytest.approx(700.0)

    @pytest.mark.asyncio
    async def test_consume_exactly_at_limit_allowed(self) -> None:
        engine = _make_engine()
        cpf = "e" * 64
        await engine.set_limit(cpf, "deposit", "daily", 1000.0)
        result = await engine.check_and_consume(cpf, "deposit", "daily", 1000.0)
        assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_consume_over_limit_raises(self) -> None:
        engine = _make_engine()
        cpf = "f" * 64
        await engine.set_limit(cpf, "deposit", "daily", 500.0)
        await engine.check_and_consume(cpf, "deposit", "daily", 400.0)
        with pytest.raises(LimitExceededError) as exc_info:
            await engine.check_and_consume(cpf, "deposit", "daily", 200.0)
        assert exc_info.value.limit == 500.0

    @pytest.mark.asyncio
    async def test_no_limit_configured_allows_any_amount(self) -> None:
        engine = _make_engine()
        cpf = "g" * 64
        result = await engine.check_and_consume(cpf, "deposit", "daily", 99999.0)
        assert result["allowed"] is True
        assert result["limit"] is None

    @pytest.mark.asyncio
    async def test_warning_threshold_triggered(self) -> None:
        engine = _make_engine()
        cpf = "h" * 64
        await engine.set_limit(cpf, "deposit", "daily", 1000.0)
        result = await engine.check_and_consume(cpf, "deposit", "daily", 850.0)
        assert result["warning"] is True

    @pytest.mark.asyncio
    async def test_reset_usage_clears_counter(self) -> None:
        engine = _make_engine()
        cpf = "i" * 64
        await engine.set_limit(cpf, "deposit", "daily", 1000.0)
        await engine.check_and_consume(cpf, "deposit", "daily", 900.0)
        await engine.reset_usage(cpf, "deposit", "daily")
        usage = await engine.get_usage(cpf, "deposit", "daily")
        assert usage == 0.0

    @pytest.mark.asyncio
    async def test_multiple_limit_types_independent(self) -> None:
        engine = _make_engine()
        cpf = "j" * 64
        await engine.set_limit(cpf, "deposit", "daily", 500.0)
        await engine.set_limit(cpf, "loss", "daily", 200.0)
        await engine.check_and_consume(cpf, "deposit", "daily", 500.0)
        # Loss limit is separate — should not be exhausted
        result = await engine.check_and_consume(cpf, "loss", "daily", 100.0)
        assert result["allowed"] is True


# ─────────────────────────────────────────────────────────────
# RiskScorer tests
# ─────────────────────────────────────────────────────────────


class TestRiskScorer:
    def test_clean_signals_low_risk(self) -> None:
        scorer = RiskScorer()
        signals = PlayerSignals(cpf_hash="a" * 64)
        result = scorer.compute(signals)
        assert result.risk_level == "low"
        assert result.overall_score < 0.31

    def test_high_deposit_velocity_increases_score(self) -> None:
        scorer = RiskScorer()
        signals = PlayerSignals(cpf_hash="b" * 64, deposits_last_24h=10)
        result = scorer.compute(signals)
        assert result.overall_score > 0.15

    def test_prior_self_exclusions_critical(self) -> None:
        scorer = RiskScorer()
        signals = PlayerSignals(cpf_hash="c" * 64, prior_self_exclusions=2)
        result = scorer.compute(signals)
        assert result.overall_score > 0.10  # weighted avg; verify signal is high
        assert result.components["self_exclusion_hist"] > 0.8

    def test_loss_chasing_raises_score(self) -> None:
        scorer = RiskScorer()
        signals = PlayerSignals(
            cpf_hash="d" * 64, loss_streak_current=5, bets_after_loss=3
        )
        result = scorer.compute(signals)
        assert result.components["loss_chasing"] == 1.0

    def test_night_play_ratio_flagged(self) -> None:
        score, msg = _score_night_play(
            PlayerSignals(
                cpf_hash="e" * 64,
                night_sessions_last_30d=20,
                total_sessions_last_30d=30,
            )
        )
        assert score >= 0.50
        assert msg is not None

    def test_score_to_level_boundaries(self) -> None:
        assert score_to_level(0.00) == "low"
        assert score_to_level(0.30) == "low"
        assert score_to_level(0.31) == "medium"
        assert score_to_level(0.60) == "medium"
        assert score_to_level(0.61) == "high"
        assert score_to_level(0.80) == "high"
        assert score_to_level(0.81) == "critical"
        assert score_to_level(1.00) == "critical"

    def test_overall_score_clamped_to_one(self) -> None:
        scorer = RiskScorer()
        signals = PlayerSignals(
            cpf_hash="f" * 64,
            deposits_last_24h=10,
            loss_streak_current=5,
            bets_after_loss=3,
            prior_self_exclusions=2,
            alerts_last_7d=10,
            night_sessions_last_30d=30,
            total_sessions_last_30d=30,
        )
        result = scorer.compute(signals)
        assert result.overall_score <= 1.0

    def test_signals_list_populated_on_risk(self) -> None:
        scorer = RiskScorer()
        signals = PlayerSignals(cpf_hash="g" * 64, deposits_last_24h=10)
        result = scorer.compute(signals)
        assert len(result.signals) > 0

    @pytest.mark.asyncio
    async def test_async_score_method(self) -> None:
        scorer = RiskScorer()
        result = await scorer.score("h" * 64)
        assert 0.0 <= result.overall_score <= 1.0
        assert result.computed_at is not None

    def test_alert_density_score(self) -> None:
        score, msg = _score_alert_density(
            PlayerSignals(cpf_hash="i" * 64, alerts_last_7d=10)
        )
        assert score == 0.90

    def test_session_duration_over_limit(self) -> None:
        score, msg = _score_session_duration(
            PlayerSignals(
                cpf_hash="j" * 64,
                session_minutes_today=240,
                configured_session_limit_minutes=60,
            )
        )
        assert score >= 0.60


# ─────────────────────────────────────────────────────────────
# NationalRegistryClient tests
# ─────────────────────────────────────────────────────────────


class TestNationalRegistryClient:
    @pytest.mark.asyncio
    async def test_clean_cpf_not_excluded(self) -> None:
        client = NationalRegistryClient()
        result = await client.check("aa" + "0" * 62)
        assert result.is_excluded is False

    @pytest.mark.asyncio
    async def test_00_prefix_permanently_excluded(self) -> None:
        client = NationalRegistryClient()
        result = await client.check("00" + "0" * 62)
        assert result.is_excluded is True
        assert result.exclusion_type == "permanent"

    @pytest.mark.asyncio
    async def test_0a_prefix_temporarily_excluded(self) -> None:
        client = NationalRegistryClient()
        result = await client.check("0a" + "0" * 62)
        assert result.is_excluded is True
        assert result.exclusion_type == "temporary"
        assert result.ends_at is not None

    @pytest.mark.asyncio
    async def test_register_new_exclusion(self) -> None:
        client = NationalRegistryClient()
        cpf_hash = "dd" + "0" * 62
        reg = await client.register(cpf_hash, "temporary", duration_days=30)
        assert reg.accepted is True
        assert reg.exclusion_type == "temporary"

        check = await client.check(cpf_hash)
        assert check.is_excluded is True

    @pytest.mark.asyncio
    async def test_revoke_temporary_after_expiry(self) -> None:
        client = NationalRegistryClient()
        cpf_hash = "ee" + "0" * 62
        # Register with 1 day
        await client.register(cpf_hash, "temporary", duration_days=1)
        # Manually expire the record
        client._store[cpf_hash].ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        revoked = await client.revoke(cpf_hash)
        assert revoked is True

    @pytest.mark.asyncio
    async def test_revoke_permanent_raises(self) -> None:
        client = NationalRegistryClient()
        cpf_hash = "00" + "f" * 62
        await client.check(cpf_hash)  # populate store
        with pytest.raises(RevocationBlockedError) as exc_info:
            await client.revoke(cpf_hash)
        assert "Permanent" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_revoke_during_cooling_off_raises(self) -> None:
        client = NationalRegistryClient()
        cpf_hash = "cc" + "1" * 62
        await client.register(cpf_hash, "temporary", duration_days=30)
        with pytest.raises(RevocationBlockedError):
            await client.revoke(cpf_hash)

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_returns_true(self) -> None:
        client = NationalRegistryClient()
        result = await client.revoke("zz" + "0" * 62)
        assert result is True


# ─────────────────────────────────────────────────────────────
# Model validation tests
# ─────────────────────────────────────────────────────────────


class TestModelValidation:
    def test_limit_set_request_valid(self) -> None:
        req = LimitSetRequest(
            limit_type=LimitType.DEPOSIT,
            period=LimitPeriod.DAILY,
            amount=500.0,
        )
        assert req.amount == 500.0

    def test_limit_set_request_negative_amount_raises(self) -> None:
        with pytest.raises(Exception):
            LimitSetRequest(
                limit_type=LimitType.DEPOSIT,
                period=LimitPeriod.DAILY,
                amount=-100.0,
            )

    def test_temporary_exclusion_without_duration_raises(self) -> None:
        with pytest.raises(Exception):
            SelfExclusionRequest(
                exclusion_type=SelfExclusionType.TEMPORARY,
                duration_days=None,
            )

    def test_permanent_exclusion_with_duration_raises(self) -> None:
        with pytest.raises(Exception):
            SelfExclusionRequest(
                exclusion_type=SelfExclusionType.PERMANENT,
                duration_days=30,
            )

    def test_valid_temporary_exclusion(self) -> None:
        req = SelfExclusionRequest(
            exclusion_type=SelfExclusionType.TEMPORARY,
            duration_days=90,
        )
        assert req.duration_days == 90

    def test_valid_permanent_exclusion(self) -> None:
        req = SelfExclusionRequest(exclusion_type=SelfExclusionType.PERMANENT)
        assert req.duration_days is None

    def test_behavioral_alert_request_valid(self) -> None:
        req = BehavioralAlertRequest(
            alert_type=AlertType.LOSS_CHASING,
            context={"loss_streak": 5},
            triggered_by="risk_engine_v1",
            severity=RiskLevel.HIGH,
        )
        assert req.severity == RiskLevel.HIGH


# ─────────────────────────────────────────────────────────────
# FastAPI endpoint tests
# ─────────────────────────────────────────────────────────────


class TestRGEndpoints:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self) -> None:
        import httpx
        from main import app

        with patch("main.create_tables", new_callable=AsyncMock), \
             patch("main.dispose_engine", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "responsible-gaming"

    @pytest.mark.asyncio
    async def test_check_exclusion_clean_cpf(self) -> None:
        import httpx
        from main import app

        with patch("main.create_tables", new_callable=AsyncMock), \
             patch("main.dispose_engine", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/self-exclusion/check/52998224725")

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_excluded"] is False

    @pytest.mark.asyncio
    async def test_set_limit_invalid_amount_returns_422(self) -> None:
        import httpx
        from main import app

        with patch("main.create_tables", new_callable=AsyncMock), \
             patch("main.dispose_engine", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {
                    "limit_type": "deposit",
                    "period": "daily",
                    "amount": -50.0,
                }
                resp = await client.post("/limits/52998224725", json=payload)

        assert resp.status_code == 422
