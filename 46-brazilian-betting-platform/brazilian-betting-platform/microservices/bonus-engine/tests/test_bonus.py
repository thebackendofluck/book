# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Bonus Engine test suite — covers all major paths plus the auth,
wagering-integrity, and anti-abuse controls added in the security hardening
pass."""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import cast

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Test JWT secret — must be set before any request exercises auth.py's
# `_secret()`, which reads this env var on every call.
# ---------------------------------------------------------------------------
TEST_JWT_SECRET = "test-only-secret-do-not-use-in-prod"
os.environ["BONUS_JWT_SECRET"] = TEST_JWT_SECRET

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# Each chapter-46 microservice (aml-fraud, bonus-engine, pam,
# responsible-gaming, ...) ships its own top-level `models.py` and
# `main.py`, so whichever one pytest imports first wins
# `sys.modules["models"]` and `sys.modules["main"]` for the session.
# We explicitly install this service's copies right here at test-module
# import time -- doing it in a conftest.py isn't enough because pytest
# loads all conftests up front, before importing any test file.
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


# `models` first, then siblings that do `from models import ...`
# at module load time.
_THIS_SERVICE_MODULES: list[tuple[str, str]] = [
    ("models", "models.py"),
    ("database", "database.py"),
    ("auth", "auth.py"),
    ("verification", "verification.py"),
    ("wagering", "wagering.py"),
    ("sigap_bonus", "sigap_bonus.py"),
    ("main", "main.py"),
]

for _mod_name, _file_name in _THIS_SERVICE_MODULES:
    _load_local_module(_mod_name, _file_name)


@pytest.fixture(autouse=True, scope="module")
def _pin_local_modules():
    """Re-install this service's local modules before any other
    module-scoped fixture runs -- see the matching comment in
    aml-fraud/tests/test_aml.py for the rationale."""
    for _mod_name, _file_name in _THIS_SERVICE_MODULES:
        _load_local_module(_mod_name, _file_name)
    yield

from main import app, _campaigns, _bonuses, _requirements
from models import (
    Bonus,
    BonusStatus,
    BonusType,
    Campaign,
    CampaignStatus,
    FreeBet,
    WageringContribution,
    WageringRequirement,
)
from sigap_bonus import SigapBonusTracker
from verification import (
    DepositVerificationProvider,
    SettledBet,
    SettledBetNotFoundError,
    SettledBetOwnershipError,
    SettledBetProvider,
    get_deposit_provider,
    get_settled_bet_provider,
)
from wagering import WageringEngine

client = TestClient(app)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _token(cpf: str | None = None, role: str | None = None) -> str:
    payload: dict = {}
    if cpf is not None:
        payload["cpf"] = cpf
    if role is not None:
        payload["role"] = role
    return pyjwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


def _auth(cpf: str | None = None, role: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(cpf=cpf, role=role)}"}


PLAYER = lambda cpf: _auth(cpf=cpf, role="player")
OPERATOR = _auth(role="operator")


# ── Fake external-truth providers ────────────────────────────────────────────

class _FakeSettledBetProvider(SettledBetProvider):
    def __init__(self, bets: dict[str, SettledBet] | None = None) -> None:
        self.bets = bets or {}

    async def get_settled_bet(self, bet_id: str, cpf: str) -> SettledBet:
        bet = self.bets.get(bet_id)
        if not bet:
            raise SettledBetNotFoundError(bet_id)
        if bet.cpf != cpf:
            raise SettledBetOwnershipError(bet_id)
        return bet


class _FakeDepositProvider(DepositVerificationProvider):
    def __init__(self, totals: dict[str, Decimal] | None = None) -> None:
        self.totals = totals or {}

    async def total_confirmed_deposits(self, cpf: str) -> Decimal:
        return self.totals.get(cpf, Decimal("0"))


@pytest.fixture(autouse=True)
def _default_provider_overrides():
    """Default fakes so tests that don't care about deposits/settlement
    don't need to wire anything. Individual tests override further via
    app.dependency_overrides and this fixture resets everything after."""
    app.dependency_overrides[get_settled_bet_provider] = lambda: _FakeSettledBetProvider()
    app.dependency_overrides[get_deposit_provider] = lambda: _FakeDepositProvider()
    yield
    app.dependency_overrides.pop(get_settled_bet_provider, None)
    app.dependency_overrides.pop(get_deposit_provider, None)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _active_campaign(
    bonus_type: BonusType = BonusType.WELCOME,
    amount: Decimal = Decimal("100"),
    wagering_multiplier: int = 10,
    sigap_deductible: bool = False,
    eligible_cpfs: list[str] | None = None,
    min_deposit: Decimal = Decimal("0"),
) -> Campaign:
    now = datetime.now(timezone.utc)
    c = Campaign(
        name                = f"Test {bonus_type.value} {uuid.uuid4().hex[:6]}",
        bonus_type          = bonus_type,
        status              = CampaignStatus.ACTIVE,
        bonus_amount        = amount,
        wagering_multiplier = wagering_multiplier,
        max_claims          = 100,
        valid_from          = now,
        valid_until         = now + timedelta(days=30),
        sigap_deductible    = sigap_deductible,
        eligible_cpfs       = eligible_cpfs or [],
        min_deposit         = min_deposit,
    )
    _campaigns[str(c.campaign_id)] = c
    return c


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_200(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "UP"

    def test_health_contains_service_name(self):
        r = client.get("/health")
        assert r.json()["service"] == "bonus-engine"

    def test_health_requires_no_auth(self):
        # Health checks must stay reachable without a token.
        r = client.get("/health")
        assert r.status_code == 200


# ── Campaign creation ─────────────────────────────────────────────────────────

class TestCreateCampaign:
    def test_create_welcome_campaign(self):
        payload = {
            "name": "Welcome Bonus",
            "bonus_type": "WELCOME",
            "bonus_amount": "200.00",
            "wagering_multiplier": 5,
            "max_claims": 500,
            "min_deposit": "50.00",
            "valid_days": 14,
        }
        r = client.post("/bonuses/create", json=payload, headers=OPERATOR)
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Welcome Bonus"
        assert data["status"] == "ACTIVE"

    def test_create_free_bet_campaign_with_sigap(self):
        payload = {
            "name": "Free Bet Weekend",
            "bonus_type": "FREE_BET",
            "bonus_amount": "50.00",
            "wagering_multiplier": 1,
            "valid_days": 7,
            "sigap_deductible": True,
            "sigap_category": "FREE_BET",
        }
        r = client.post("/bonuses/create", json=payload, headers=OPERATOR)
        assert r.status_code == 201
        assert r.json()["sigap_deductible"] is True

    def test_create_campaign_requires_name(self):
        r = client.post("/bonuses/create", json={
            "bonus_type": "RELOAD",
            "bonus_amount": "10.00",
            "valid_days": 7,
        }, headers=OPERATOR)
        assert r.status_code == 422

    def test_create_campaign_without_token_returns_401(self):
        r = client.post("/bonuses/create", json={
            "name": "No Auth", "bonus_type": "RELOAD",
            "bonus_amount": "10.00", "valid_days": 7,
        })
        assert r.status_code == 401

    def test_create_campaign_with_player_role_returns_403(self):
        r = client.post("/bonuses/create", json={
            "name": "Player Role", "bonus_type": "RELOAD",
            "bonus_amount": "10.00", "valid_days": 7,
        }, headers=PLAYER("11122233344"))
        assert r.status_code == 403


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_claim_without_token_returns_401(self):
        c = _active_campaign()
        r = client.post(f"/bonuses/claim/12312312312?campaign_id={c.campaign_id}")
        assert r.status_code == 401

    def test_claim_with_garbage_token_returns_401(self):
        c = _active_campaign()
        r = client.post(
            f"/bonuses/claim/12312312312?campaign_id={c.campaign_id}",
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )
        assert r.status_code == 401

    def test_claim_with_mismatched_cpf_returns_403(self):
        c = _active_campaign()
        r = client.post(
            f"/bonuses/claim/12312312312?campaign_id={c.campaign_id}",
            headers=PLAYER("99999999999"),
        )
        assert r.status_code == 403

    def test_operator_can_claim_on_behalf_of_any_cpf(self):
        c = _active_campaign()
        r = client.post(
            f"/bonuses/claim/12312312399?campaign_id={c.campaign_id}",
            headers=OPERATOR,
        )
        assert r.status_code == 200

    def test_get_bonuses_requires_cpf_match(self):
        r = client.get("/bonuses/12312312312", headers=PLAYER("00000000000"))
        assert r.status_code == 403

    def test_get_bonuses_own_cpf_allowed(self):
        r = client.get("/bonuses/12312312312", headers=PLAYER("12312312312"))
        assert r.status_code == 200

    def test_wagering_check_rejects_player_role(self):
        r = client.post(
            "/bonuses/wagering-check/12312312312",
            json={"bet_id": "bet-1"},
            headers=PLAYER("12312312312"),
        )
        assert r.status_code == 403

    def test_sigap_report_rejects_player_role(self):
        r = client.get("/bonuses/sigap-report?period=2026-01", headers=PLAYER("12312312312"))
        assert r.status_code == 403

    def test_sigap_report_allows_operator(self):
        r = client.get("/bonuses/sigap-report?period=2026-01", headers=OPERATOR)
        assert r.status_code == 200


# ── Claiming ──────────────────────────────────────────────────────────────────

class TestClaimBonus:
    def test_claim_valid_campaign(self):
        c = _active_campaign()
        cpf = "12345678909"
        r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r.status_code == 200
        data = r.json()
        assert data["cpf"] == cpf
        assert data["status"] == "ACTIVE"
        assert Decimal(data["wagering_required"]) == c.bonus_amount * c.wagering_multiplier

    def test_claim_nonexistent_campaign_returns_404(self):
        cpf = "12345678909"
        r = client.post(f"/bonuses/claim/{cpf}?campaign_id={uuid.uuid4()}", headers=PLAYER(cpf))
        assert r.status_code == 404

    def test_duplicate_claim_returns_409(self):
        c = _active_campaign()
        cpf = "99988877766"
        client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        r2 = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r2.status_code == 409

    def test_claim_expired_campaign_returns_409(self):
        now = datetime.now(timezone.utc)
        c = Campaign(
            name="Expired", bonus_type=BonusType.RELOAD,
            status=CampaignStatus.ACTIVE, bonus_amount=Decimal("10"),
            wagering_multiplier=5, max_claims=100,
            valid_from=now - timedelta(days=10),
            valid_until=now - timedelta(days=1),
        )
        _campaigns[str(c.campaign_id)] = c
        cpf = "55544433322"
        r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r.status_code == 409

    def test_claim_maxed_campaign_returns_409(self):
        c = _active_campaign()
        c.max_claims = 0
        cpf = "11100011100"
        r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r.status_code == 409

    def test_claim_ineligible_cpf_returns_403(self):
        c = _active_campaign(eligible_cpfs=["11111111111"])
        cpf = "22222222222"
        r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r.status_code == 403

    def test_claim_eligible_cpf_allowed(self):
        cpf = "11111111112"
        c = _active_campaign(eligible_cpfs=[cpf])
        r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r.status_code == 200

    def test_claim_forfeit_reclaim_same_campaign_returns_409(self):
        c = _active_campaign()
        cpf = "44455566677"
        claim_r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        bonus_id = claim_r.json()["bonus_id"]
        client.post(f"/bonuses/{bonus_id}/forfeit", headers=PLAYER(cpf))
        r2 = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r2.status_code == 409

    def test_second_welcome_campaign_blocked_after_first(self):
        cpf = "77788899900"
        c1 = _active_campaign(bonus_type=BonusType.WELCOME)
        c2 = _active_campaign(bonus_type=BonusType.WELCOME)
        r1 = client.post(f"/bonuses/claim/{cpf}?campaign_id={c1.campaign_id}", headers=PLAYER(cpf))
        assert r1.status_code == 200
        r2 = client.post(f"/bonuses/claim/{cpf}?campaign_id={c2.campaign_id}", headers=PLAYER(cpf))
        assert r2.status_code == 409

    def test_reload_bonus_type_may_recur_across_campaigns(self):
        # RELOAD is not in ONE_PER_PLAYER_BONUS_TYPES — a player may hold
        # reload bonuses from two different campaigns.
        cpf = "77788899911"
        c1 = _active_campaign(bonus_type=BonusType.RELOAD)
        c2 = _active_campaign(bonus_type=BonusType.RELOAD)
        r1 = client.post(f"/bonuses/claim/{cpf}?campaign_id={c1.campaign_id}", headers=PLAYER(cpf))
        assert r1.status_code == 200
        r2 = client.post(f"/bonuses/claim/{cpf}?campaign_id={c2.campaign_id}", headers=PLAYER(cpf))
        assert r2.status_code == 200

    def test_claim_below_min_deposit_returns_403(self):
        cpf = "33344455566"
        c = _active_campaign(min_deposit=Decimal("100"))
        app.dependency_overrides[get_deposit_provider] = lambda: _FakeDepositProvider(
            {cpf: Decimal("50")}
        )
        r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r.status_code == 403

    def test_claim_meeting_min_deposit_allowed(self):
        cpf = "33344455577"
        c = _active_campaign(min_deposit=Decimal("100"))
        app.dependency_overrides[get_deposit_provider] = lambda: _FakeDepositProvider(
            {cpf: Decimal("150")}
        )
        r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r.status_code == 200

    def test_shared_device_blocks_second_cpf_same_campaign(self):
        c = _active_campaign()
        cpf_a, cpf_b = "10120120120", "10120120121"
        r1 = client.post(
            f"/bonuses/claim/{cpf_a}?campaign_id={c.campaign_id}&device_id=dev-shared-1",
            headers=PLAYER(cpf_a),
        )
        assert r1.status_code == 200
        r2 = client.post(
            f"/bonuses/claim/{cpf_b}?campaign_id={c.campaign_id}&device_id=dev-shared-1",
            headers=PLAYER(cpf_b),
        )
        assert r2.status_code == 409

    def test_different_device_ids_do_not_collide(self):
        c = _active_campaign()
        cpf_a, cpf_b = "10120120222", "10120120223"
        r1 = client.post(
            f"/bonuses/claim/{cpf_a}?campaign_id={c.campaign_id}&device_id=dev-a",
            headers=PLAYER(cpf_a),
        )
        assert r1.status_code == 200
        r2 = client.post(
            f"/bonuses/claim/{cpf_b}?campaign_id={c.campaign_id}&device_id=dev-b",
            headers=PLAYER(cpf_b),
        )
        assert r2.status_code == 200


# ── Active bonuses ────────────────────────────────────────────────────────────

class TestGetBonuses:
    def test_returns_empty_list_for_unknown_player(self):
        cpf = "00000000000"
        r = client.get(f"/bonuses/{cpf}", headers=PLAYER(cpf))
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_claimed_bonus(self):
        c = _active_campaign()
        cpf = "77766655544"
        client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        r = client.get(f"/bonuses/{cpf}", headers=PLAYER(cpf))
        assert r.status_code == 200
        bonuses = r.json()
        assert len(bonuses) >= 1
        assert bonuses[0]["status"] == "ACTIVE"


# ── Forfeit ───────────────────────────────────────────────────────────────────

class TestForfeitBonus:
    def test_forfeit_active_bonus(self):
        c = _active_campaign()
        cpf = "33322211100"
        claim_r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        bonus_id = claim_r.json()["bonus_id"]

        r = client.post(f"/bonuses/{bonus_id}/forfeit", headers=PLAYER(cpf))
        assert r.status_code == 200
        assert r.json()["status"] == "FORFEITED"

    def test_forfeit_nonexistent_bonus_returns_404(self):
        r = client.post(f"/bonuses/{uuid.uuid4()}/forfeit", headers=OPERATOR)
        assert r.status_code == 404

    def test_forfeit_already_forfeited_returns_409(self):
        c = _active_campaign()
        cpf = "44433322211"
        claim_r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        bonus_id = claim_r.json()["bonus_id"]
        client.post(f"/bonuses/{bonus_id}/forfeit", headers=PLAYER(cpf))
        r2 = client.post(f"/bonuses/{bonus_id}/forfeit", headers=PLAYER(cpf))
        assert r2.status_code == 409

    def test_forfeit_by_other_cpf_returns_403(self):
        c = _active_campaign()
        cpf = "44433322299"
        claim_r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        bonus_id = claim_r.json()["bonus_id"]
        r = client.post(f"/bonuses/{bonus_id}/forfeit", headers=PLAYER("00011122233"))
        assert r.status_code == 403

    def test_forfeit_by_operator_allowed(self):
        c = _active_campaign()
        cpf = "44433322288"
        claim_r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        bonus_id = claim_r.json()["bonus_id"]
        r = client.post(f"/bonuses/{bonus_id}/forfeit", headers=OPERATOR)
        assert r.status_code == 200


# ── Wagering check endpoint (settlement-driven credit) ────────────────────────

class TestWageringCheckEndpoint:
    def _claim(self, cpf: str, amount=Decimal("100"), mult=2) -> str:
        c = _active_campaign(amount=amount, wagering_multiplier=mult)
        r = client.post(f"/bonuses/claim/{cpf}?campaign_id={c.campaign_id}", headers=PLAYER(cpf))
        assert r.status_code == 200
        return r.json()["bonus_id"]

    def test_credits_progress_from_settled_bet(self):
        cpf = "20120120120"
        self._claim(cpf, amount=Decimal("100"), mult=2)  # needs 200 total
        bet_id = "bet-abc-1"
        app.dependency_overrides[get_settled_bet_provider] = lambda: _FakeSettledBetProvider({
            bet_id: SettledBet(bet_id=bet_id, cpf=cpf, stake=Decimal("100"),
                                bet_type=WageringContribution.SPORTS_PRE_MATCH),
        })
        r = client.post(
            f"/bonuses/wagering-check/{cpf}", json={"bet_id": bet_id}, headers=OPERATOR,
        )
        assert r.status_code == 200
        data = r.json()
        assert Decimal(data["total_wagered"]) == Decimal("100")

    def test_ignores_client_supplied_amount_and_type(self):
        """Even if a caller still sends the old wager_amount/bet_type
        fields, only the settled bet's real stake is credited."""
        cpf = "20120120121"
        self._claim(cpf, amount=Decimal("100"), mult=2)  # needs 200 total
        bet_id = "bet-abc-2"
        app.dependency_overrides[get_settled_bet_provider] = lambda: _FakeSettledBetProvider({
            bet_id: SettledBet(bet_id=bet_id, cpf=cpf, stake=Decimal("5"),
                                bet_type=WageringContribution.CASINO_TABLE),  # 20% weight
        })
        r = client.post(
            f"/bonuses/wagering-check/{cpf}",
            json={
                "bet_id": bet_id,
                "wager_amount": "999999",       # attacker-supplied, must be ignored
                "bet_type": "SPORTS_PRE_MATCH",  # attacker-supplied, must be ignored
            },
            headers=OPERATOR,
        )
        assert r.status_code == 200
        # 5 * 0.20 (CASINO_TABLE weight) = 1, not 999999.
        assert Decimal(r.json()["total_wagered"]) == Decimal("1")

    def test_unknown_bet_id_returns_404(self):
        cpf = "20120120122"
        self._claim(cpf)
        r = client.post(
            f"/bonuses/wagering-check/{cpf}", json={"bet_id": "does-not-exist"}, headers=OPERATOR,
        )
        assert r.status_code == 404

    def test_bet_belonging_to_other_cpf_returns_403(self):
        cpf = "20120120123"
        self._claim(cpf)
        bet_id = "bet-other-cpf"
        app.dependency_overrides[get_settled_bet_provider] = lambda: _FakeSettledBetProvider({
            bet_id: SettledBet(bet_id=bet_id, cpf="99999999999", stake=Decimal("50"),
                                bet_type=WageringContribution.SPORTS_PRE_MATCH),
        })
        r = client.post(
            f"/bonuses/wagering-check/{cpf}", json={"bet_id": bet_id}, headers=OPERATOR,
        )
        assert r.status_code == 403

    def test_replaying_same_bet_id_does_not_double_count(self):
        cpf = "20120120124"
        self._claim(cpf, amount=Decimal("100"), mult=10)  # needs 1000, won't complete
        bet_id = "bet-replay-1"
        app.dependency_overrides[get_settled_bet_provider] = lambda: _FakeSettledBetProvider({
            bet_id: SettledBet(bet_id=bet_id, cpf=cpf, stake=Decimal("50"),
                                bet_type=WageringContribution.SPORTS_PRE_MATCH),
        })
        r1 = client.post(f"/bonuses/wagering-check/{cpf}", json={"bet_id": bet_id}, headers=OPERATOR)
        r2 = client.post(f"/bonuses/wagering-check/{cpf}", json={"bet_id": bet_id}, headers=OPERATOR)
        assert r1.status_code == 200 and r2.status_code == 200
        assert Decimal(r2.json()["total_wagered"]) == Decimal("50")

    def test_no_active_bonus_returns_404(self):
        r = client.post(
            "/bonuses/wagering-check/00000000001", json={"bet_id": "x"}, headers=OPERATOR,
        )
        assert r.status_code == 404


