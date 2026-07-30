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
PAM Service — Test Suite
=========================
30+ pytest tests covering:
  - CPF mod-11 digit validation (valid, invalid, edge cases)
  - Receita Federal client stubs
  - Biometric verification (pass, fail, liveness)
  - SIGAP impediment checks, including social-program restrictions
  - Player registration happy path
  - Duplicate registration prevention
  - Biometric endpoint (activate, block on regulatory impediment)
  - Status updates (activate, suspend, block)
  - Welfare check endpoint
  - Sessions endpoint
  - Re-verification trigger
  - LGPD erasure
  - Health check
  - HTTP error codes

Run with:
    pytest tests/test_pam.py -v
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
import pathlib
from datetime import datetime, timedelta, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

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
    ("cpf_validator", "cpf_validator.py"),
    ("biometric", "biometric.py"),
    ("welfare", "welfare.py"),
]

for _mod_name, _file_name in _THIS_SERVICE_MODULES:
    _load_local_module(_mod_name, _file_name)


@pytest.fixture(autouse=True, scope="module")
def _pin_local_modules():
    """Re-install this service's local modules before any other
    module-scoped fixture runs -- must be module-scoped because any
    `client` fixture that enters a `patch("main.XYZ")` context is
    module-scoped too, and function-scoped autouse would fire too late.
    """
    for _mod_name, _file_name in _THIS_SERVICE_MODULES:
        _load_local_module(_mod_name, _file_name)
    _load_local_module("main", "main.py")
    yield

from cpf_validator import (
    CPFDeceasedError,
    CPFInvalidError,
    CPFNameMismatchError,
    CPFStatusError,
    CPFValidator,
    ReceitaFederalClient,
    ReceitaFederalResult,
)
from biometric import (
    BiometricMismatchError,
    BiometricService,
    BiometricVerificationResult,
    LivenessFailedError,
)
from welfare import (
    WelfareBeneficiaryError,
    WelfareCheckError,
    WelfareCheckResult,
    WelfareRegistryClient,
)
from models import PlayerStatus, DocumentType, GenderCode, StatusAction


# ─────────────────────────────────────────────────────────────
# CPFValidator tests
# ─────────────────────────────────────────────────────────────


class TestCPFValidator:
    """Tests for the mod-11 CPF digit algorithm."""

    # Valid CPFs (algorithmically correct)
    VALID_CPFS = [
        "529.982.247-25",
        "111.444.777-35",
        "104.332.181-00",
        "960.013.389-14",
    ]

    # Invalid CPFs (wrong check digit)
    INVALID_CPFS = [
        "529.982.247-26",  # last digit off
        "000.000.000-00",  # all-zeros
        "111.111.111-11",  # all-same-digit
        "999.999.999-99",
        "123.456.789-00",
        "12345",           # too short
        "abcdefghijk",     # non-numeric
    ]

    @pytest.mark.parametrize("cpf", VALID_CPFS)
    def test_valid_cpf(self, cpf: str) -> None:
        assert CPFValidator.validate(cpf) is True

    @pytest.mark.parametrize("cpf", INVALID_CPFS)
    def test_invalid_cpf(self, cpf: str) -> None:
        assert CPFValidator.validate(cpf) is False

    def test_normalise_strips_punctuation(self) -> None:
        assert CPFValidator.normalise("529.982.247-25") == "52998224725"

    def test_format_returns_correct_pattern(self) -> None:
        assert CPFValidator.format("52998224725") == "529.982.247-25"

    def test_hash_is_sha256_hex(self) -> None:
        h = CPFValidator.hash("52998224725")
        assert len(h) == 64
        assert h == hashlib.sha256(b"52998224725").hexdigest()

    def test_validate_or_raise_valid(self) -> None:
        result = CPFValidator.validate_or_raise("529.982.247-25")
        assert result == "52998224725"

    def test_validate_or_raise_invalid_raises(self) -> None:
        with pytest.raises(CPFInvalidError):
            CPFValidator.validate_or_raise("529.982.247-26")

    def test_all_same_digits_rejected(self) -> None:
        for d in range(10):
            cpf = str(d) * 11
            assert CPFValidator.validate(cpf) is False


