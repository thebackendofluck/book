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
# Game Service Performance Test — Locust Simulation
# Source: Production casino platform (sanitized)
# Chapter 31 - Performance Testing
#
# Simulates real casino lobby traffic against the game-service API:
#   - load: just fetch the game feed (capacity test)
#   - soak: full lobby simulation — game feed + categories + details (endurance)
#
# Run (load test):
#   locust -f locustfile.py --host http://game-service:8083 \
#          --headless -u 200 -r 20 -t 10m \
#          -E GAME_SERVICE_URL=http://game-service:8083 \
#          -E TEST_TYPE=load
#
# Run (soak test):
#   locust -f locustfile.py --host http://game-service:8083 \
#          -E TEST_TYPE=soak
#
# Pass-fail assertions mirror the Gatling originals:
#   - P99 response time < 5000 ms
#   - P95 response time < 3000 ms
#   - Max response time < 10000 ms
#   - Zero failures
# =============================================================================

from __future__ import annotations

import os
import random

from locust import HttpUser, TaskSet, between, events, task
from locust.contrib.fasthttp import FastHttpUser

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GAME_SERVICE_URL = os.getenv("GAME_SERVICE_URL", "http://localhost:8083")
TEST_TYPE = os.getenv("TEST_TYPE", "load")  # "load" | "soak"

# Weighted brand distribution matching production traffic
BRAND_WEIGHTS = [
    (121, 40),
    (72,  20),
    (31,  20),
    (71,  10),
    (49,   5),
    (101,  1),
    (114,  1),
    (107,  1),
    (110,  1),
    (118,  1),
]

JURISDICTION_WEIGHTS = [
    ("mga",  90),
    ("ukgc",  8),
    ("de",    1),
    ("sga",   1),
]

COUNTRY_WEIGHTS = [
    ("mt", 60), ("es", 20), ("gb", 10),
    ("fi",  3), ("no",  2), ("pr",  1),
    ("ge",  1), ("de",  1), ("bg",  1), ("fr",  1),
]

LANGUAGE_WEIGHTS = [
    ("en", 90), ("de", 4), ("fr", 2), ("sv", 2), ("fi", 1), ("no", 1),
]

DEVICE_WEIGHTS = [
    ("Desktop",    70),
    ("MobileHtml", 30),
]


def weighted_choice(pairs: list[tuple]) -> str:
    population = [item for item, weight in pairs for _ in range(weight)]
    return random.choice(population)


def random_brand_id() -> int:
    return weighted_choice(BRAND_WEIGHTS)


# ---------------------------------------------------------------------------
# Task sets
# ---------------------------------------------------------------------------


class GameFeedOnly(TaskSet):
    """Fetch the game feed once per virtual user iteration (load test)."""

    @task
    def fetch_game_feed(self):
        brand_id    = random_brand_id()
        language    = weighted_choice(LANGUAGE_WEIGHTS)
        country     = weighted_choice(COUNTRY_WEIGHTS)
        jurisdiction = weighted_choice(JURISDICTION_WEIGHTS)
        device      = weighted_choice(DEVICE_WEIGHTS)
        block_unfunded = random.choices([False, True], weights=[90, 10])[0]

        params = (
            f"brandId={brand_id}&language={language}&country={country}"
            f"&jurisdiction={jurisdiction}&blockUnfunded={str(block_unfunded).lower()}&device={device}"
        )
        with self.client.get(
            f"/api/games?{params}",
            name="GET /api/games",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"games feed returned {resp.status_code}")

        self.interrupt()


class GameLobbyFull(TaskSet):
    """Simulate a full lobby browsing session (soak test)."""

    def on_start(self):
        self.brand_id    = random_brand_id()
        self.language    = weighted_choice(LANGUAGE_WEIGHTS)
        self.country     = weighted_choice(COUNTRY_WEIGHTS)
        self.jurisdiction = weighted_choice(JURISDICTION_WEIGHTS)
        self.device      = weighted_choice(DEVICE_WEIGHTS)
        self.block_unfunded = random.choices([False, True], weights=[90, 10])[0]
        self.launch_codes: list[str] = []
        self.category_ids: list[str] = []

    def _base_params(self) -> str:
        return (
            f"brandId={self.brand_id}&language={self.language}&country={self.country}"
            f"&jurisdiction={self.jurisdiction}&blockUnfunded={str(self.block_unfunded).lower()}&device={self.device}"
        )

    @task(1)
    def full_lobby(self):
        # Game feed
        with self.client.get(f"/api/games?{self._base_params()}", name="GET /api/games", catch_response=True) as r:
            if r.status_code == 200:
                data = r.json() if callable(r.json) else []
                if isinstance(data, list):
                    self.launch_codes = [g.get("launchCode") for g in data[:10] if g.get("launchCode")]
            else:
                r.failure(f"games feed {r.status_code}")

        # Providers
        self.client.get("/api/game-providers", name="GET /api/game-providers")

        # Categories
        with self.client.get(
            f"/api/game-categories/brands/{self.brand_id}?device={self.device}",
            name="GET /api/game-categories/brands",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                data = r.json() if callable(r.json) else []
                if isinstance(data, list):
                    self.category_ids = [str(c.get("id")) for c in data[:10] if c.get("id")]

        # Random additional endpoint
        endpoint = random.choice([
            lambda: self.client.get(f"/api/games/basic-info?brandId={self.brand_id}", name="GET /api/games/basic-info"),
            lambda: self.client.get(f"/api/games?{self._base_params()}&onlyPopular=true", name="GET /api/games (popular)"),
            lambda: self.client.get("/api/game-providers/unique", name="GET /api/game-providers/unique"),
            lambda: self.client.get("/api/games/is_state", name="GET /api/games/is_state"),
            lambda: self.client.get("/api/history/last-change", name="GET /api/history/last-change"),
        ])
        endpoint()

        # Per-category + game detail iterations
        for _ in range(10):
            if self.category_ids:
                cat_id = random.choice(self.category_ids)
                self.client.get(
                    f"/api/games?{self._base_params()}&categoryId={cat_id}",
                    name="GET /api/games (category)",
                )
                self.client.get(f"/api/game-brand-category?categoryId={cat_id}", name="GET /api/game-brand-category")

            if self.launch_codes:
                code = random.choice(self.launch_codes)
                self.client.get(f"/api/games/details/{code}", name="GET /api/games/details")
                self.client.get(f"/api/game-descriptions/{code}", name="GET /api/game-descriptions")
                self.client.get(f"/api/game-tag/{code}/{self.brand_id}", name="GET /api/game-tag")

        self.interrupt()


# ---------------------------------------------------------------------------
# User classes
# ---------------------------------------------------------------------------

class LoadTestUser(FastHttpUser):
    """Load test: sequential game-feed-only requests."""
    tasks = [GameFeedOnly]
    wait_time = between(1, 3)
    host = GAME_SERVICE_URL


class SoakTestUser(FastHttpUser):
    """Soak test: full lobby browsing simulation."""
    tasks = [GameLobbyFull]
    wait_time = between(2, 5)
    host = GAME_SERVICE_URL


# ---------------------------------------------------------------------------
# Select scenario based on TEST_TYPE environment variable
# ---------------------------------------------------------------------------

if TEST_TYPE == "soak":
    # Override default user class for soak tests
    LoadTestUser.tasks = [GameLobbyFull]