# ── Wagering engine unit tests ────────────────────────────────────────────────

class TestWageringEngine:
    engine = WageringEngine()

    def _make_req(self, bonus_amount=Decimal("100"), mult=10) -> WageringRequirement:
        return self.engine.create_requirement(
            bonus_id=uuid.uuid4(), cpf="TEST",
            bonus_amount=bonus_amount, wagering_multiplier=mult,
        )

    def test_creates_correct_total(self):
        req = self._make_req(Decimal("100"), 10)
        assert req.total_required == Decimal("1000")

    def test_sports_wager_applies_100_percent(self):
        req = self._make_req(Decimal("100"), 2)
        updated = self.engine.apply_wager(req, Decimal("100"), WageringContribution.SPORTS_PRE_MATCH)
        assert updated.total_wagered == Decimal("100")

    def test_casino_table_applies_20_percent(self):
        req = self._make_req(Decimal("100"), 5)
        updated = self.engine.apply_wager(req, Decimal("100"), WageringContribution.CASINO_TABLE)
        assert updated.total_wagered == Decimal("20")

    def test_live_casino_applies_10_percent(self):
        req = self._make_req(Decimal("100"), 5)
        updated = self.engine.apply_wager(req, Decimal("50"), WageringContribution.LIVE_CASINO)
        assert updated.total_wagered == Decimal("5")

    def test_completes_when_requirement_met(self):
        req = self._make_req(Decimal("100"), 1)  # need 100 total
        updated = self.engine.apply_wager(req, Decimal("100"), WageringContribution.SPORTS_PRE_MATCH)
        assert updated.completed is True
        assert updated.remaining == Decimal("0")

    def test_no_further_wagering_after_completion(self):
        req = self._make_req(Decimal("50"), 1)
        completed = self.engine.apply_wager(req, Decimal("50"), WageringContribution.CASINO_SLOTS)
        # Second wager should not change state
        result = self.engine.apply_wager(completed, Decimal("100"), WageringContribution.CASINO_SLOTS)
        assert result.total_wagered == Decimal("50")

    def test_duplicate_bet_id_is_not_applied_twice(self):
        req = self._make_req(Decimal("100"), 10)  # needs 1000
        once = self.engine.apply_wager(
            req, Decimal("50"), WageringContribution.SPORTS_PRE_MATCH, bet_id="dup-1"
        )
        twice = self.engine.apply_wager(
            once, Decimal("50"), WageringContribution.SPORTS_PRE_MATCH, bet_id="dup-1"
        )
        assert twice.total_wagered == Decimal("50")
        assert twice.processed_bet_ids.count("dup-1") == 1

    def test_different_bet_ids_both_apply(self):
        req = self._make_req(Decimal("100"), 10)  # needs 1000
        first = self.engine.apply_wager(
            req, Decimal("50"), WageringContribution.SPORTS_PRE_MATCH, bet_id="a"
        )
        second = self.engine.apply_wager(
            first, Decimal("50"), WageringContribution.SPORTS_PRE_MATCH, bet_id="b"
        )
        assert second.total_wagered == Decimal("100")


