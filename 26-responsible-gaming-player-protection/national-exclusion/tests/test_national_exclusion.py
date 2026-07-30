# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
tests/test_national_exclusion.py — Tests for the national exclusion service.

Covers:
  - GamstopService: validation, exclusion detection, rate limiting
  - SpelpausService: MD5 hashing, SSN expansion, exclusion logic
  - RofusService: single and batch CPR checks
  - BrazilRegistryService: CPF normalisation, register, revoke
  - RegistryRouter: jurisdiction routing
  - FastAPI endpoints: /check, /register, /revoke, /status
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import date, datetime, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Optional, cast
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# national-exclusion ships `models.py`, `main.py`, and a bunch of
# short-named service modules (gamstop, rofus, spelpaus, ...). Several
# sibling chapters ship conflicting `models.py` / `main.py`, so
# pre-install this service's copies via importlib before the plain
# `from main import app` runs.
SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str) -> None:
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, SERVICE_DIR / file_name,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


for _mod, _file in [
    ("models", "models.py"),
    ("hash_utils", "hash_utils.py"),
    ("brazil_registry", "brazil_registry.py"),
    ("gamstop", "gamstop.py"),
    ("rofus", "rofus.py"),
    ("spelpaus", "spelpaus.py"),
    ("registry_router", "registry_router.py"),
    ("national_exclusion", "national_exclusion.py"),
    ("main", "main.py"),
]:
    _load_local_module(_mod, _file)


@pytest.fixture(autouse=True, scope="module")
def _pin_local_modules():
    """Re-pin this service's modules at module-scoped time so lazy
    `from main import app` inside `TestClient(app)` picks up this
    chapter's main rather than a sibling's."""
    for _mod, _file in [
        ("models", "models.py"),
        ("hash_utils", "hash_utils.py"),
        ("brazil_registry", "brazil_registry.py"),
        ("gamstop", "gamstop.py"),
        ("rofus", "rofus.py"),
        ("spelpaus", "spelpaus.py"),
        ("registry_router", "registry_router.py"),
        ("national_exclusion", "national_exclusion.py"),
        ("main", "main.py"),
    ]:
        _load_local_module(_mod, _file)
    yield


from brazil_registry import BrazilRegistryService, format_cpf, normalise_cpf
from gamstop import GamstopService
from main import app
from models import (
    BrazilApiConfig,
    BrazilUser,
    ExclusionCheck,
    GamstopApiConfig,
    GamstopUser,
    Jurisdiction,
    Registry,
    RegistrationRequest,
    RevocationRequest,
    RofusApiConfig,
    RofusUser,
    SpelpausApiConfig,
    SpelpausUser,
)
from registry_router import RegistryRouter
from rofus import RofusService
from spelpaus import SpelpausService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gamstop_config() -> GamstopApiConfig:
    return GamstopApiConfig(
        batch_service_url="https://api.gamstop.test/v2",
        api_key="test-gamstop-key",
        response_timeout_seconds=5,
    )


@pytest.fixture
def spelpaus_config() -> SpelpausApiConfig:
    return SpelpausApiConfig(
        batch_service_url="https://api.spelpaus.test",
        api_key="test-spelpaus-key",
        actor_id="operator-123",
        response_timeout_seconds=5,
    )


@pytest.fixture
def rofus_config() -> RofusApiConfig:
    return RofusApiConfig(
        base_url="https://api.rofus.test",
        api_key="test-rofus-key",
        operator_id="dk-operator-1",
        response_timeout_seconds=5,
    )


@pytest.fixture
def brazil_config() -> BrazilApiConfig:
    return BrazilApiConfig(
        base_url="https://api.seae.test",
        api_key="test-brazil-key",
        response_timeout_seconds=5,
    )


@pytest.fixture
def api_client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# GamStop tests
# ---------------------------------------------------------------------------

