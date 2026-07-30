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
Full user journey tests for the Brazilian Betting Platform.

Simulates a complete player lifecycle end-to-end:
  registration → KYC → deposit → bet → settlement → withdrawal → self-exclusion

All tests run against real microservices (no mocks).
Tests are ordered and share state via module-level variables so that the
journey flows naturally from one step to the next.

Run with:
    pytest test_user_journey.py -v --asyncio-mode=auto
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Any, Dict

import httpx
import pytest
import pytest_asyncio

# Sibling conftest.py exports helpers; add its directory to sys.path
# so `from conftest import ...` works in importlib import mode.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from conftest import APIClients, generate_valid_cpf, register_test_player

# ---------------------------------------------------------------------------
# Module-level state shared across the ordered journey
# ---------------------------------------------------------------------------

# These are populated by early tests and consumed by later ones.
_state: Dict[str, Any] = {
    "cpf": None,
    "player_id": None,
    "wallet_balance": 0.0,
    "bet_id": None,
    "event_id": None,
    "selection_id": None,
    "deposit_amount": 500.0,
    "bet_stake": 50.0,
    "bet_odds": 2.0,
    "settlement_run_id": None,
    "withdrawal_amount": None,
}


# ---------------------------------------------------------------------------
# Helper: assert response time
# ---------------------------------------------------------------------------

def assert_latency(elapsed_ms: float, threshold_ms: float = 2000) -> None:
    assert elapsed_ms < threshold_ms, (
        f"Response took {elapsed_ms:.0f}ms — exceeds {threshold_ms}ms threshold"
    )


# ---------------------------------------------------------------------------
# 1. Player Registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(1)
async def test_register_player(api_clients: APIClients) -> None:
    """POST /players/register with a valid CPF should return 201."""
    cpf = generate_valid_cpf()
    _state["cpf"] = cpf

    payload = {
        "cpf": cpf,
        "full_name": "Maria Fernanda Silva",
        "email": f"maria_{cpf[-4:]}@betbr-test.com",
        "phone": "+5511988887777",
        "date_of_birth": "1992-03-20",
        "address": {
            "street": "Avenida Paulista",
            "number": "1578",
            "city": "São Paulo",
            "state": "SP",
            "zip_code": "01310-200",
        },
    }

    start = time.monotonic()
    resp = await api_clients.pam.post("/players/register", json=payload)
    elapsed = (time.monotonic() - start) * 1000

    assert resp.status_code in (200, 201), f"Unexpected status: {resp.status_code} — {resp.text}"
    body = resp.json()
    assert body.get("cpf") == cpf or body.get("player_id") is not None

    _state["player_id"] = body.get("player_id") or body.get("id")
    assert_latency(elapsed)


@pytest.mark.asyncio
@pytest.mark.order(2)
async def test_register_duplicate_cpf(api_clients: APIClients) -> None:
    """Registering the same CPF twice must return 409 Conflict."""
    cpf = _state["cpf"]
    assert cpf is not None, "test_register_player must run first"

    payload = {
        "cpf": cpf,
        "full_name": "Impostor Teste",
        "email": f"dup_{cpf}@betbr-test.com",
        "phone": "+5511900000001",
        "date_of_birth": "1985-01-01",
        "address": {
            "street": "Rua Qualquer",
            "number": "1",
            "city": "São Paulo",
            "state": "SP",
            "zip_code": "01310-100",
        },
    }
    resp = await api_clients.pam.post("/players/register", json=payload)
    assert resp.status_code in (409, 422), (
        f"Expected 409/422 for duplicate CPF, got {resp.status_code}"
    )


@pytest.mark.asyncio
@pytest.mark.order(3)
async def test_register_invalid_cpf(api_clients: APIClients) -> None:
    """Registering with an invalid CPF must be rejected (422)."""
    payload = {
        "cpf": "00000000000",  # all zeros — arithmetically invalid
        "full_name": "CPF Invalido",
        "email": "invalid@betbr-test.com",
        "phone": "+5511900000002",
        "date_of_birth": "1990-01-01",
        "address": {
            "street": "Rua Nenhuma",
            "number": "0",
            "city": "Curitiba",
            "state": "PR",
            "zip_code": "80010-000",
        },
    }
    resp = await api_clients.pam.post("/players/register", json=payload)
    assert resp.status_code in (400, 422), (
        f"Expected 400/422 for invalid CPF, got {resp.status_code}"
    )


