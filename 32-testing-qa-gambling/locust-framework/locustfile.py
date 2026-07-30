# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Locust Load Test — Sports Betting Platform

Simulates realistic user journeys through a complete sportsbook session:
  register -> login -> geo-validate -> browse homepage -> deposit -> bet -> logout

Production patterns:
  - Behavioral schema-driven action sequences (Markov chain transitions)
  - Pre-registered user pool with CSV fallback
  - Sportsbook Offering API integration for live odds browsing
  - Session management with automatic re-authentication on expiry
  - Failure tracking with aggregation across distributed workers
  - DynamoDB result persistence for post-test analysis

Derived from a production load-testing codebase for a US-regulated
sportsbook platform. Internal URLs, credentials, and vendor-specific
identifiers have been replaced with configurable environment variables.
"""

import json
import logging
import os
import random
import time
import uuid

import numpy as np
import yaml
from locust import HttpUser, TaskSet, between, events, task
from locust.contrib.fasthttp import FastHttpUser
from locust.exception import InterruptTaskSet, StopUser

from synthetic_user_engine import Replicant
from betting_api_integration import Bettor

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
LOG_DIR = os.getenv("LOG_DIR", "./")

SPORTSBOOK_OFFERING_URL = os.getenv(
    "SPORTSBOOK_OFFERING_URL", "https://offering.sportsbookprovider.example.com"
)
SPORTSBOOK_PLAYER_URL = os.getenv(
    "SPORTSBOOK_PLAYER_URL", "https://player.sportsbookprovider.example.com"
)
SPORTSBOOK_OFFERING_ID = os.getenv("SPORTSBOOK_OFFERING_ID", "acmetocasino")

PLATFORM_BASE_URL = os.getenv(
    "PLATFORM_BASE_URL", "https://platform.acmetocasino.example.com/api"
)

USE_PREREG = os.getenv("USE_PREREG", "false")

# ---------------------------------------------------------------------------
# Load behavioral schemas
# ---------------------------------------------------------------------------
bettor_weights = {"aggressive": 1}
bettor_profiles = {}
for bettor_type in bettor_weights:
    with open(f"../schemas/{bettor_type}.yaml", "r") as f:
        bettor_profiles[bettor_type] = yaml.load(f, Loader=yaml.FullLoader)

# Pre-registered user pool (optional)
users_list = []
if USE_PREREG == "true":
    with open("users.csv") as f:
        users_list = list(set(f.read().splitlines()))

# ---------------------------------------------------------------------------
# Failure tracking — aggregated across workers for master reporting
# ---------------------------------------------------------------------------
failed_records = []
master_fails = {}


@events.request_failure.add_listener  # ty:ignore[unresolved-attribute]
def on_failure(request_type, name, response_time, response_length, exception):
    if hasattr(exception, "request") and hasattr(exception, "response"):
        failed_records.append({
            "url": exception.request.url,
            "payload": getattr(exception.request, "payload", ""),
            "response_status": exception.response.status_code,
            "response_content": exception.response.content.decode()
            if exception.response.content else "",
        })
    else:
        raise exception


@events.report_to_master.add_listener
def report_failures(client_id, data, **kw):
    data["failed_record"] = failed_records


@events.worker_report.add_listener
def aggregate_failures(client_id, data, **kw):
    master_fails[client_id] = data["failed_record"]


@events.test_stop.add_listener
@events.quitting.add_listener
def write_failures_to_file(**kw):
    with open(f"{LOG_DIR}/locust_failed_requests.csv", "w") as f:
        for client_id in master_fails:
            for record in master_fails[client_id]:
                f.write(f"REQUEST URL: {record['url']}\n")
                f.write(f"REQUEST PAYLOAD: {record['payload']}\n")
                f.write(f"RESPONSE STATUS: {record['response_status']}\n")
                f.write(f"RESPONSE CONTENT: {record['response_content']}\n\n")


# ---------------------------------------------------------------------------
# TaskSet — the complete user journey
# ---------------------------------------------------------------------------
class BettorTasks(TaskSet):
    wait_time = between(0.0, 0.0)
    wallet = 0.0

    def on_start(self):
        self.map_functions()

        bettor_type = np.random.choice(
            list(bettor_weights.keys()), 1, p=list(bettor_weights.values())
        )[0]
        schema = bettor_profiles[bettor_type]

        self.replicant = Replicant(schema)
        self.replicant.implant_memories()
        self.user_actions = self.replicant.false_memories
        self.correlation_id = self.replicant.ocular_serial_number

        # Execute first action (register or login)
        if users_list and bool(np.random.binomial(1, 0.95)):
            random.shuffle(users_list)
            self.username = users_list.pop()
            self.password = os.getenv("LOAD_TEST_PASSWORD", "changeme")
            self.login(None, 0)
        else:
            self.register(self.replicant.user_data, 0)

        # Execute remaining actions from the generated sequence
        for memory in self.replicant.false_memories[1:]:
            self._map[memory["action"]](memory["data"], memory["wait"])

        raise StopUser

    @task
    def register(self, data, wait):
        self.username = data["username"]
        self.password = data["password"]

        with self.client.post(
            "/users/register/",
            headers={"X-Corr-Id": self.correlation_id, "Consumer": "script"},
            data=data,
            catch_response=True,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                response.failure(f"Registration failed for {self.username}")
                raise InterruptTaskSet

            resp_data = response.json()["data"]
            self._extract_session(resp_data)

        time.sleep(wait)

    @task
    def login(self, _, wait):
        with self.client.post(
            "/users/login/",
            headers={"X-Corr-Id": self.correlation_id, "Consumer": "script"},
            data={"username": self.username, "password": self.password},
            catch_response=True,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                response.failure(f"Login failed for {self.username}")
                raise InterruptTaskSet

            resp_data = response.json()["data"]
            self._extract_session(resp_data)

        time.sleep(wait)

    @task
    def place_bet(self, data, wait):
        n_bets = random.randint(2, 16)
        bettor = Bettor(
            client=self.client,
            offering_url=SPORTSBOOK_OFFERING_URL,
            player_api=SPORTSBOOK_PLAYER_URL,
            offering_id=SPORTSBOOK_OFFERING_ID,
            session_id=self.sportsbook_session_id,
            correlation_id=self.correlation_id,
            user_id=self.user_guid,
            user=self.username,
            pam_user_id=self.pam_user_id,
        )
        result = bettor.create_betslip(sport="basketball", count=n_bets)
        if result == "USER_NOT_AUTHENTICATED":
            self._log_back_in()
            bettor.create_betslip(sport="basketball", count=n_bets)

        time.sleep(wait)

    @task
    def deposit_funds(self, data, wait):
        with self.client.post(
            "/users/deposit/",
            headers={
                "Authorization": f"Token {self.token}",
                "X-Corr-Id": self.correlation_id,
                "Consumer": "script",
            },
            data=data,
            catch_response=True,
        ) as response:
            pass
        self.wallet += data.get("amount", 0) if data else 0
        time.sleep(wait)

    @task
    def get_balance(self, _, wait):
        self.client.post(
            f"{PLATFORM_BASE_URL}/usergateway/getbalance",
            headers={"Consumer": "script", "Content-Type": "application/json"},
            name="/getbalance",
            data=json.dumps({"type": "getbalance", "sessionid": self.pam_session_id}),
        )
        time.sleep(wait)

    @task
    def get_homepage(self, _, wait):
        response = self.client.get(
            "/offerings/homepage",
            headers={"X-Corr-Id": self.correlation_id, "Consumer": "script"},
        ).json()["layout"]

        group_ids = ",".join(str(item["groupId"]) for item in response)

        self.client.get(
            f"{SPORTSBOOK_OFFERING_URL}/offering/v2018/{SPORTSBOOK_OFFERING_ID}"
            f"/section/eventgroup/{group_ids}.json"
            f"?lang=en_US&market=US&useCombined=true&onlyMatches=true",
            headers={"X-Corr-Id": self.correlation_id},
            name="Homepage<sportsbook>/offerings/section/eventgroup",
        )
        time.sleep(wait)

    @task
    def extend_session(self, _, wait):
        with self.client.post(
            "/users/extend_session/",
            headers={
                "Authorization": f"Token {self.token}",
                "X-Corr-Id": self.correlation_id,
                "Consumer": "script",
            },
            catch_response=True,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                response.success()
                self._log_back_in()

        time.sleep(wait)

    @task
    def logout(self, _, wait):
        self.client.post(
            "/users/logout/",
            headers={
                "Authorization": f"Token {self.token}",
                "X-Corr-Id": self.correlation_id,
                "Consumer": "script",
            },
        )
        time.sleep(wait)

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------
    def _extract_session(self, data):
        self.sportsbook_session_id = data.get("sportsbook_session_id")
        self.pam_session_id = data.get("pam_session_id")
        self.pam_user_id = data.get("pam_user_id")
        self.token = data.get("token")
        self.user_guid = data.get("guid")

    def _log_back_in(self):
        self.login(None, 1)

    def map_functions(self):
        self._map = {
            "register": self.register,
            "login": self.login,
            "get_balance": self.get_balance,
            "get_homepage": self.get_homepage,
            "deposit_funds": self.deposit_funds,
            "place_bet": self.place_bet,
            "extend_session": self.extend_session,
            "logout": self.logout,
        }


# ---------------------------------------------------------------------------
# User class — FastHttpUser for high concurrency via gevent
# ---------------------------------------------------------------------------
class SyntheticBettor(FastHttpUser):
    tasks = [BettorTasks]
    wait_time = between(0, 0)
