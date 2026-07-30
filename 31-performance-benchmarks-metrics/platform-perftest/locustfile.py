# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Platform Performance Test — Locust Simulation
# Source: Production casino platform (sanitized)
# Chapter 31 - Performance Testing
#
# Simulates two realistic player journeys on the Hub + Spoke platform:
#   1. Kambi Scenario   — sports betting: register/login -> deposit -> bet -> settle
#   2. Evolution Scenario — live casino: register/login -> deposit -> evo session
#
# Run:
#   locust -f locustfile.py --host http://hub:8080
#   locust -f locustfile.py --host http://hub:8080 \
#          --headless -u 100 -r 10 -t 10m \
#          -H http://hub:8080 \
#          --il-url http://il-spoke:8080 \
#          --pa-url http://pa-spoke:8080
#
# Environment / locust params (set via --variable or environment vars):
#   HUB_URL     = http://hub:8080
#   IL_URL      = http://il-spoke:8080
#   PA_URL      = http://pa-spoke:8080
#   NODE        = 0   (used to split CSV user files: users_aa, users_ab, ...)
# =============================================================================

from __future__ import annotations

import os
import random
import string
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from locust import HttpUser, SequentialTaskSet, between, events, task
from locust.contrib.fasthttp import FastHttpUser

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HUB_URL = os.getenv("HUB_URL", "http://localhost:8080")
IL_URL  = os.getenv("IL_URL",  "http://localhost:8081")
PA_URL  = os.getenv("PA_URL",  "http://localhost:8082")

DEFAULT_PASSWORD = os.getenv("LOADTEST_PASSWORD", "loadtest-user")
SPORT_BONUS_GROUP = 1789
CASINO_BONUS_GROUP = 1790

URL_MAP = {
    "IL": IL_URL,
    "PA": PA_URL,
}

JURISDICTIONS = ["IL", "PA"]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def random_jurisdiction() -> str:
    return random.choice(JURISDICTIONS)


def jurisdiction_param(jurisdiction: str) -> str:
    return f"US-{jurisdiction}"


def jurisdiction_url(jurisdiction: str) -> str:
    return URL_MAP.get(jurisdiction, IL_URL)


def make_suffix(idx: int) -> str:
    alphabet = string.ascii_lowercase
    mod = len(alphabet)
    return alphabet[idx // mod] + alphabet[idx % mod]


# ---------------------------------------------------------------------------
# Hub request helpers (mixed into TaskSet via composition)
# ---------------------------------------------------------------------------


class HubMixin:
    """Mixin providing Hub platform request helpers."""

    def _hub_post(self, path: str, payload: dict, name: str, client):
        with client.post(
            f"{HUB_URL}{path}",
            json=payload,
            name=name,
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"{name} returned {resp.status_code}")
            return resp

    def register(self, client) -> Optional[dict]:
        uid = str(uuid.uuid4())
        phone = str(random.randint(1000000000, 9999999999))
        payload = {
            "username": f"perf-{uid}",
            "email": f"perf-{uid}@test.example.com",
            "password": DEFAULT_PASSWORD,
            "confirm_password": DEFAULT_PASSWORD,
            "regulation_id": "US_WEST_VIRGINIA",
            "phone": phone,
            "currency_code": "USD",
            "firstname": "Perf",
            "lastname": "Test",
            "dob": "1985-10-06",
            "address1": "638 Appleseed Dr",
            "postalcode": phone[:5],
            "town": "lorain",
            "city": "Philadelphia",
            "state": "PA",
            "zipcode": phone[:5],
            "country_code": "1",
            "externalId": f"perf-{uid}",
            "ssnMatch": "1233",
            "jurisdiction": jurisdiction_param(random_jurisdiction()),
            "preferences": {
                "odds_format": "american",
                "require_login_security_questions": False,
                "login_notification": False,
                "review_line_change": False,
            },
            "brand": 90,
            "currency": "USD",
            "country": "US",
        }
        resp = self._hub_post("/platform/usergateway/registeruser", payload, "Register", client)
        if resp.status_code == 200:
            data = resp.json()
            return {"user_id": data.get("userid"), "session_id": data.get("sessionid")}
        return None

    def login(self, username: str, jurisdiction: str, client) -> Optional[dict]:
        payload = {
            "brand": "acmesportsbook",
            "username": username,
            "password": DEFAULT_PASSWORD,
            "jurisdiction": jurisdiction_param(jurisdiction),
            "ip": "10.0.0.1",
            "userAgent": "Mozilla/5.0 (Locust Perf Test)",
            "type": "userlogin",
        }
        resp = self._hub_post("/platform/usergateway/userlogin", payload, "Login", client)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "user_id": data.get("userid"),
                "username": data.get("username"),
                "session_id": data.get("sessionid"),
            }
        return None

    def hub_deposit(self, user_id: str, client) -> None:
        payload = {
            "userId": user_id,
            "brand": "acme",
            "provider": "payment-provider",
            "method": "test",
            "ref": "test",
            "amount": "100000000.0",
        }
        self._hub_post("/platform/usergateway/deposit", payload, "HubDeposit", client)

    def get_user_details(self, user_id: str, client) -> Optional[str]:
        resp = self._hub_post(
            "/platform/usergateway/getuserdetails",
            {"userId": user_id, "gameid": 1},
            "GetUserDetails",
            client,
        )
        if resp.status_code == 200:
            return resp.json().get("playerid")
        return None

    def geo_verify_lease(self, user_id: str, client) -> None:
        expires_on = (datetime.now(timezone.utc) + timedelta(minutes=11)).isoformat()
        self._hub_post(
            "/platform/usergateway/geoverify-lease",
            {"userId": user_id, "expiresOn": expires_on, "type": "geoverify-lease"},
            "GeoVerifyLease",
            client,
        )

    def get_balance(self, user_id: str, client) -> None:
        self._hub_post(
            "/platform/usergateway/getbalance",
            {"userId": user_id},
            "GetBalance",
            client,
        )

    def limit_status(self, user_id: str, session_id: str, limit_type: str, client) -> None:
        self._hub_post(
            f"/platform/usergateway/{limit_type}-limit-status",
            {"userId": user_id, "sessionid": session_id},
            f"{limit_type}-limit-status",
            client,
        )

    def operator_site_updates(self, user_id: str, session_id: str, client) -> None:
        self.get_balance(user_id, client)
        for lt in [
            "dailyloggedin", "singlewager", "monthlywager", "weeklywager",
            "weeklydeposit", "monthlydeposit", "dailywager", "dailydeposit", "rcperiod",
        ]:
            self.limit_status(user_id, session_id, lt, client)


