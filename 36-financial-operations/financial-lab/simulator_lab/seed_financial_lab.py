#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
seed_financial_lab.py -- Seeds the financial lab with test data.

Creates:
  - Test players with KYC-verified profiles
  - Player wallets with initial balances
  - Double-entry ledger entries for each financial event
  - Historical transactions (deposits, bets, wins, withdrawals)
  - Player self-exclusion records for RG testing

Run via: python3 seed_financial_lab.py
or via K8s Job: kubectl apply -f k8s/jobs.yaml

Chapter 36b: Financial Truth Layer -- local lab testing
"""

from __future__ import annotations

import asyncio
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LEDGER_URL = os.environ.get("LEDGER_SERVICE_URL", "http://localhost:8080")
PAYMENTS_URL = os.environ.get("PAYMENTS_SERVICE_URL", "http://localhost:8081")

PLAYER_COUNT = int(os.environ.get("SEED_PLAYER_COUNT", "100"))
TRANSACTIONS_PER_PLAYER = int(os.environ.get("SEED_TRANSACTIONS_PER_PLAYER", "10"))

CURRENCIES = ["EUR", "GBP", "USD"]
JURISDICTIONS = ["MT", "GB", "NJ", "PA"]
GAME_IDS = [
    "starburst",
    "book_of_dead",
    "gonzo_quest",
    "sweet_bonanza",
    "lightning_roulette",
]
PSP_IDS = ["adyen", "trustly", "paypal", "paysafe"]


# ---------------------------------------------------------------------------
# Data generators
# ---------------------------------------------------------------------------

def _random_amount(min_cents: int = 500, max_cents: int = 50000) -> int:
    """Return a random amount in cents."""
    return random.randint(min_cents, max_cents)


def _random_player() -> dict[str, object]:
    player_id = str(uuid.uuid4())
    jurisdiction = random.choice(JURISDICTIONS)
    currency = "GBP" if jurisdiction == "GB" else ("USD" if jurisdiction in ("NJ", "PA") else "EUR")
    return {
        "player_id": player_id,
        "username": f"test_player_{player_id[:8]}",
        "email": f"{player_id[:8]}@test.lab",
        "jurisdiction": jurisdiction,
        "currency": currency,
        "kyc_status": "VERIFIED",
        "kyc_tier": random.choice(["STANDARD", "ENHANCED"]),
        "date_of_birth": "1990-06-15",
        "country_code": "MT" if jurisdiction == "MT" else jurisdiction,
        "marketing_consent": random.choice([True, False]),
        "created_at": (
            datetime.now(tz=timezone.utc) - timedelta(days=random.randint(1, 365))
        ).isoformat(),
    }


# ---------------------------------------------------------------------------
# API clients
# ---------------------------------------------------------------------------

async def create_player(client: httpx.AsyncClient, player: dict[str, object]) -> dict[str, object]:
    """POST /api/v1/players to create a test player."""
    resp = await client.post(f"{PAYMENTS_URL}/api/v1/players", json=player, timeout=10)
    if resp.status_code not in (200, 201, 409):  # 409 = already exists
        resp.raise_for_status()
    return resp.json() if resp.status_code != 409 else player


async def post_ledger_entry(
    client: httpx.AsyncClient,
    player_id: str,
    event_type: str,
    debit_account: str,
    credit_account: str,
    amount_cents: int,
    currency: str,
    reference_id: str,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """POST a double-entry ledger booking."""
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": event_type,
        "player_id": player_id,
        "entries": [
            {
                "account": debit_account,
                "direction": "DEBIT",
                "amount_cents": amount_cents,
                "currency": currency,
            },
            {
                "account": credit_account,
                "direction": "CREDIT",
                "amount_cents": amount_cents,
                "currency": currency,
            },
        ],
        "reference_id": reference_id,
        "metadata": metadata or {},
        "booked_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    resp = await client.post(f"{LEDGER_URL}/api/v1/entries", json=payload, timeout=10)
    if resp.status_code not in (200, 201, 409):
        resp.raise_for_status()
    return resp.json() if resp.status_code != 409 else payload


async def seed_player_transactions(
    client: httpx.AsyncClient,
    player: dict[str, object],
    tx_count: int,
) -> int:
    """Seed deposit/bet/win/withdrawal transactions for a player."""
    player_id = str(player["player_id"])
    currency = str(player["currency"])
    balance_cents = 0
    tx_created = 0

    for i in range(tx_count):
        psp = random.choice(PSP_IDS)
        ref = str(uuid.uuid4())

        # Deposit
        deposit_amount = _random_amount(1000, 20000)
        await post_ledger_entry(
            client,
            player_id=player_id,
            event_type="DEPOSIT",
            debit_account=f"PSP_CLEARING:{psp}",
            credit_account=f"PLAYER_WALLET:{player_id}",
            amount_cents=deposit_amount,
            currency=currency,
            reference_id=f"dep_{ref}",
            metadata={"psp": psp, "jurisdiction": player["jurisdiction"]},
        )
        balance_cents += deposit_amount
        tx_created += 1

        # Place 1-3 bets
        bet_count = random.randint(1, 3)
        for _ in range(bet_count):
            if balance_cents <= 0:
                break
            bet_amount = min(_random_amount(100, 5000), balance_cents)
            game_id = random.choice(GAME_IDS)
            game_ref = str(uuid.uuid4())

            # Bet
            await post_ledger_entry(
                client,
                player_id=player_id,
                event_type="BET",
                debit_account=f"PLAYER_WALLET:{player_id}",
                credit_account=f"OPERATOR_REVENUE:{game_id}",
                amount_cents=bet_amount,
                currency=currency,
                reference_id=f"bet_{game_ref}",
                metadata={"game_id": game_id},
            )
            balance_cents -= bet_amount
            tx_created += 1

            # Win (66% RTP-approximating probability)
            if random.random() < 0.66:
                win_amount = int(bet_amount * random.uniform(0.5, 2.5))
                await post_ledger_entry(
                    client,
                    player_id=player_id,
                    event_type="WIN",
                    debit_account=f"OPERATOR_REVENUE:{game_id}",
                    credit_account=f"PLAYER_WALLET:{player_id}",
                    amount_cents=win_amount,
                    currency=currency,
                    reference_id=f"win_{game_ref}",
                    metadata={"game_id": game_id, "round_ref": game_ref},
                )
                balance_cents += win_amount
                tx_created += 1

        # Occasional withdrawal (30% chance per loop iteration)
        if balance_cents > 1000 and random.random() < 0.30:
            withdraw_amount = min(
                _random_amount(500, 10000),
                balance_cents - 500,  # Keep at least 5.00 in wallet
            )
            if withdraw_amount > 0:
                await post_ledger_entry(
                    client,
                    player_id=player_id,
                    event_type="WITHDRAWAL",
                    debit_account=f"PLAYER_WALLET:{player_id}",
                    credit_account=f"PSP_CLEARING:{psp}",
                    amount_cents=withdraw_amount,
                    currency=currency,
                    reference_id=f"wd_{ref}",
                    metadata={"psp": psp},
                )
                balance_cents -= withdraw_amount
                tx_created += 1

    return tx_created


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print(f"Seeding financial lab: {PLAYER_COUNT} players, "
          f"{TRANSACTIONS_PER_PLAYER} tx/player")
    print(f"  Ledger: {LEDGER_URL}")
    print(f"  Payments: {PAYMENTS_URL}")

    total_tx = 0
    players_ok = 0
    players_failed = 0

    async with httpx.AsyncClient() as client:
        for i in range(PLAYER_COUNT):
            player = _random_player()
            try:
                created = await create_player(client, player)
                tx_count = await seed_player_transactions(
                    client, created, TRANSACTIONS_PER_PLAYER
                )
                total_tx += tx_count
                players_ok += 1
                if (i + 1) % 10 == 0:
                    print(f"  {i + 1}/{PLAYER_COUNT} players seeded, "
                          f"{total_tx} transactions total")
            except httpx.HTTPStatusError as exc:
                player_id_str = str(player.get("player_id", "unknown"))[:8]
                print(f"  WARN: Player {player_id_str} failed: {exc}")
                players_failed += 1

    print(f"\nSeed complete:")
    print(f"  Players created: {players_ok}")
    print(f"  Players failed:  {players_failed}")
    print(f"  Total ledger entries: {total_tx}")

    if players_failed > PLAYER_COUNT * 0.1:
        raise SystemExit(
            f"Too many failures: {players_failed}/{PLAYER_COUNT}. "
            f"Check service health."
        )


if __name__ == "__main__":
    asyncio.run(main())