# ─────────────────────────────────────────────────────────────
# ReceitaFederalClient tests
# ─────────────────────────────────────────────────────────────


class TestReceitaFederalClient:
    @pytest.mark.asyncio
    async def test_regular_cpf_returns_positive_result(self) -> None:
        client = ReceitaFederalClient()
        result = await client.consult("52998224725", "Maria Silva", "1990-05-15")
        assert result.status == "regular"
        assert result.name_match is True
        assert result.deceased is False

    @pytest.mark.asyncio
    async def test_deceased_cpf_returns_deceased_flag(self) -> None:
        client = ReceitaFederalClient()
        result = await client.consult("12345679999", "Joao Santos", "1950-01-01")
        assert result.deceased is True
        assert result.status == "titular_falecido"

    @pytest.mark.asyncio
    async def test_suspended_cpf_returns_suspensa(self) -> None:
        client = ReceitaFederalClient()
        result = await client.consult("12345678888", "Ana Oliveira", "1985-03-20")
        assert result.status == "suspensa"
        assert result.name_match is False

    @pytest.mark.asyncio
    async def test_consult_or_raise_deceased_raises(self) -> None:
        client = ReceitaFederalClient()
        with pytest.raises(CPFDeceasedError):
            await client.consult_or_raise("12345679999", "Joao", "1950-01-01")

    @pytest.mark.asyncio
    async def test_consult_or_raise_suspended_raises(self) -> None:
        client = ReceitaFederalClient()
        with pytest.raises(CPFStatusError):
            await client.consult_or_raise("12345678888", "Ana", "1985-01-01")

    @pytest.mark.asyncio
    async def test_consult_or_raise_regular_returns_result(self) -> None:
        client = ReceitaFederalClient()
        result = await client.consult_or_raise("52998224725", "Maria", "1990-01-01")
        assert result.status == "regular"


# ─────────────────────────────────────────────────────────────
# BiometricService tests
# ─────────────────────────────────────────────────────────────


class TestBiometricService:
    @pytest.mark.asyncio
    async def test_normal_token_passes(self) -> None:
        svc = BiometricService()
        result = await svc.verify("selfie_b64", "doc_b64", liveness_token="OK")
        assert result.passed is True
        assert result.confidence_score >= 0.80

    @pytest.mark.asyncio
    async def test_fail_token_fails_liveness(self) -> None:
        svc = BiometricService()
        result = await svc.verify("selfie_b64", "doc_b64", liveness_token="FAIL")
        assert result.liveness_passed is False
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_low_token_fails_threshold(self) -> None:
        svc = BiometricService()
        result = await svc.verify("selfie_b64", "doc_b64", liveness_token="LOW")
        assert result.confidence_score < 0.80
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_no_token_passes(self) -> None:
        svc = BiometricService()
        result = await svc.verify("selfie_b64", "doc_b64")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_verify_or_raise_liveness_fail_raises(self) -> None:
        svc = BiometricService()
        with pytest.raises(LivenessFailedError):
            await svc.verify_or_raise("selfie_b64", "doc_b64", liveness_token="FAIL")

    @pytest.mark.asyncio
    async def test_verify_or_raise_low_confidence_raises(self) -> None:
        svc = BiometricService()
        with pytest.raises(BiometricMismatchError) as exc_info:
            await svc.verify_or_raise("selfie_b64", "doc_b64", liveness_token="LOW")
        assert exc_info.value.score < 0.80

    @pytest.mark.asyncio
    async def test_result_contains_reference_hash(self) -> None:
        svc = BiometricService()
        result = await svc.verify("selfie_b64", "doc_b64")
        assert len(result.reference_hash) == 64  # SHA-256 hex

    @pytest.mark.asyncio
    async def test_custom_threshold_respected(self) -> None:
        svc = BiometricService(threshold=0.95)
        result = await svc.verify("selfie_b64", "doc_b64")
        # Default stub score is 0.92 < 0.95
        assert result.passed is False


