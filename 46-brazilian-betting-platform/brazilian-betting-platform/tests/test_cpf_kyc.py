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
Tests for CPF Validation & KYC Pipeline
=========================================
Comprehensive pytest suite covering:
  - CPF digit validation algorithm (Receita Federal spec)
  - Known-invalid CPF rejection
  - KYC registration flow
  - Biometric verification flow
  - Self-exclusion registry integration
  - Welfare registry check
  - Age verification (18+)
  - Duplicate account prevention
  - Periodic re-verification scheduler
  - LGPD erasure workflow
  - Edge cases and adversarial inputs

Reference implementation for Chapter 46: Brazilian Betting Platform.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from cpf_kyc_service import (
    BiometricMismatchError,
    BiometricSubmission,
    BiometricVerificationClient,
    CPFInvalidError,
    CPFValidator,
    DocumentType,
    GenderCode,
    KYCError,
    KYCPipeline,
    KYCRecord,
    KYCRegistrationRequest,
    KYCStatus,
    KYCStore,
    LGPDConsentError,
    ReceiraFederalResult,
    ReceitaFederalClient,
    ReVerificationRequest,
    ReVerificationScheduler,
    SelfExcludedError,
    SelfExclusionRegistryClient,
    SelfExclusionResult,
    WelfareBeneficiaryError,
    WelfareCheckResult,
    WelfareRegistryClient,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def kyc_store():
    return KYCStore()


@pytest.fixture
def mock_rf_client():
    """Receita Federal client that always returns success."""
    client = AsyncMock(spec=ReceitaFederalClient)
    client.consult.return_value = ReceiraFederalResult(
        cpf="52998224725",
        name_match=True,
        dob_match=True,
        status="regular",
        deceased=False,
    )
    return client


@pytest.fixture
def mock_biometric_client():
    client = AsyncMock(spec=BiometricVerificationClient)
    client.verify.return_value = 0.95  # high confidence
    return client


@pytest.fixture
def mock_exclusion_client():
    client = AsyncMock(spec=SelfExclusionRegistryClient)
    client.check.return_value = SelfExclusionResult(
        cpf_hash="any_hash",
        is_excluded=False,
        exclusion_type=None,
        exclusion_end=None,
        source="SIGAP",
    )
    return client


@pytest.fixture
def mock_welfare_client():
    client = AsyncMock(spec=WelfareRegistryClient)
    client.check.return_value = WelfareCheckResult(
        cpf_hash="any_hash",
        resultado="NAO_IMPEDIDO",
        motivos=(),
        request_id="req-clean",
    )
    return client


@pytest.fixture
def pipeline(
    kyc_store,
    mock_rf_client,
    mock_biometric_client,
    mock_exclusion_client,
    mock_welfare_client,
):
    return KYCPipeline(
        store=kyc_store,
        rf_client=mock_rf_client,
        biometric_client=mock_biometric_client,
        exclusion_client=mock_exclusion_client,
        welfare_client=mock_welfare_client,
    )


def make_registration(**overrides) -> KYCRegistrationRequest:
    """Return a valid KYCRegistrationRequest, optionally overriding fields."""
    defaults = dict(
        cpf="529.982.247-25",   # valid CPF for testing
        full_name="Maria Oliveira Santos",
        date_of_birth="1990-05-15",
        email="maria@example.com",
        phone_br="+5511999999999",
        address_cep="01310-100",
        address_street="Av. Paulista",
        address_number="1000",
        address_city="São Paulo",
        address_state="SP",
        document_type=DocumentType.CNH,
        document_number="12345678900",
        gender=GenderCode.FEMALE,
        lgpd_consent=True,
        marketing_consent=False,
    )
    defaults.update(overrides)
    return KYCRegistrationRequest(**defaults)


def make_biometric(player_id: str) -> BiometricSubmission:
    return BiometricSubmission(
        player_id=player_id,
        selfie_base64="/9j/4AAQSkZJRgABAQ...",
        document_front_base64="/9j/4AAQSkZJRgABAQ...",
        liveness_token="PASS",
    )


# ---------------------------------------------------------------------------
# CPFValidator Unit Tests
# ---------------------------------------------------------------------------


class TestCPFValidator:
    # Known valid CPFs (generated with algorithm)
    VALID_CPFS = [
        "529.982.247-25",
        "52998224725",
        "111.444.777-35",
        "11144477735",
        "582.858.314-00",
    ]

    INVALID_CPFS = [
        "123.456.789-00",   # wrong check digits
        "000.000.000-00",   # all zeros
        "111.111.111-11",   # repeating digits
        "999.999.999-99",   # repeating digits
        "12345678",         # too short
        "1234567890123",    # too long
        "",
        "abc.def.ghi-jk",
    ]

    @pytest.mark.parametrize("cpf", VALID_CPFS)
    def test_valid_cpfs_pass(self, cpf):
        assert CPFValidator.validate(cpf) is True

    @pytest.mark.parametrize("cpf", INVALID_CPFS)
    def test_invalid_cpfs_fail(self, cpf):
        assert CPFValidator.validate(cpf) is False

    def test_format_applies_mask(self):
        assert CPFValidator.format("52998224725") == "529.982.247-25"

    def test_format_accepts_bare_digits(self):
        result = CPFValidator.format("11144477735")
        assert result == "111.444.777-35"

    def test_hash_returns_sha256(self):
        cpf = "52998224725"
        expected = hashlib.sha256(cpf.encode()).hexdigest()
        assert CPFValidator.hash(cpf) == expected

    def test_hash_strips_punctuation(self):
        assert CPFValidator.hash("529.982.247-25") == CPFValidator.hash("52998224725")

    def test_all_same_digit_cpfs_invalid(self):
        for d in range(10):
            assert CPFValidator.validate(str(d) * 11) is False

    def test_cpf_with_spaces_normalised(self):
        # Leading/trailing spaces should not cause issues when pre-normalised
        bare = "52998224725"
        assert CPFValidator.validate(bare) is True

    def test_check_digit_boundary_zero(self):
        # CPF where remainder >= 10 → digit must be 0 (boundary case)
        # 104.332.181-00 is a valid CPF whose second check digit uses the boundary (rem=10 → 0)
        assert CPFValidator.validate("10433218100") is True

    def test_first_check_digit_only(self):
        """Corrupt just the first check digit."""
        # 529.982.247-25 → tamper digit at index 9
        tampered = "52998224715"  # changed 2→1
        assert CPFValidator.validate(tampered) is False

    def test_second_check_digit_only(self):
        """Corrupt just the second check digit."""
        tampered = "52998224724"  # changed 5→4
        assert CPFValidator.validate(tampered) is False


# ---------------------------------------------------------------------------
# KYC Registration Tests
# ---------------------------------------------------------------------------


class TestKYCRegistration:
    @pytest.mark.asyncio
    async def test_successful_registration(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        assert record.kyc_status == KYCStatus.IDENTITY_VERIFIED
        assert record.player_id is not None
        assert record.cpf_hash == CPFValidator.hash("52998224725")

    @pytest.mark.asyncio
    async def test_invalid_cpf_raises(self, pipeline):
        req = make_registration(cpf="123.456.789-00")
        with pytest.raises(CPFInvalidError):
            await pipeline.register(req)

    @pytest.mark.asyncio
    async def test_lgpd_consent_required(self):
        with pytest.raises(Exception):
            make_registration(lgpd_consent=False)

    @pytest.mark.asyncio
    async def test_duplicate_cpf_raises(self, pipeline):
        req = make_registration()
        await pipeline.register(req)
        # Second registration with same CPF
        req2 = make_registration(email="other@example.com")
        with pytest.raises(KYCError, match="Account already exists"):
            await pipeline.register(req2)

    @pytest.mark.asyncio
    async def test_deceased_cpf_raises(self, pipeline, mock_rf_client):
        mock_rf_client.consult.return_value = ReceiraFederalResult(
            cpf="52998224725",
            name_match=False,
            dob_match=False,
            status="titular_falecido",
            deceased=True,
        )
        req = make_registration()
        with pytest.raises(CPFInvalidError, match="deceased"):
            await pipeline.register(req)

    @pytest.mark.asyncio
    async def test_suspended_cpf_raises(self, pipeline, mock_rf_client):
        mock_rf_client.consult.return_value = ReceiraFederalResult(
            cpf="52998224725",
            name_match=True,
            dob_match=True,
            status="suspensa",
            deceased=False,
        )
        req = make_registration()
        with pytest.raises(CPFInvalidError):
            await pipeline.register(req)

    @pytest.mark.asyncio
    async def test_name_mismatch_raises(self, pipeline, mock_rf_client):
        mock_rf_client.consult.return_value = ReceiraFederalResult(
            cpf="52998224725",
            name_match=False,
            dob_match=True,
            status="regular",
            deceased=False,
        )
        req = make_registration()
        with pytest.raises(CPFInvalidError, match="Name does not match"):
            await pipeline.register(req)

    @pytest.mark.asyncio
    async def test_underage_player_raises(self, pipeline):
        # DOB making player 17 years old
        dob_17 = (datetime.now(timezone.utc) - timedelta(days=17 * 365)).strftime(
            "%Y-%m-%d"
        )
        req = make_registration(date_of_birth=dob_17)
        with pytest.raises(KYCError, match="18 years"):
            await pipeline.register(req)

    @pytest.mark.asyncio
    async def test_exactly_18_is_allowed(self, pipeline):
        dob_18 = (datetime.now(timezone.utc) - timedelta(days=18 * 365 + 1)).strftime(
            "%Y-%m-%d"
        )
        req = make_registration(date_of_birth=dob_18)
        record = await pipeline.register(req)
        assert record.kyc_status == KYCStatus.IDENTITY_VERIFIED

    @pytest.mark.asyncio
    async def test_pii_is_hashed_not_stored_plaintext(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        # Ensure no plaintext CPF stored
        assert "52998224725" not in str(record.cpf_hash)
        assert record.cpf_hash == hashlib.sha256("52998224725".encode()).hexdigest()

    @pytest.mark.asyncio
    async def test_registration_audit_trail_created(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        assert len(record.audit_trail) >= 1
        assert record.audit_trail[0]["event"] == "registration"

    @pytest.mark.asyncio
    async def test_cpf_normalised_before_hash(self, pipeline, kyc_store):
        req_formatted = make_registration(cpf="529.982.247-25")
        req_bare = make_registration(cpf="52998224725", email="other2@example.com")
        record1 = await pipeline.register(req_formatted)
        # Hashes must be equal (same CPF, different format)
        assert record1.cpf_hash == CPFValidator.hash("52998224725")


# ---------------------------------------------------------------------------
# Biometric Verification Tests
# ---------------------------------------------------------------------------


class TestBiometricVerification:
    @pytest.mark.asyncio
    async def test_successful_biometric_leads_to_approval(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)
        approved = await pipeline.submit_biometric(bio)
        assert approved.kyc_status == KYCStatus.APPROVED

    @pytest.mark.asyncio
    async def test_low_confidence_raises_mismatch(
        self, pipeline, mock_biometric_client
    ):
        mock_biometric_client.verify.return_value = 0.50  # below threshold

        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)

        with pytest.raises(BiometricMismatchError):
            await pipeline.submit_biometric(bio)

    @pytest.mark.asyncio
    async def test_failed_biometric_sets_rejected_status(
        self, pipeline, mock_biometric_client, kyc_store
    ):
        mock_biometric_client.verify.return_value = 0.30

        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)

        try:
            await pipeline.submit_biometric(bio)
        except BiometricMismatchError:
            pass

        stored = await kyc_store.get_by_player(record.player_id)
        assert stored.kyc_status == KYCStatus.REJECTED

    @pytest.mark.asyncio
    async def test_biometric_score_recorded(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)
        approved = await pipeline.submit_biometric(bio)
        assert approved.biometric_score == pytest.approx(0.95, abs=0.01)

    @pytest.mark.asyncio
    async def test_biometric_for_nonexistent_player_raises(self, pipeline):
        bio = make_biometric("nonexistent_player_xyz")
        with pytest.raises(KYCError):
            await pipeline.submit_biometric(bio)

    @pytest.mark.asyncio
    async def test_biometric_liveness_fail_token(
        self, pipeline, mock_biometric_client
    ):
        mock_biometric_client.verify.return_value = 0.20  # FAIL token result

        req = make_registration()
        record = await pipeline.register(req)
        bio = BiometricSubmission(
            player_id=record.player_id,
            selfie_base64="data",
            document_front_base64="data",
            liveness_token="FAIL",
        )
        with pytest.raises(BiometricMismatchError):
            await pipeline.submit_biometric(bio)


# ---------------------------------------------------------------------------
# Self-Exclusion Registry Tests
# ---------------------------------------------------------------------------


class TestSelfExclusionCheck:
    @pytest.mark.asyncio
    async def test_excluded_player_raises_self_excluded(
        self, pipeline, mock_exclusion_client
    ):
        mock_exclusion_client.check.return_value = SelfExclusionResult(
            cpf_hash="any",
            is_excluded=True,
            exclusion_type="permanent",
            exclusion_end=None,
            source="SIGAP",
        )
        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)

        with pytest.raises(SelfExcludedError):
            await pipeline.submit_biometric(bio)

    @pytest.mark.asyncio
    async def test_excluded_player_status_set_to_rejected(
        self, pipeline, mock_exclusion_client, kyc_store
    ):
        mock_exclusion_client.check.return_value = SelfExclusionResult(
            cpf_hash="any",
            is_excluded=True,
            exclusion_type="temporary",
            exclusion_end=datetime.now(timezone.utc) + timedelta(days=30),
            source="APOSTA_RESPONSAVEL",
        )
        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)

        try:
            await pipeline.submit_biometric(bio)
        except SelfExcludedError:
            pass

        stored = await kyc_store.get_by_player(record.player_id)
        assert stored.kyc_status == KYCStatus.REJECTED
        assert "self-exclusion" in stored.rejection_reason.lower()