class TestGamstopService:

    def test_valid_user_passes_validation(self, gamstop_config):
        svc = GamstopService(gamstop_config)
        user = GamstopUser(id=1, first_name="John", last_name="Smith",
                           dob="1985-06-15", email="john@example.com",
                           postcode="SW1A1AA", mobile="+447911123456")
        assert svc._is_valid(user) is True

    def test_short_first_name_fails_validation(self, gamstop_config):
        svc = GamstopService(gamstop_config)
        user = GamstopUser(id=1, first_name="J", last_name="Smith",
                           dob="1985-06-15", email=None,
                           postcode="SW1A1AA", mobile=None)
        assert svc._is_valid(user) is False

    def test_short_postcode_fails_validation(self, gamstop_config):
        svc = GamstopService(gamstop_config)
        user = GamstopUser(id=1, first_name="John", last_name="Smith",
                           dob="1985-06-15", email=None,
                           postcode="SW1", mobile=None)
        assert svc._is_valid(user) is False

    def test_payload_omits_empty_optional_fields(self, gamstop_config):
        svc = GamstopService(gamstop_config)
        user = GamstopUser(id=1, first_name="Jane", last_name="Doe",
                           dob="1990-01-01", email=None,
                           postcode="EC1A1BB", mobile=None)
        payload = svc._to_payload(user)
        assert "email" not in payload
        assert "mobile" not in payload
        assert payload["firstName"] == "Jane"

    def test_get_excluded_users_returns_excluded_only(self, gamstop_config):
        svc = GamstopService(gamstop_config)
        users = [
            GamstopUser(id=1, first_name="Alice", last_name="Jones",
                        dob="1988-03-12", email=None, postcode="W1A1AA", mobile=None),
            GamstopUser(id=2, first_name="Bob", last_name="Brown",
                        dob="1992-07-22", email=None, postcode="E1W1AA", mobile=None),
        ]
        mock_responses = [
            {"exclusionStatus": "Y"},
            {"exclusionStatus": "N"},
        ]
        with patch.object(svc, "_call_api", return_value=mock_responses):
            result = svc.get_excluded_users(users)
        assert len(result) == 1
        assert result[0].id == 1

    def test_invalid_users_are_skipped(self, gamstop_config):
        svc = GamstopService(gamstop_config)
        users = [
            GamstopUser(id=99, first_name="X", last_name="Y",   # invalid names
                        dob="2000-01-01", email=None, postcode="ABC", mobile=None),
        ]
        with patch.object(svc, "_call_api") as mock_api:
            result = svc.get_excluded_users(users)
        mock_api.assert_not_called()
        assert result == []

    def test_rate_limit_enforces_sleep(self, gamstop_config):
        """Verify rate limiter sleeps when called twice in quick succession."""
        svc = GamstopService(gamstop_config)
        svc._last_request_at = svc._last_request_at  # access attribute
        import time
        svc._last_request_at = time.monotonic()  # simulate just-completed request
        start = time.monotonic()
        svc._enforce_rate_limit()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.9   # should have waited ~1 second


# ---------------------------------------------------------------------------
# Spelpaus tests
# ---------------------------------------------------------------------------

class TestSpelpausService:

    def test_build_full_pin_expands_century(self, spelpaus_config):
        svc = SpelpausService(spelpaus_config)
        ssn = "900115-1234"
        dob = date(1990, 1, 15)
        result = svc._build_full_pin(ssn, dob)
        assert result == "199001151234"

    def test_md5_user_id_is_correct(self, spelpaus_config):
        svc = SpelpausService(spelpaus_config)
        expected = hashlib.md5(b"42").hexdigest()
        assert svc._md5_user_id(42) == expected

    def test_check_users_returns_excluded_set(self, spelpaus_config):
        svc = SpelpausService(spelpaus_config)
        user1 = SpelpausUser(id=10, ssn="900115-1234", dob=date(1990, 1, 15))
        user2 = SpelpausUser(id=20, ssn="850601-5678", dob=date(1985, 6, 1))

        # user1's hash is in allowed list → NOT excluded
        # user2's hash is absent → excluded
        hash1 = svc._md5_user_id(10)

        with patch.object(svc, "_call_api", return_value={hash1}):
            excluded_ids = svc.check_users([user1, user2])

        assert excluded_ids == {20}

    def test_check_users_empty_input(self, spelpaus_config):
        svc = SpelpausService(spelpaus_config)
        result = svc.check_users([])
        assert result == set()

    def test_all_users_allowed(self, spelpaus_config):
        svc = SpelpausService(spelpaus_config)
        users = [SpelpausUser(id=i, ssn="900115-1234", dob=date(1990, 1, 15))
                 for i in range(5)]
        all_hashes = {svc._md5_user_id(u.id) for u in users}
        with patch.object(svc, "_call_api", return_value=all_hashes):
            excluded_ids = svc.check_users(users)
        assert excluded_ids == set()

    def test_all_users_excluded(self, spelpaus_config):
        svc = SpelpausService(spelpaus_config)
        users = [SpelpausUser(id=i, ssn="900115-1234", dob=date(1990, 1, 15))
                 for i in range(3)]
        with patch.object(svc, "_call_api", return_value=set()):
            excluded_ids = svc.check_users(users)
        assert excluded_ids == {0, 1, 2}


# ---------------------------------------------------------------------------
# ROFUS tests
# ---------------------------------------------------------------------------