# ── SIGAP tracker unit tests ──────────────────────────────────────────────────

class TestSigapTracker:
    def test_deductible_win_free_bet(self):
        tracker = SigapBonusTracker()
        fb = FreeBet(
            bonus_id=uuid.uuid4(), cpf="FB_CPF_01",
            face_value=Decimal("50"), stake_replaced=True,
            valid_until=datetime.now(timezone.utc) + timedelta(days=1),
        )
        tracker.register_free_bet(fb)
        updated = tracker.record_outcome(
            str(fb.free_bet_id), outcome="WIN",
            gross_win=Decimal("150"), bet_type="SPORTS_PRE_MATCH"
        )
        assert updated.deductible_from_ggr is True
        # net win = gross_win(150) - face_value(50) = 100
        assert updated.net_player_win == Decimal("100")

    def test_loss_free_bet_not_deductible(self):
        tracker = SigapBonusTracker()
        fb = FreeBet(
            bonus_id=uuid.uuid4(), cpf="FB_CPF_02",
            face_value=Decimal("30"), stake_replaced=False,
            valid_until=datetime.now(timezone.utc) + timedelta(days=1),
        )
        tracker.register_free_bet(fb)
        updated = tracker.record_outcome(str(fb.free_bet_id), "LOSS", Decimal("0"))
        assert updated.deductible_from_ggr is False

    def test_monthly_report_contains_deductible_records(self):
        tracker = SigapBonusTracker()
        now = datetime.now(timezone.utc)
        fb = FreeBet(
            bonus_id=uuid.uuid4(), cpf="REPORT_CPF",
            face_value=Decimal("25"), stake_replaced=True,
            valid_until=now + timedelta(days=1),
        )
        tracker.register_free_bet(fb)
        tracker.record_outcome(str(fb.free_bet_id), "WIN", Decimal("75"))

        period = now.strftime("%Y-%m")
        report = tracker.generate_monthly_report(period)
        assert report.total_free_bets == 1
        assert Decimal(report.total_deductible) == Decimal("50")