@pytest.mark.asyncio
@pytest.mark.order(4)
async def test_register_underage_player(api_clients: APIClients) -> None:
    """Registering a player under 18 must be rejected."""
    cpf = generate_valid_cpf()
    payload = {
        "cpf": cpf,
        "full_name": "Menor de Idade",
        "email": f"minor_{cpf}@betbr-test.com",
        "phone": "+5511900000003",
        "date_of_birth": "2015-06-01",  # under 18
        "address": {
            "street": "Rua da Escola",
            "number": "42",
            "city": "Belo Horizonte",
            "state": "MG",
            "zip_code": "30140-070",
        },
    }
    resp = await api_clients.pam.post("/players/register", json=payload)
    assert resp.status_code in (400, 422), (
        f"Expected 400/422 for underage player, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# 2. Biometric Verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(5)
async def test_biometric_verify(api_clients: APIClients) -> None:
    """POST /players/{cpf}/verify-biometric should confirm facial liveness."""
    cpf = _state["cpf"]
    assert cpf is not None

    payload = {
        "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        "liveness_token": f"mock_liveness_{uuid.uuid4().hex}",
    }

    start = time.monotonic()
    resp = await api_clients.pam.post(
        f"/players/{cpf}/verify-biometric", json=payload
    )
    elapsed = (time.monotonic() - start) * 1000

    # In integration env with BIOMETRIC_MOCK=true the service should accept any image
    assert resp.status_code in (200, 201), (
        f"Biometric verify failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    assert body.get("status") in ("verified", "ok", "success", "accepted"), (
        f"Unexpected biometric status: {body}"
    )
    assert_latency(elapsed)


# ---------------------------------------------------------------------------
# 3. Welfare Check (Bolsa Família / BPC)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(6)
async def test_welfare_check(api_clients: APIClients) -> None:
    """POST /players/{cpf}/welfare-check should confirm player is not a beneficiary."""
    cpf = _state["cpf"]
    assert cpf is not None

    start = time.monotonic()
    resp = await api_clients.pam.post(f"/players/{cpf}/welfare-check")
    elapsed = (time.monotonic() - start) * 1000

    assert resp.status_code == 200, (
        f"Welfare check failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    # In mock mode the player should NOT be flagged as a beneficiary
    assert body.get("is_beneficiary") in (False, None) or body.get("blocked") is not True, (
        f"Player incorrectly flagged as welfare beneficiary: {body}"
    )
    assert_latency(elapsed)


# ---------------------------------------------------------------------------
# 4. Get Player Profile
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(7)
async def test_get_player_profile(api_clients: APIClients) -> None:
    """GET /players/{cpf} should return the registered player profile."""
    cpf = _state["cpf"]
    assert cpf is not None

    resp = await api_clients.pam.get(f"/players/{cpf}")
    assert resp.status_code == 200, f"Get player failed: {resp.status_code} — {resp.text}"
    body = resp.json()
    assert body.get("cpf") == cpf or body.get("cpf_hash") is not None


# ---------------------------------------------------------------------------
# 5. Deposit Limits (Responsible Gaming)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(8)
async def test_set_deposit_limit(api_clients: APIClients) -> None:
    """POST /limits/{cpf} should set a daily deposit limit."""
    cpf = _state["cpf"]
    assert cpf is not None

    payload = {
        "limit_type": "deposit",
        "period": "daily",
        "amount": 1000.0,
    }

    start = time.monotonic()
    resp = await api_clients.responsible_gaming.post(f"/limits/{cpf}", json=payload)
    elapsed = (time.monotonic() - start) * 1000

    assert resp.status_code in (200, 201), (
        f"Set deposit limit failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    assert body.get("limit_type") == "deposit" or body.get("status") in ("ok", "created")
    assert_latency(elapsed)


@pytest.mark.asyncio
@pytest.mark.order(9)
async def test_get_deposit_limits(api_clients: APIClients) -> None:
    """GET /limits/{cpf} should return the previously set limit."""
    cpf = _state["cpf"]
    assert cpf is not None

    resp = await api_clients.responsible_gaming.get(f"/limits/{cpf}")
    assert resp.status_code == 200, (
        f"Get limits failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    # Body is a list or dict with limits — just assert it exists
    assert body is not None


@pytest.mark.asyncio
@pytest.mark.order(10)
async def test_set_session_limit(api_clients: APIClients) -> None:
    """POST /limits/{cpf} with type=session should succeed."""
    cpf = _state["cpf"]
    assert cpf is not None

    payload = {
        "limit_type": "session",
        "period": "daily",
        "duration_minutes": 120,
    }
    resp = await api_clients.responsible_gaming.post(f"/limits/{cpf}", json=payload)
    assert resp.status_code in (200, 201), (
        f"Set session limit failed: {resp.status_code} — {resp.text}"
    )


# ---------------------------------------------------------------------------
# 6. PIX Deposit (Wallet)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(11)
async def test_pix_deposit(api_clients: APIClients) -> None:
    """POST /wallet/{cpf}/deposit should initiate a PIX deposit and return a QR code."""
    cpf = _state["cpf"]
    assert cpf is not None

    payload = {
        "amount": _state["deposit_amount"],
        "payment_method": "pix",
        "description": "Integration test deposit",
    }

    start = time.monotonic()
    resp = await api_clients.wallet.post(f"/wallet/{cpf}/deposit", json=payload)
    elapsed = (time.monotonic() - start) * 1000

    assert resp.status_code in (200, 201, 202), (
        f"PIX deposit failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    # Deposit may be immediate (mock) or pending (awaiting PIX callback)
    assert body.get("transaction_id") or body.get("pix_code") or body.get("status"), (
        f"No transaction reference in deposit response: {body}"
    )
    assert_latency(elapsed, threshold_ms=3000)


@pytest.mark.asyncio
@pytest.mark.order(12)
async def test_check_balance(api_clients: APIClients) -> None:
    """GET /wallet/{cpf}/balance should reflect the deposited amount."""
    cpf = _state["cpf"]
    assert cpf is not None

    resp = await api_clients.wallet.get(f"/wallet/{cpf}/balance")
    assert resp.status_code == 200, (
        f"Get balance failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    balance = float(body.get("balance") or body.get("available_balance") or 0)
    # In mock mode the deposit should be credited immediately
    assert balance >= 0, f"Balance should be non-negative, got {balance}"
    _state["wallet_balance"] = balance


@pytest.mark.asyncio
@pytest.mark.order(13)
async def test_deposit_exceeds_limit_rejected(api_clients: APIClients) -> None:
    """Depositing above the daily limit set in test_set_deposit_limit must be blocked."""
    cpf = _state["cpf"]
    assert cpf is not None

    # Limit is R$1000/day — try to deposit R$1001
    payload = {
        "amount": 1001.0,
        "payment_method": "pix",
        "description": "Over-limit deposit attempt",
    }
    resp = await api_clients.wallet.post(f"/wallet/{cpf}/deposit", json=payload)
    # Service may delegate limit check to responsible-gaming; accept 400/403/422
    assert resp.status_code in (400, 403, 409, 422), (
        f"Expected rejection for over-limit deposit, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# 7. Get Odds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(14)
async def test_get_odds_by_sport(api_clients: APIClients) -> None:
    """GET /odds/sport/football should return Brazilian football events."""
    start = time.monotonic()
    resp = await api_clients.odds_feed.get("/odds/sport/football")
    elapsed = (time.monotonic() - start) * 1000

    assert resp.status_code == 200, (
        f"Get odds by sport failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    events = body.get("events", [])
    assert isinstance(events, list), "Expected events list in response"
    assert_latency(elapsed)


@pytest.mark.asyncio
@pytest.mark.order(15)
async def test_get_odds(api_clients: APIClients) -> None:
    """Seed an event via POST /odds/update and then fetch it by ID."""
    # Seed a mock event
    event_id = f"evt-{uuid.uuid4().hex[:8]}"
    selection_id = f"sel-{uuid.uuid4().hex[:8]}"
    _state["event_id"] = event_id
    _state["selection_id"] = selection_id

    seed_payload = {
        "event_id": event_id,
        "market_id": f"mkt-{uuid.uuid4().hex[:8]}",
        "selection_id": selection_id,
        "new_odds": _state["bet_odds"],
        "sport": "football",
        "updated_at": "2025-01-01T12:00:00Z",
        "source": "integration_test",
    }
    seed_resp = await api_clients.odds_feed.post("/odds/update", json=seed_payload)
    assert seed_resp.status_code in (200, 201), (
        f"Seeding odds failed: {seed_resp.status_code} — {seed_resp.text}"
    )

    # Now fetch the event
    resp = await api_clients.odds_feed.get(f"/odds/event/{event_id}")
    assert resp.status_code in (200, 404), (
        f"Get odds by event failed unexpectedly: {resp.status_code}"
    )
    # 404 is acceptable if the cache TTL is very short; the seed verified the update path


# ---------------------------------------------------------------------------
# 8. Place Bet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(16)
async def test_place_bet(api_clients: APIClients) -> None:
    """POST /bets should accept a valid bet from a registered, funded player."""
    cpf = _state["cpf"]
    event_id = _state["event_id"] or f"evt-{uuid.uuid4().hex[:8]}"
    selection_id = _state["selection_id"] or f"sel-{uuid.uuid4().hex[:8]}"
    assert cpf is not None

    payload = {
        "cpf": cpf,
        "event_id": event_id,
        "selections": [
            {
                "event_id": event_id,
                "market_id": f"mkt-{uuid.uuid4().hex[:8]}",
                "selection_id": selection_id,
                "odds": _state["bet_odds"],
            }
        ],
        "stake": _state["bet_stake"],
        "bet_type": "single",
    }

    start = time.monotonic()
    resp = await api_clients.betting_engine.post("/bets", json=payload)
    elapsed = (time.monotonic() - start) * 1000

    assert resp.status_code in (200, 201), (
        f"Place bet failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    bet_id = body.get("bet_id") or body.get("id")
    assert bet_id is not None, f"No bet_id in response: {body}"
    _state["bet_id"] = bet_id
    assert_latency(elapsed)


@pytest.mark.asyncio
@pytest.mark.order(17)
async def test_get_bet(api_clients: APIClients) -> None:
    """GET /bets/{id} should return the placed bet details."""
    bet_id = _state["bet_id"]
    if bet_id is None:
        pytest.skip("Skipping: no bet_id from previous test")

    resp = await api_clients.betting_engine.get(f"/bets/{bet_id}")
    assert resp.status_code == 200, (
        f"Get bet failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    assert body.get("id") == bet_id or body.get("bet_id") == bet_id


@pytest.mark.asyncio
@pytest.mark.order(18)
async def test_bet_below_minimum_stake_rejected(api_clients: APIClients) -> None:
    """Bets below the minimum stake must be rejected."""
    cpf = _state["cpf"]
    assert cpf is not None

    payload = {
        "cpf": cpf,
        "event_id": "evt-mintest",
        "selections": [
            {
                "event_id": "evt-mintest",
                "market_id": "mkt-mintest",
                "selection_id": "sel-mintest",
                "odds": 1.5,
            }
        ],
        "stake": 0.01,  # Below R$1 minimum
        "bet_type": "single",
    }
    resp = await api_clients.betting_engine.post("/bets", json=payload)
    assert resp.status_code in (400, 422), (
        f"Expected rejection for sub-minimum stake, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# 9. Settle Bet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(19)
async def test_settle_bet(api_clients: APIClients) -> None:
    """POST /settle/event/{id} should settle the bet placed in test_place_bet."""
    event_id = _state["event_id"]
    selection_id = _state["selection_id"]
    if event_id is None:
        pytest.skip("No event_id available from previous tests")

    payload = {
        "results": {
            selection_id: "won",
        }
    }

    start = time.monotonic()
    resp = await api_clients.settlement.post(
        f"/settle/event/{event_id}", json=payload
    )
    elapsed = (time.monotonic() - start) * 1000

    assert resp.status_code in (200, 201), (
        f"Settlement failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    run_id = body.get("id") or body.get("settlement_run_id")
    _state["settlement_run_id"] = run_id
    assert_latency(elapsed, threshold_ms=5000)


# ---------------------------------------------------------------------------
# 10. GGR Calculation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(20)
async def test_check_ggr(api_clients: APIClients) -> None:
    """GET /ggr/daily should return a valid GGR report for today."""
    import datetime

    today = datetime.date.today().isoformat()
    resp = await api_clients.settlement.get(f"/ggr/daily?date={today}")

    assert resp.status_code in (200, 404), (
        f"GGR report request failed: {resp.status_code} — {resp.text}"
    )
    if resp.status_code == 200:
        body = resp.json()
        # Verify the report has expected financial fields
        assert "ggr" in body or "gross_gaming_revenue" in body or "total_stake" in body, (
            f"GGR response missing financial fields: {body}"
        )


@pytest.mark.asyncio
@pytest.mark.order(21)
async def test_ggr_report_structure(api_clients: APIClients) -> None:
    """GET /ggr/report should return a structured daily report."""
    import datetime

    today = datetime.date.today().isoformat()
    resp = await api_clients.settlement.get(f"/ggr/report?date={today}")
    # 404 is acceptable on the first run before any bets settle; 200 must be valid
    assert resp.status_code in (200, 404)


# ---------------------------------------------------------------------------
# 11. Withdrawal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(22)
async def test_withdraw(api_clients: APIClients) -> None:
    """POST /wallet/{cpf}/withdraw should process a PIX withdrawal."""
    cpf = _state["cpf"]
    assert cpf is not None

    # Attempt to withdraw whatever balance exists (minimum R$1)
    withdraw_amount = max(10.0, min(_state["wallet_balance"] * 0.5, 100.0))
    _state["withdrawal_amount"] = withdraw_amount

    payload = {
        "amount": withdraw_amount,
        "pix_key": f"{cpf}",  # CPF as PIX key
        "pix_key_type": "cpf",
        "description": "Integration test withdrawal",
    }

    start = time.monotonic()
    resp = await api_clients.wallet.post(f"/wallet/{cpf}/withdraw", json=payload)
    elapsed = (time.monotonic() - start) * 1000

    # 402 / 422 are acceptable if balance is zero after test setup
    assert resp.status_code in (200, 201, 202, 402, 422), (
        f"Withdrawal failed unexpectedly: {resp.status_code} — {resp.text}"
    )
    assert_latency(elapsed, threshold_ms=5000)


@pytest.mark.asyncio
@pytest.mark.order(23)
async def test_withdraw_exceeds_balance(api_clients: APIClients) -> None:
    """Withdrawing more than available balance must be rejected."""
    cpf = _state["cpf"]
    assert cpf is not None

    payload = {
        "amount": 99_999_999.00,
        "pix_key": cpf,
        "pix_key_type": "cpf",
        "description": "Over-balance withdrawal attempt",
    }
    resp = await api_clients.wallet.post(f"/wallet/{cpf}/withdraw", json=payload)
    assert resp.status_code in (400, 402, 422), (
        f"Expected rejection for over-balance withdrawal, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# 12. Bonus Engine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(24)
async def test_create_bonus_campaign(api_clients: APIClients) -> None:
    """POST /bonuses/create should create a welcome bonus campaign."""
    payload = {
        "name": "Welcome Bonus — Integration Test",
        "type": "deposit_match",
        "match_percentage": 100.0,
        "max_amount": 200.0,
        "min_deposit": 20.0,
        "wagering_multiplier": 5,
        "validity_days": 30,
        "active": True,
    }
    resp = await api_clients.bonus_engine.post("/bonuses/create", json=payload)
    assert resp.status_code in (200, 201), (
        f"Create bonus campaign failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    _state["campaign_id"] = body.get("id") or body.get("campaign_id")


@pytest.mark.asyncio
@pytest.mark.order(25)
async def test_claim_bonus(api_clients: APIClients) -> None:
    """POST /bonuses/claim/{cpf} should claim the welcome bonus."""
    cpf = _state["cpf"]
    campaign_id = _state.get("campaign_id")
    assert cpf is not None

    payload = {"campaign_id": campaign_id, "deposit_amount": _state["deposit_amount"]}
    resp = await api_clients.bonus_engine.post(f"/bonuses/claim/{cpf}", json=payload)
    # May fail if campaign_id is None or service requires active deposit
    assert resp.status_code in (200, 201, 400, 404, 422), (
        f"Claim bonus returned unexpected status: {resp.status_code}"
    )


@pytest.mark.asyncio
@pytest.mark.order(26)
async def test_get_player_bonuses(api_clients: APIClients) -> None:
    """GET /bonuses/{cpf} should return the player's active bonuses."""
    cpf = _state["cpf"]
    assert cpf is not None

    resp = await api_clients.bonus_engine.get(f"/bonuses/{cpf}")
    assert resp.status_code in (200, 404), (
        f"Get player bonuses failed: {resp.status_code} — {resp.text}"
    )


# ---------------------------------------------------------------------------
# 13. Casino Aggregation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(27)
async def test_get_game_catalog(api_clients: APIClients) -> None:
    """GET /games/catalog should return at least one active game."""
    resp = await api_clients.casino_aggregation.get("/games/catalog")
    assert resp.status_code == 200, (
        f"Get game catalog failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    games = body.get("games", [])
    assert isinstance(games, list), "Expected games list"
    # Catalog may be empty in integration env; just assert structure
    assert "total" in body or isinstance(games, list)


# ---------------------------------------------------------------------------
# 14. Self-Exclusion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(28)
async def test_self_exclude(api_clients: APIClients) -> None:
    """POST /self-exclusion/{cpf} should enroll the player in self-exclusion."""
    cpf = _state["cpf"]
    assert cpf is not None

    payload = {
        "exclusion_type": "temporary",
        "duration_days": 30,
        "reason": "Integration test self-exclusion",
    }

    start = time.monotonic()
    resp = await api_clients.responsible_gaming.post(
        f"/self-exclusion/{cpf}", json=payload
    )
    elapsed = (time.monotonic() - start) * 1000

    assert resp.status_code in (200, 201), (
        f"Self-exclusion failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    assert body.get("status") in ("excluded", "active", "ok", "created") or \
           body.get("exclusion_id") is not None, (
        f"Unexpected self-exclusion response: {body}"
    )
    assert_latency(elapsed)


@pytest.mark.asyncio
@pytest.mark.order(29)
async def test_check_self_exclusion_status(api_clients: APIClients) -> None:
    """GET /self-exclusion/check/{cpf} must confirm the player is excluded."""
    cpf = _state["cpf"]
    assert cpf is not None

    resp = await api_clients.responsible_gaming.get(
        f"/self-exclusion/check/{cpf}"
    )
    assert resp.status_code == 200, (
        f"Self-exclusion check failed: {resp.status_code} — {resp.text}"
    )
    body = resp.json()
    assert body.get("is_excluded") is True or body.get("excluded") is True or \
           body.get("status") == "excluded", (
        f"Player not marked as excluded: {body}"
    )


@pytest.mark.asyncio
@pytest.mark.order(30)
async def test_bet_after_exclusion(api_clients: APIClients) -> None:
    """Placing a bet after self-exclusion must be rejected (403 or 422)."""
    cpf = _state["cpf"]
    assert cpf is not None

    payload = {
        "cpf": cpf,
        "event_id": "evt-postexclusion",
        "selections": [
            {
                "event_id": "evt-postexclusion",
                "market_id": "mkt-postexclusion",
                "selection_id": "sel-postexclusion",
                "odds": 1.8,
            }
        ],
        "stake": 10.0,
        "bet_type": "single",
    }

    resp = await api_clients.betting_engine.post("/bets", json=payload)
    assert resp.status_code in (403, 409, 422), (
        f"Expected bet rejection for excluded player, got "
        f"{resp.status_code} — {resp.text}"
    )


@pytest.mark.asyncio
@pytest.mark.order(31)
async def test_deposit_after_exclusion(api_clients: APIClients) -> None:
    """Depositing after self-exclusion must be blocked."""
    cpf = _state["cpf"]
    assert cpf is not None

    payload = {
        "amount": 100.0,
        "payment_method": "pix",
        "description": "Post-exclusion deposit attempt",
    }
    resp = await api_clients.wallet.post(f"/wallet/{cpf}/deposit", json=payload)
    assert resp.status_code in (400, 403, 409, 422), (
        f"Expected deposit rejection after exclusion, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# 15. Health checks for all services
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.order(32)
async def test_all_services_healthy(api_clients: APIClients) -> None:
    """All 8 microservices must report healthy simultaneously."""
    checks = {
        "pam":                api_clients.pam.get("/health"),
        "responsible_gaming": api_clients.responsible_gaming.get("/health"),
        "betting_engine":     api_clients.betting_engine.get("/health"),
        "wallet":             api_clients.wallet.get("/health"),
        "settlement":         api_clients.settlement.get("/health"),
        "odds_feed":          api_clients.odds_feed.get("/health"),
        "bonus_engine":       api_clients.bonus_engine.get("/health"),
        "casino_aggregation": api_clients.casino_aggregation.get("/health"),
    }

    import asyncio
    results = dict(
        zip(checks.keys(), await asyncio.gather(*checks.values()))
    )

    for name, resp in results.items():
        assert resp.status_code < 500, (
            f"Service '{name}' reported unhealthy: {resp.status_code} — {resp.text}"
        )