class TestRofusService:

    def test_check_single_excluded(self, rofus_config):
        svc = RofusService(rofus_config)
        user = RofusUser(id=100, cpr="150590-1234")
        with patch.object(svc, "_call_single",
                          return_value={"excluded": True, "until": "2025-12-31"}):
            is_excluded, until = svc.check_single(user)
        assert is_excluded is True
        assert until == "2025-12-31"

    def test_check_single_not_excluded(self, rofus_config):
        svc = RofusService(rofus_config)
        user = RofusUser(id=101, cpr="200180-9876")
        with patch.object(svc, "_call_single",
                          return_value={"excluded": False, "until": None}):
            is_excluded, until = svc.check_single(user)
        assert is_excluded is False
        assert until is None

    def test_batch_check_maps_results(self, rofus_config):
        svc = RofusService(rofus_config)
        users = [
            RofusUser(id=1, cpr="010101-1111"),
            RofusUser(id=2, cpr="020202-2222"),
        ]
        api_response = [
            {"cpr": "010101-1111", "excluded": True,  "until": None},
            {"cpr": "020202-2222", "excluded": False, "until": None},
        ]
        with patch.object(svc, "_call_batch", return_value=api_response):
            results = svc.check_batch(users)
        assert results[1] == (True, None)
        assert results[2] == (False, None)

    def test_get_excluded_users(self, rofus_config):
        svc = RofusService(rofus_config)
        users = [RofusUser(id=i, cpr=f"0101{i:02d}-000{i}") for i in range(1, 4)]
        with patch.object(svc, "_call_batch", return_value=[
            {"cpr": users[0].cpr, "excluded": True,  "until": None},
            {"cpr": users[1].cpr, "excluded": False, "until": None},
            {"cpr": users[2].cpr, "excluded": True,  "until": "2026-06-01"},
        ]):
            excluded = svc.get_excluded_users(users)
        assert {u.id for u in excluded} == {1, 3}


# ---------------------------------------------------------------------------
# Brazil registry tests
# ---------------------------------------------------------------------------

class TestBrazilRegistryService:

    def test_normalise_cpf_strips_formatting(self, brazil_config):
        raw = normalise_cpf("123.456.789-09")
        assert raw == "12345678909"

    def test_format_cpf_formats_correctly(self, brazil_config):
        formatted = format_cpf("12345678909")
        assert formatted == "123.456.789-09"

    def test_check_single_excluded(self, brazil_config):
        svc = BrazilRegistryService(brazil_config)
        user = BrazilUser(id=1, cpf="123.456.789-09")
        with patch.object(svc, "_get_exclusion",
                          return_value={"excluded": True,
                                        "registered_at": "2024-01-15T10:00:00Z"}):
            is_excluded, registered = svc.check_single(user)
        assert is_excluded is True
        assert registered == "2024-01-15T10:00:00Z"

    def test_check_batch_returns_correct_mapping(self, brazil_config):
        svc = BrazilRegistryService(brazil_config)
        users = [
            BrazilUser(id=1, cpf="111.111.111-11"),
            BrazilUser(id=2, cpf="222.222.222-22"),
        ]
        with patch.object(svc, "_post_batch", return_value=[
            {"cpf": "111.111.111-11", "excluded": False},
            {"cpf": "222.222.222-22", "excluded": True},
        ]):
            results = svc.check_batch(users)
        assert results[1] is False
        assert results[2] is True

    def test_register_calls_api(self, brazil_config):
        svc = BrazilRegistryService(brazil_config)
        user = BrazilUser(id=5, cpf="529.982.247-25")
        expected = {"registration_id": "abc-123", "effective_from": "2024-03-01"}
        with patch.object(svc, "_post_register", return_value=expected) as mock_reg:
            result = svc.register(user, duration="permanent", reason="personal choice")
        mock_reg.assert_called_once()
        assert result == expected

    def test_revoke_calls_api(self, brazil_config):
        svc = BrazilRegistryService(brazil_config)
        user = BrazilUser(id=5, cpf="529.982.247-25")
        expected = {"revoked": True, "effective_from": "2024-06-01"}
        with patch.object(svc, "_delete_exclusion", return_value=expected):
            result = svc.revoke(user)
        assert result["revoked"] is True

    def test_invalid_cpf_length_raises(self, brazil_config):
        svc = BrazilRegistryService(brazil_config)
        user = BrazilUser(id=9, cpf="123-ABC")
        with pytest.raises((ValueError, Exception)):
            svc.check_single(user)


# ---------------------------------------------------------------------------
# RegistryRouter tests
# ---------------------------------------------------------------------------