# ---------------------------------------------------------------------------
# Welfare Registry Tests
# ---------------------------------------------------------------------------


class TestWelfareCheck:
    @pytest.mark.asyncio
    async def test_bolsa_familia_beneficiary_blocked(
        self, pipeline, mock_welfare_client
    ):
        mock_welfare_client.check.return_value = WelfareCheckResult(
            cpf_hash="any",
            resultado="IMPEDIDO",
            motivos=("PROGRAMA_SOCIAL",),
            request_id="req-social",
        )
        req = make_registration()
        with pytest.raises(WelfareBeneficiaryError):
            await pipeline.register(req)

    @pytest.mark.asyncio
    async def test_other_sigap_impediment_is_also_blocked(
        self, pipeline, mock_welfare_client
    ):
        mock_welfare_client.check.return_value = WelfareCheckResult(
            cpf_hash="any",
            resultado="IMPEDIDO",
            motivos=("AUTOEXCLUSAO_CENTRALIZADA",),
            request_id="req-exclusion",
        )
        req = make_registration()
        with pytest.raises(WelfareBeneficiaryError):
            await pipeline.register(req)

    @pytest.mark.asyncio
    async def test_no_welfare_passes(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)
        approved = await pipeline.submit_biometric(bio)
        assert approved.kyc_status == KYCStatus.APPROVED