# ---------------------------------------------------------------------------
# Spoke request helpers
# ---------------------------------------------------------------------------


class SpokeMixin:
    """Mixin providing Spoke supplier request helpers."""

    def _spoke_post(self, path: str, payload: dict, name: str, jurisdiction: str, client):
        url = jurisdiction_url(jurisdiction)
        with client.post(
            f"{url}{path}",
            json=payload,
            name=f"{jurisdiction} {name}",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"{name} returned {resp.status_code}")
            return resp

    def kambi_authenticate(self, player_id: int, jurisdiction: str, client) -> Optional[str]:
        payload = {"ticket": f"encoded-token-{player_id}"}
        resp = self._spoke_post(
            "/platform/supplier/kambi/authenticate",
            payload,
            "Kambi:Authenticate",
            jurisdiction,
            client,
        )
        if resp.status_code == 200:
            return resp.json().get("playerSessionToken")
        return None

    def kambi_fund(
        self, player_id: str, session_token: str, combination_ref: int, coupon_ref: int, jurisdiction: str, client
    ) -> None:
        payload = {
            "customerPlayerId": player_id,
            "kambiTransactionId": str(uuid.uuid4()),
            "kambiTransactionType": "STAKE_TRANSACTION_APPROVED_BET",
            "productType": "SPORTSBOOK",
            "betInformation": {
                "couponRef": coupon_ref,
                "type": "SPORTSBOOK_COUPON_STAKE_ODDS",
                "channel": "MOBILE",
                "combinations": [{
                    "combinationRef": combination_ref,
                    "size": 1,
                    "liveBetting": False,
                    "odds": 1.91,
                    "stake": 400,
                    "payload": "2",
                }],
            },
            "currencyCode": "USD",
            "amount": 400,
            "playerSessionToken": session_token,
        }
        self._spoke_post("/platform/supplier/kambi/fund", payload, "Kambi:Fund", jurisdiction, client)

    def kambi_deposit(
        self, player_id: str, combination_ref: int, coupon_ref: int, jurisdiction: str, client
    ) -> None:
        payload = {
            "customerPlayerId": player_id,
            "kambiTransactionId": str(uuid.uuid4()),
            "kambiTransactionType": "PAYOUT_SETTLED_BET_DEPOSIT",
            "productType": "SPORTSBOOK",
            "betInformation": {
                "couponRef": coupon_ref,
                "type": "SPORTSBOOK_COMBINATION",
                "combinationRefs": [combination_ref],
            },
            "currencyCode": "USD",
            "amount": 400,
        }
        self._spoke_post("/platform/supplier/kambi/deposit", payload, "Kambi:Deposit", jurisdiction, client)

    def kambi_close(
        self, player_id: str, combination_ref: int, coupon_ref: int, jurisdiction: str, client
    ) -> None:
        payload = {
            "customerPlayerId": player_id,
            "kambiTransactionId": str(uuid.uuid4()),
            "kambiTransactionType": "CLOSE_LOST_BET",
            "productType": "SPORTSBOOK",
            "betInformation": {
                "couponRef": coupon_ref,
                "type": "SPORTSBOOK_COMBINATION",
                "combinationRefs": [combination_ref],
            },
        }
        self._spoke_post("/platform/supplier/kambi/close", payload, "Kambi:Close", jurisdiction, client)

    def evo_login(self, player_id: int, jurisdiction: str, client) -> Optional[dict]:
        payload = {"uuid": str(uuid.uuid4()), "userId": str(player_id)}
        resp = self._spoke_post(
            "/platform/supplier/evolution/sid?authToken=test_api_key",
            payload,
            "Evolution:Authenticate",
            jurisdiction,
            client,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"sid": data.get("sid"), "uuid": data.get("uuid")}
        return None

    def evo_debit(
        self, player_id: int, sid: str, evo_uuid: str, transaction_id: str, round_id: str, jurisdiction: str, client
    ) -> None:
        payload = {
            "sid": sid,
            "uuid": evo_uuid,
            "userId": str(player_id),
            "currency": "USD",
            "game": {
                "id": round_id,
                "type": "blackjack",
                "details": {"table": {"id": "testTableId"}},
            },
            "transaction": {"id": f"D{transaction_id}", "refId": transaction_id, "amount": 2.50},
        }
        self._spoke_post(
            "/platform/supplier/evolution/debit?authToken=test_api_key",
            payload,
            "Evolution:Debit",
            jurisdiction,
            client,
        )

    def evo_credit(
        self, player_id: int, sid: str, evo_uuid: str, transaction_id: str, round_id: str, jurisdiction: str, client
    ) -> None:
        payload = {
            "sid": sid,
            "uuid": evo_uuid,
            "userId": str(player_id),
            "currency": "USD",
            "game": {
                "id": round_id,
                "type": "blackjack",
                "details": {"table": {"id": "testTableId"}},
            },
            "transaction": {"id": f"C{transaction_id}", "refId": transaction_id, "amount": 3.25},
        }
        self._spoke_post(
            "/platform/supplier/evolution/credit?authToken=test_api_key",
            payload,
            "Evolution:Credit",
            jurisdiction,
            client,
        )