class TestRegistryRouter:

    def test_routes_gb_to_gamstop(self):
        router = RegistryRouter()
        req = ExclusionCheck(
            player_id="test-gb",
            jurisdiction=Jurisdiction.GB,
            registry=Registry.GAMSTOP,
        )
        with patch.object(router, "_check_gamstop") as mock_check:
            from models import ExclusionStatus
            mock_check.return_value = ExclusionStatus(
                player_id="test-gb",
                registry=Registry.GAMSTOP,
                is_excluded=False,
                checked_at=datetime.now(timezone.utc),
            )
            router.check(req)
        mock_check.assert_called_once_with(req)

    def test_routes_br_to_brazil(self):
        router = RegistryRouter()
        req = ExclusionCheck(
            player_id="123.456.789-09",
            jurisdiction=Jurisdiction.BR,
            registry=Registry.BRAZIL_NATIONAL,
        )
        with patch.object(router, "_check_brazil") as mock_check:
            from models import ExclusionStatus
            mock_check.return_value = ExclusionStatus(
                player_id="123.456.789-09",
                registry=Registry.BRAZIL_NATIONAL,
                is_excluded=True,
                checked_at=datetime.now(timezone.utc),
            )
            router.check(req)
        mock_check.assert_called_once_with(req)

    def test_register_not_supported_for_gb(self):
        router = RegistryRouter()
        req = RegistrationRequest(player_id="test", jurisdiction=Jurisdiction.GB)
        with pytest.raises(NotImplementedError):
            router.register(req)

    def test_revoke_not_supported_for_se(self):
        router = RegistryRouter()
        req = RevocationRequest(player_id="test", jurisdiction=Jurisdiction.SE)
        with pytest.raises(NotImplementedError):
            router.revoke(req)

    def test_unsupported_jurisdiction_raises(self):
        router = RegistryRouter()
        with pytest.raises((ValueError, AttributeError)):
            router.check(ExclusionCheck(
                player_id="x",
                jurisdiction="ZZ",   # type: ignore[arg-type]
                registry=Registry.GAMSTOP,
            ))


# ---------------------------------------------------------------------------
# FastAPI endpoint tests
# ---------------------------------------------------------------------------

class TestApiEndpoints:

    def test_health_returns_ok(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_check_gb_returns_200(self, api_client):
        from models import ExclusionStatus
        mock_status = ExclusionStatus(
            player_id="player-1",
            registry=Registry.GAMSTOP,
            is_excluded=False,
            checked_at=datetime.now(timezone.utc),
        )
        with patch("main._router.check", return_value=mock_status):
            resp = api_client.get("/check/player-1?jurisdiction=GB")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_excluded"] is False
        assert data["registry"] == "gamstop"

    def test_check_excluded_player(self, api_client):
        from models import ExclusionStatus
        mock_status = ExclusionStatus(
            player_id="excluded-player",
            registry=Registry.ROFUS,
            is_excluded=True,
            checked_at=datetime.now(timezone.utc),
            exclusion_period="2026-01-01",
        )
        with patch("main._router.check", return_value=mock_status):
            resp = api_client.get("/check/excluded-player?jurisdiction=DK")
        assert resp.status_code == 200
        assert resp.json()["is_excluded"] is True
        assert resp.json()["exclusion_period"] == "2026-01-01"

    def test_check_invalid_jurisdiction_returns_422(self, api_client):
        resp = api_client.get("/check/player-1?jurisdiction=XX")
        assert resp.status_code == 422

    def test_register_br_returns_201(self, api_client):
        with patch("main._router.register",
                   return_value={"registration_id": "uuid-1",
                                 "effective_from": "2024-01-01"}):
            resp = api_client.post("/register", json={
                "player_id":    "123.456.789-09",
                "jurisdiction": "BR",
                "duration":     "permanent",
            })
        assert resp.status_code == 201

    def test_register_gb_returns_501(self, api_client):
        with patch("main._router.register",
                   side_effect=NotImplementedError("Not supported")):
            resp = api_client.post("/register", json={
                "player_id":    "test",
                "jurisdiction": "GB",
            })
        assert resp.status_code == 501

    def test_revoke_br_returns_200(self, api_client):
        with patch("main._router.revoke",
                   return_value={"revoked": True, "effective_from": "2024-06-01"}):
            resp = api_client.post("/revoke", json={
                "player_id":    "123.456.789-09",
                "jurisdiction": "BR",
            })
        assert resp.status_code == 200

    def test_status_endpoint_returns_registry_name(self, api_client):
        from models import ExclusionStatus
        mock_status = ExclusionStatus(
            player_id="probe",
            registry=Registry.SPELPAUS,
            is_excluded=False,
            checked_at=datetime.now(timezone.utc),
        )
        with patch("main._router.check", return_value=mock_status):
            resp = api_client.get("/status/SE")
        assert resp.status_code == 200
        data = resp.json()
        assert data["registry"] == "spelpaus"

    def test_status_unknown_jurisdiction_returns_400(self, api_client):
        resp = api_client.get("/status/ZZ")
        assert resp.status_code == 400