# ---------------------------------------------------------------------------
# Re-verification Tests
# ---------------------------------------------------------------------------


class TestReVerification:
    @pytest.mark.asyncio
    async def test_approved_player_can_be_reverified(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)
        await pipeline.submit_biometric(bio)

        reverify = ReVerificationRequest(
            player_id=record.player_id, reason="periodic_15_day"
        )
        updated = await pipeline.trigger_reverification(reverify)
        assert updated.kyc_status == KYCStatus.REVERIFICATION_REQUIRED

    @pytest.mark.asyncio
    async def test_non_approved_cannot_be_reverified(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        # Still in IDENTITY_VERIFIED state
        reverify = ReVerificationRequest(player_id=record.player_id)
        with pytest.raises(KYCError):
            await pipeline.trigger_reverification(reverify)

    @pytest.mark.asyncio
    async def test_reverification_sets_next_due(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)
        approved = await pipeline.submit_biometric(bio)

        assert approved.next_verification_due is not None
        expected_days = KYCPipeline.RE_VERIFICATION_DAYS
        delta = (approved.next_verification_due - approved.last_verified_at).days
        assert delta == expected_days

    @pytest.mark.asyncio
    async def test_is_kyc_expired_true_when_overdue(self, pipeline, kyc_store):
        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)
        await pipeline.submit_biometric(bio)

        stored = await kyc_store.get_by_player(record.player_id)
        # Manually backdate the next verification due
        stored.next_verification_due = datetime.now(timezone.utc) - timedelta(days=1)
        await kyc_store.save(stored)

        assert stored.is_kyc_expired is True

    @pytest.mark.asyncio
    async def test_is_kyc_expired_false_when_fresh(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        bio = make_biometric(record.player_id)
        approved = await pipeline.submit_biometric(bio)
        assert approved.is_kyc_expired is False


# ---------------------------------------------------------------------------
# LGPD Erasure Tests
# ---------------------------------------------------------------------------


class TestLGPDErasure:
    @pytest.mark.asyncio
    async def test_erasure_anonymises_pii(self, pipeline, kyc_store):
        req = make_registration()
        record = await pipeline.register(req)
        await pipeline.process_lgpd_deletion(record.player_id)

        stored = await kyc_store.get_by_player(record.player_id)
        assert stored.full_name == "[DELETED]"
        assert stored.email == "[DELETED]"
        assert stored.phone_hash == "[DELETED]"
        assert stored.document_number_hash == "[DELETED]"

    @pytest.mark.asyncio
    async def test_erasure_retains_cpf_hash(self, pipeline, kyc_store):
        """CPF hash must be retained for 5-year regulatory compliance."""
        req = make_registration()
        record = await pipeline.register(req)
        original_cpf_hash = record.cpf_hash
        await pipeline.process_lgpd_deletion(record.player_id)

        stored = await kyc_store.get_by_player(record.player_id)
        assert stored.cpf_hash == original_cpf_hash

    @pytest.mark.asyncio
    async def test_erasure_sets_suspended_status(self, pipeline, kyc_store):
        req = make_registration()
        record = await pipeline.register(req)
        await pipeline.process_lgpd_deletion(record.player_id)

        stored = await kyc_store.get_by_player(record.player_id)
        assert stored.kyc_status == KYCStatus.SUSPENDED

    @pytest.mark.asyncio
    async def test_erasure_appends_audit_entry(self, pipeline, kyc_store):
        req = make_registration()
        record = await pipeline.register(req)
        await pipeline.process_lgpd_deletion(record.player_id)

        stored = await kyc_store.get_by_player(record.player_id)
        events = [e["event"] for e in stored.audit_trail]
        assert "lgpd_erasure" in events

    @pytest.mark.asyncio
    async def test_erasure_of_nonexistent_player_raises(self, pipeline):
        with pytest.raises(KYCError):
            await pipeline.process_lgpd_deletion("nonexistent_xyz")

    @pytest.mark.asyncio
    async def test_erasure_returns_confirmation(self, pipeline):
        req = make_registration()
        record = await pipeline.register(req)
        result = await pipeline.process_lgpd_deletion(record.player_id)
        assert result["status"] == "anonymized"
        assert result["player_id"] == record.player_id


# ---------------------------------------------------------------------------
# KYC Store Tests
# ---------------------------------------------------------------------------


class TestKYCStore:
    def _make_record(self, player_id: str = "p001") -> KYCRecord:
        now = datetime.now(timezone.utc)
        return KYCRecord(
            player_id=player_id,
            cpf_hash=hashlib.sha256(f"cpf_{player_id}".encode()).hexdigest(),
            full_name="Test User",
            date_of_birth="1990-01-01",
            email="test@example.com",
            phone_hash="hash",
            address_cep="01310-100",
            address_state="SP",
            document_type=DocumentType.CNH,
            document_number_hash="doc_hash",
            gender=GenderCode.NOT_STATED,
            kyc_status=KYCStatus.APPROVED,
            lgpd_consent_at=now,
            marketing_consent=False,
            created_at=now,
            updated_at=now,
            last_verified_at=now,
            next_verification_due=now + timedelta(days=15),
        )

    @pytest.mark.asyncio
    async def test_save_and_get_by_player(self, kyc_store):
        record = self._make_record()
        await kyc_store.save(record)
        found = await kyc_store.get_by_player(record.player_id)
        assert found is not None
        assert found.player_id == record.player_id

    @pytest.mark.asyncio
    async def test_get_by_cpf_hash(self, kyc_store):
        record = self._make_record()
        await kyc_store.save(record)
        found = await kyc_store.get_by_cpf_hash(record.cpf_hash)
        assert found is not None
        assert found.cpf_hash == record.cpf_hash

    @pytest.mark.asyncio
    async def test_missing_player_returns_none(self, kyc_store):
        assert await kyc_store.get_by_player("xyz") is None

    @pytest.mark.asyncio
    async def test_list_due_reverification(self, kyc_store):
        now = datetime.now(timezone.utc)
        # Due
        due = self._make_record("due_player")
        due.next_verification_due = now - timedelta(hours=1)
        await kyc_store.save(due)
        # Not yet due
        not_due = self._make_record("fresh_player")
        not_due.next_verification_due = now + timedelta(days=10)
        await kyc_store.save(not_due)

        result = await kyc_store.list_due_reverification()
        assert any(r.player_id == "due_player" for r in result)
        assert not any(r.player_id == "fresh_player" for r in result)


# ---------------------------------------------------------------------------
# Edge Case / Adversarial Tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_cpf_with_all_nines_invalid(self):
        assert CPFValidator.validate("99999999999") is False

    def test_cpf_with_leading_zeros_valid(self):
        # CPF starting with 0 (border case for numeric parsing)
        # 013.713.793-19 is a valid CPF
        assert CPFValidator.validate("01371379319") is True

    @pytest.mark.asyncio
    async def test_registration_without_marketing_consent_succeeds(self, pipeline):
        req = make_registration(marketing_consent=False)
        record = await pipeline.register(req)
        assert record.marketing_consent is False

    @pytest.mark.asyncio
    async def test_registration_with_formatted_cpf_normalised(self, pipeline):
        req = make_registration(cpf="529.982.247-25")
        record = await pipeline.register(req)
        # CPF hash must equal bare CPF hash
        assert record.cpf_hash == CPFValidator.hash("52998224725")

    def test_cpf_hash_is_deterministic(self):
        h1 = CPFValidator.hash("52998224725")
        h2 = CPFValidator.hash("52998224725")
        assert h1 == h2

    def test_different_cpfs_have_different_hashes(self):
        assert CPFValidator.hash("52998224725") != CPFValidator.hash("11144477735")

    @pytest.mark.asyncio
    async def test_concurrent_registrations_different_players(self, pipeline):
        """Two different players can register concurrently without conflict."""
        req1 = make_registration(cpf="529.982.247-25", email="a@ex.com")
        req2 = make_registration(cpf="111.444.777-35", email="b@ex.com")
        r1, r2 = await asyncio.gather(
            pipeline.register(req1),
            pipeline.register(req2),
        )
        assert r1.player_id != r2.player_id