# ---------------------------------------------------------------------------
# User scenarios
# ---------------------------------------------------------------------------


class KambiBettorJourney(SequentialTaskSet, HubMixin, SpokeMixin):
    """
    Kambi sports-betting journey (new user):
    register -> deposit -> geo-verify -> kambi authenticate -> fund -> deposit/close
    """

    @task
    def run_journey(self):
        jurisdiction = random_jurisdiction()
        reg = self.register(self.client)
        if not reg:
            return

        user_id = str(reg["user_id"])
        session_id = str(reg["session_id"])
        player_id = self.get_user_details(user_id, self.client)
        self.operator_site_updates(user_id, session_id, self.client)
        self.geo_verify_lease(user_id, self.client)
        self.hub_deposit(user_id, self.client)

        token = self.kambi_authenticate(int(player_id or 0), jurisdiction, self.client)
        if not token:
            return

        combo_ref = random.randint(1, 2**31 - 1)
        coupon_ref = random.randint(1, 2**31 - 1)
        self.kambi_fund(player_id, token, combo_ref, coupon_ref, jurisdiction, self.client)

        if random.random() < 0.66:
            self.kambi_deposit(player_id, combo_ref, coupon_ref, jurisdiction, self.client)
        else:
            self.kambi_close(player_id, combo_ref, coupon_ref, jurisdiction, self.client)

        self.operator_site_updates(user_id, session_id, self.client)


class EvolutionJourney(SequentialTaskSet, HubMixin, SpokeMixin):
    """
    Evolution live-casino journey (new user):
    register -> deposit -> geo-verify -> evo login -> debit -> credit
    """

    @task
    def run_journey(self):
        jurisdiction = "IL"  # Evolution scenario uses IL
        reg = self.register(self.client)
        if not reg:
            return

        user_id = str(reg["user_id"])
        session_id = str(reg["session_id"])
        player_id = self.get_user_details(user_id, self.client)
        self.hub_deposit(user_id, self.client)
        self.geo_verify_lease(user_id, self.client)

        evo_session = self.evo_login(int(player_id or 0), jurisdiction, self.client)
        if not evo_session:
            return

        sid = evo_session["sid"]
        evo_uuid = evo_session["uuid"]
        transaction_id = str(uuid.uuid4())
        round_id = str(uuid.uuid4())
        self.evo_debit(int(player_id or 0), sid, evo_uuid, transaction_id, round_id, jurisdiction, self.client)
        self.evo_credit(int(player_id or 0), sid, evo_uuid, transaction_id, round_id, jurisdiction, self.client)


class KambiUser(FastHttpUser):
    tasks = [KambiBettorJourney]
    wait_time = between(5, 15)
    host = HUB_URL


class EvolutionUser(FastHttpUser):
    tasks = [EvolutionJourney]
    wait_time = between(5, 15)
    host = HUB_URL