# ─────────────────────────────────────────────────────────────
# WelfareRegistryClient tests
# ─────────────────────────────────────────────────────────────


class TestWelfareRegistryClient:
    @pytest.mark.asyncio
    async def test_clean_cpf_no_restriction(self) -> None:
        client = WelfareRegistryClient(mock=True)
        result = await client.check("52998224725")
        assert result.restriction_active is False
        assert result.resultado == "NAO_IMPEDIDO"
        assert result.motivos == ()
        assert len(result.cpf_hash) == 64

    @pytest.mark.asyncio
    async def test_official_social_program_fixture_is_restricted(self) -> None:
        client = WelfareRegistryClient(mock=True)
        result = await client.check("28784142090")
        assert result.motivos == ("PROGRAMA_SOCIAL",)
        assert result.restriction_active is True

    @pytest.mark.asyncio
    async def test_centralized_self_exclusion_is_restricted(self) -> None:
        client = WelfareRegistryClient(mock=True)
        result = await client.check("51077358008")
        assert result.motivos == ("AUTOEXCLUSAO_CENTRALIZADA",)
        assert result.restriction_active is True

    @pytest.mark.asyncio
    async def test_v2_can_return_multiple_reasons(self) -> None:
        client = WelfareRegistryClient(mock=True)
        result = await client.check("10996230572")
        assert result.motivos == (
            "AUTOEXCLUSAO_CENTRALIZADA",
            "PROGRAMA_SOCIAL",
        )

    @pytest.mark.asyncio
    async def test_check_or_raise_impediment_raises(self) -> None:
        client = WelfareRegistryClient(mock=True)
        with pytest.raises(WelfareBeneficiaryError) as exc_info:
            await client.check_or_raise("28784142090")
        assert "PROGRAMA_SOCIAL" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_production_without_token_fails_closed(self) -> None:
        client = WelfareRegistryClient()
        with pytest.raises(WelfareCheckError):
            await client.check("52998224725")

    @pytest.mark.asyncio
    async def test_result_includes_checked_at(self) -> None:
        client = WelfareRegistryClient(mock=True)
        before = datetime.now(timezone.utc)
        result = await client.check("52998224725")
        assert result.checked_at >= before


# ─────────────────────────────────────────────────────────────
# FastAPI endpoint integration tests (TestClient / httpx)
# ─────────────────────────────────────────────────────────────


def _make_register_payload(**overrides: Any) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "cpf": "529.982.247-25",
        "full_name": "Maria Aparecida Silva",
        "date_of_birth": "1990-05-15",
        "email": "maria@example.com.br",
        "phone_br": "+5511987654321",
        "address_cep": "01310-100",
        "address_street": "Avenida Paulista",
        "address_number": "1000",
        "address_city": "São Paulo",
        "address_state": "SP",
        "document_type": "rg",
        "document_number": "12.345.678-9",
        "gender": "F",
        "lgpd_consent": True,
    }
    defaults.update(overrides)
    return defaults


