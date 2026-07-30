# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Casino Load Test — Locust Simulation
# Source: Production casino platform (sanitized)
# Chapter 32 - Testing
#
# Simulates realistic casino peak-hour traffic patterns:
#   - Casual Slot Player  — browse, deposit occasionally, play slots
#   - Live Dealer Player  — deposit, play live tables
#   - Sports Bettor       — browse events, place multiple bets
#   - Lobby Browser       — search and browse without playing
#
# The load model replicates "peak Saturday evening" traffic with a
# ramp-up followed by a sustained plateau.
#
# Run:
#   locust -f casino_load_test.py \
#          --host https://api.staging-casino.com \
#          --headless -u 5000 -r 200 -t 20m \
#          --html report.html
#
# Environment variables:
#   BASE_URL      = https://api.staging-casino.com
#   API_VERSION   = v1
#   TEST_DURATION = 20   (minutes)
#
# Pass/fail assertions (checked via locust stats after the run):
#   - global P95  < 2000 ms
#   - global P99  < 5000 ms
#   - success %   > 99%
#   - Spin P95    < 500 ms
#   - Login P95   < 1500 ms
#   - Deposit P95 < 3000 ms
#   - Place Sports Bet P95 < 300 ms
# =============================================================================

from __future__ import annotations

import os
import random
import uuid
from datetime import datetime, timezone

# This file is a Locust runner (`locust -f casino_load_test.py`), not a
# pytest test module -- it just happens to match pytest's `*_test.py`
# glob. Gate the third-party imports so that a full-repo pytest collect
# from scripts/ doesn't blow up when locust is absent or conflicts with
# the system urllib3 version.
import pytest

pytest.importorskip(
    "locust",
    reason="casino load test requires locust; run with `locust -f`, not pytest",
)
try:
    from locust import HttpUser, SequentialTaskSet, TaskSet, between, events, task
    from locust.contrib.fasthttp import FastHttpUser