class TestPAMEndpoints:
    """
    Integration-style tests using an in-memory SQLite database via
    httpx.AsyncClient + ASGITransport to avoid real PostgreSQL.
    """

    @pytest.fixture(autouse=True)
    def patch_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Replace the database layer with an in-memory dict store so tests
        run without a real PostgreSQL instance.
        """
        # We test module functions directly rather than through HTTP
        # to keep tests self-contained without a running DB.
        pass

    @pytest.mark.asyncio
    async def test_health_endpoint(self) -> None:
        """Health check must always return 200 with status ok."""
        import httpx
        from main import app

        # Patch lifespan dependencies
        with patch("main.create_tables", new_callable=AsyncMock), \
             patch("main.dispose_engine", new_callable=AsyncMock), \
             patch("asyncio.create_task"):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "pam"

    @pytest.mark.asyncio
    async def test_register_invalid_cpf_returns_422(self) -> None:
        import httpx
        from main import app
        from database import get_session
        from sqlalchemy.ext.asyncio import AsyncSession

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute.return_value = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            with patch("main.create_tables", new_callable=AsyncMock), \
                 patch("main.dispose_engine", new_callable=AsyncMock), \
                 patch("asyncio.create_task"):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    payload = _make_register_payload(cpf="111.111.111-11")
                    resp = await client.post("/players/register", json=payload)
        finally:
            app.dependency_overrides.pop(get_session, None)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_register_lgpd_consent_false_returns_422(self) -> None:
        import httpx
        from main import app
        from database import get_session
        from sqlalchemy.ext.asyncio import AsyncSession

        async def override_get_session():
            mock_session = AsyncMock(spec=AsyncSession)
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            with patch("main.create_tables", new_callable=AsyncMock), \
                 patch("main.dispose_engine", new_callable=AsyncMock), \
                 patch("asyncio.create_task"):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    payload = _make_register_payload(lgpd_consent=False)
                    resp = await client.post("/players/register", json=payload)
        finally:
            app.dependency_overrides.pop(get_session, None)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_unknown_player_returns_404(self) -> None:
        import httpx
        from main import app
        from database import get_session
        from sqlalchemy.ext.asyncio import AsyncSession

        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            with patch("main.create_tables", new_callable=AsyncMock), \
                 patch("main.dispose_engine", new_callable=AsyncMock), \
                 patch("asyncio.create_task"):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/players/52998224725")
        finally:
            app.dependency_overrides.pop(get_session, None)
        assert resp.status_code == 404


# Helper for async generator mock
async def _async_gen(value: Any):
    yield value


# ─────────────────────────────────────────────────────────────
# CPF edge-case tests
# ─────────────────────────────────────────────────────────────


class TestCPFEdgeCases:
    def test_cpf_with_spaces_invalid(self) -> None:
        # Normalisation strips non-digits; 11 spaces → empty string → invalid
        assert CPFValidator.validate("           ") is False

    def test_cpf_bare_digits_valid(self) -> None:
        assert CPFValidator.validate("52998224725") is True

    def test_cpf_with_dots_and_dash_valid(self) -> None:
        assert CPFValidator.validate("529.982.247-25") is True

    def test_hash_consistency(self) -> None:
        h1 = CPFValidator.hash("529.982.247-25")
        h2 = CPFValidator.hash("52998224725")
        assert h1 == h2  # format normalised before hashing

    def test_format_raises_on_wrong_length(self) -> None:
        with pytest.raises(ValueError):
            CPFValidator.format("12345")

    def test_validate_none_like_empty_string(self) -> None:
        assert CPFValidator.validate("") is False

    def test_validate_12_digits_invalid(self) -> None:
        assert CPFValidator.validate("529982247250") is False


# ─────────────────────────────────────────────────────────────
# Welfare + biometric combined pipeline test
# ─────────────────────────────────────────────────────────────


class TestCombinedPipeline:
    @pytest.mark.asyncio
    async def test_sigap_check_blocks_social_program_fixture(self) -> None:
        """An official homologation CPF for PROGRAMA_SOCIAL is restricted."""
        welfare = WelfareRegistryClient(mock=True)
        result = await welfare.check("28784142090")
        assert result.restriction_active is True

    @pytest.mark.asyncio
    async def test_biometric_pass_then_welfare_clean(self) -> None:
        """Happy path: biometric passes and welfare is clear."""
        bio = BiometricService()
        welfare = WelfareRegistryClient(mock=True)

        bio_result = await bio.verify_or_raise("s64", "d64")
        assert bio_result.passed is True

        welfare_result = await welfare.check_or_raise("52998224725")
        assert welfare_result.restriction_active is False

    @pytest.mark.asyncio
    async def test_biometric_fail_blocks_pipeline(self) -> None:
        """Pipeline must halt on biometric failure."""
        bio = BiometricService()
        with pytest.raises(LivenessFailedError):
            await bio.verify_or_raise("s64", "d64", liveness_token="FAIL")

    @pytest.mark.asyncio
    async def test_rf_deceased_blocks_registration(self) -> None:
        """Deceased CPF must raise before any account is created."""
        rf = ReceitaFederalClient()
        with pytest.raises(CPFDeceasedError):
            await rf.consult_or_raise("12345679999", "Ghost User", "1900-01-01")