except ImportError as _exc:  # urllib3 / gevent incompat, etc.
    pytest.skip(
        f"locust import failed ({_exc}); run via `locust -f`, not pytest",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL    = os.getenv("BASE_URL", "https://api.staging-casino.com")
API_VERSION = os.getenv("API_VERSION", "v1")

SLOT_GAMES = [
    "starburst", "book-of-dead", "gonzo-quest", "mega-moolah",
    "sweet-bonanza", "gates-of-olympus", "wolf-gold", "reactoonz",
]

BET_AMOUNTS    = [0.10, 0.20, 0.50, 1.00, 2.00, 5.00, 10.00]
DEPOSIT_AMOUNTS = [10, 20, 50, 100, 200, 500]
PAYMENT_METHODS = ["visa", "mastercard", "skrill", "neteller"]
CURRENCIES      = ["USD", "EUR", "GBP", "CAD"]
SPORTS_MARKETS  = ["1x2", "over_under_2.5", "btts", "correct_score"]
SPORTS_OUTCOMES = ["home", "draw", "away", "over", "under", "yes", "no"]
SPORTS_STAKES   = [1, 2, 5, 10, 20, 50]

PREFIX = f"/api/{API_VERSION}"


# ---------------------------------------------------------------------------
# Shared request helpers (mixin)
# ---------------------------------------------------------------------------


class CasinoMixin:

    def _auth_headers(self) -> dict:
        token = getattr(self, "_auth_token", "")
        return {"Authorization": f"Bearer {token}"}

    def authenticate(self):
        uid = random.randint(1, 100_000)
        payload = {
            "username": f"gatling_player_{uid}",
            "password": f"TestP@ss{random.randint(1000, 9999)}!",
        }
        with self.client.post(
            f"{PREFIX}/auth/login",
            json=payload,
            name="Login",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                self._auth_token = resp.json().get("token", "")
            else:
                resp.failure(f"Login failed: {resp.status_code}")

    def check_balance(self):
        with self.client.get(
            f"{PREFIX}/wallet/balance",
            headers=self._auth_headers(),
            name="Check Balance",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Balance check failed: {resp.status_code}")
            else:
                self._currency = resp.json().get("currency", "EUR")

    def make_deposit(self):
        amount  = random.choice(DEPOSIT_AMOUNTS)
        method  = random.choice(PAYMENT_METHODS)
        currency = getattr(self, "_currency", "EUR")
        payload = {"amount": amount, "currency": currency, "method": method}
        with self.client.post(
            f"{PREFIX}/payments/deposit",
            json=payload,
            headers=self._auth_headers(),
            name="Deposit",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 201):
                resp.failure(f"Deposit failed: {resp.status_code}")

    def request_withdrawal(self):
        currency = getattr(self, "_currency", "EUR")
        payload = {"amount": 50, "currency": currency, "method": "bank_transfer"}
        with self.client.post(
            f"{PREFIX}/payments/withdraw",
            json=payload,
            headers=self._auth_headers(),
            name="Withdrawal",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 201, 202):
                resp.failure(f"Withdrawal failed: {resp.status_code}")

    def slot_session(self):
        game_id = random.choice(SLOT_GAMES)
        currency = getattr(self, "_currency", "EUR")

        with self.client.get(
            f"{PREFIX}/games/slots/{game_id}/launch",
            headers=self._auth_headers(),
            name="Launch Slot",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Launch slot failed: {resp.status_code}")
                return
            session_id = resp.json().get("session_id", str(uuid.uuid4()))

        spins = 5 + random.randint(0, 45)
        for _ in range(spins):
            bet   = random.choice(BET_AMOUNTS)
            lines = 20
            with self.client.post(
                f"{PREFIX}/games/slots/{game_id}/spin",
                json={"session_id": session_id, "bet_amount": bet, "lines": lines},
                headers=self._auth_headers(),
                name="Spin",
                catch_response=True,
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"Spin failed: {resp.status_code}")
                else:
                    win_amount = resp.json().get("win_amount", 0)
                    if win_amount and float(win_amount) > 0:
                        self.client.post(
                            f"{PREFIX}/games/slots/{game_id}/collect",
                            json={"session_id": session_id},
                            headers=self._auth_headers(),
                            name="Collect Win",
                        )

        self.client.post(
            f"{PREFIX}/games/slots/{game_id}/close",
            json={"session_id": session_id},
            headers=self._auth_headers(),
            name="Close Game",
        )

    def live_dealer_session(self):
        self.client.get(
            f"{PREFIX}/live/tables",
            headers=self._auth_headers(),
            name="List Live Tables",
        )

        with self.client.post(
            f"{PREFIX}/live/roulette-eu-1/join",
            headers=self._auth_headers(),
            name="Join Table",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Join table failed: {resp.status_code}")

        rounds = 3 + random.randint(0, 17)
        for _ in range(rounds):
            bet_amount = 5 + random.randint(0, 95)
            self.client.post(
                f"{PREFIX}/live/roulette-eu-1/bet",
                json={"bets": [{"type": "red", "amount": bet_amount}]},
                headers=self._auth_headers(),
                name="Place Live Bet",
            )
            self.client.get(
                f"{PREFIX}/live/roulette-eu-1/result",
                headers=self._auth_headers(),
                name="Get Round Result",
            )

        self.client.post(
            f"{PREFIX}/live/roulette-eu-1/leave",
            headers=self._auth_headers(),
            name="Leave Table",
        )

    def sports_betting(self):
        currency = getattr(self, "_currency", "EUR")
        self.client.get(
            f"{PREFIX}/sports/events?status=live&sport=football",
            headers=self._auth_headers(),
            name="Get Live Events",
        )

        event_id = f"event_{random.randint(1, 500)}"
        market   = random.choice(SPORTS_MARKETS)
        outcome  = random.choice(SPORTS_OUTCOMES)
        odds     = round(1.1 + random.random() * 9.9, 2)
        stake    = random.choice(SPORTS_STAKES)

        self.client.get(
            f"{PREFIX}/sports/events/{event_id}/odds",
            headers=self._auth_headers(),
            name="Get Event Odds",
        )

        bet_id: str | None = None
        with self.client.post(
            f"{PREFIX}/sports/bets",
            json={
                "type": "single",
                "selections": [{"event_id": event_id, "market": market, "outcome": outcome, "odds": odds}],
                "stake": stake,
                "currency": currency,
            },
            headers=self._auth_headers(),
            name="Place Sports Bet",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                bet_id = resp.json().get("bet_id")
            else:
                resp.failure(f"Place sports bet failed: {resp.status_code}")

        if bet_id and random.random() < 0.2:
            self.client.get(
                f"{PREFIX}/sports/bets/{bet_id}/cashout",
                headers=self._auth_headers(),
                name="Check Cash Out",
            )


# ---------------------------------------------------------------------------
# Player scenarios
# ---------------------------------------------------------------------------


class CasualSlotPlayer(SequentialTaskSet, CasinoMixin):
    @task
    def journey(self):
        self.authenticate()
        self.check_balance()
        if random.random() < 0.4:
            self.make_deposit()
        self.slot_session()
        if random.random() < 0.05:
            self.request_withdrawal()
        self.interrupt()


class LiveDealerPlayer(SequentialTaskSet, CasinoMixin):
    @task
    def journey(self):
        self.authenticate()
        self.check_balance()
        self.make_deposit()
        self.live_dealer_session()
        if random.random() < 0.1:
            self.request_withdrawal()
        self.interrupt()


class SportsBettorJourney(SequentialTaskSet, CasinoMixin):
    @task
    def journey(self):
        self.authenticate()
        self.check_balance()
        if random.random() < 0.3:
            self.make_deposit()
        bets = 2 + random.randint(0, 8)
        for _ in range(bets):
            self.sports_betting()
        self.interrupt()


class LobbyBrowseJourney(SequentialTaskSet, CasinoMixin):
    @task
    def journey(self):
        self.authenticate()
        self.client.get(
            f"{PREFIX}/games/lobby?category=popular",
            headers=self._auth_headers(),
            name="Browse Lobby",
        )
        self.client.get(
            f"{PREFIX}/games/search?q=mega&limit=20",
            headers=self._auth_headers(),
            name="Search Games",
        )
        self.interrupt()


# ---------------------------------------------------------------------------
# User classes — load model: peak Saturday evening
# ---------------------------------------------------------------------------


class CasualSlotUser(HttpUser):
    tasks = [CasualSlotPlayer]
    wait_time = between(2, 10)
    host = BASE_URL
    weight = 50


class LiveDealerUser(HttpUser):
    tasks = [LiveDealerPlayer]
    wait_time = between(3, 8)
    host = BASE_URL
    weight = 20


class SportsBettorUser(HttpUser):
    tasks = [SportsBettorJourney]
    wait_time = between(1, 5)
    host = BASE_URL
    weight = 30


class LobbyBrowserUser(HttpUser):
    tasks = [LobbyBrowseJourney]
    wait_time = between(5, 15)
    host = BASE_URL
    weight = 10
