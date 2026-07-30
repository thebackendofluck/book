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
Shared pytest fixtures for Brazilian Betting Platform functional tests.

All fixtures target real running services started by docker-compose.integration.yml.
No mocks are used — tests hit actual HTTP endpoints.

Environment variables (with defaults):
  PAM_URL                http://127.0.0.1:8010
  RESPONSIBLE_GAMING_URL http://127.0.0.1:8020
  BETTING_ENGINE_URL     http://127.0.0.1:8080
  WALLET_URL             http://127.0.0.1:8081
  SETTLEMENT_URL         http://127.0.0.1:8082
  ODDS_FEED_URL          http://127.0.0.1:8083
  BONUS_ENGINE_URL       http://127.0.0.1:8030
  CASINO_AGGREGATION_URL http://127.0.0.1:8040
  HEALTH_TIMEOUT_SECS    120
"""

from __future__ import annotations

import asyncio
import os
import random
import string
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, Generator

import httpx
import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Service URL configuration
# ---------------------------------------------------------------------------

SERVICE_URLS: Dict[str, str] = {
    "pam":                os.environ.get("PAM_URL",                "http://127.0.0.1:8010"),
    "responsible_gaming": os.environ.get("RESPONSIBLE_GAMING_URL", "http://127.0.0.1:8020"),
    "betting_engine":     os.environ.get("BETTING_ENGINE_URL",     "http://127.0.0.1:8080"),
    "wallet":             os.environ.get("WALLET_URL",             "http://127.0.0.1:8081"),
    "settlement":         os.environ.get("SETTLEMENT_URL",         "http://127.0.0.1:8082"),
    "odds_feed":          os.environ.get("ODDS_FEED_URL",          "http://127.0.0.1:8083"),
    "bonus_engine":       os.environ.get("BONUS_ENGINE_URL",       "http://127.0.0.1:8030"),
    "casino_aggregation": os.environ.get("CASINO_AGGREGATION_URL", "http://127.0.0.1:8040"),
}

HEALTH_TIMEOUT_SECS = int(os.environ.get("HEALTH_TIMEOUT_SECS", "120"))
HEALTH_POLL_INTERVAL = 3  # seconds between health check polls
RUN_FUNCTIONAL_TESTS = os.environ.get("RUN_CH46_FUNCTIONAL") == "1"


# ---------------------------------------------------------------------------
# CPF generation utilities
# ---------------------------------------------------------------------------

def _cpf_digit(partial: list[int]) -> int:
    """Compute a single CPF verification digit."""
    n = len(partial) + 1
    total = sum(v * (n - i) for i, v in enumerate(partial))
    remainder = total % 11
    return 0 if remainder < 2 else 11 - remainder


def generate_valid_cpf() -> str:
    """Generate a random valid CPF (bare 11-digit string)."""
    base = [random.randint(0, 9) for _ in range(9)]
    d1 = _cpf_digit(base)
    d2 = _cpf_digit(base + [d1])
    digits = base + [d1, d2]
    return "".join(str(d) for d in digits)


def format_cpf(cpf: str) -> str:
    """Format a bare CPF string as NNN.NNN.NNN-DD."""
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


# ---------------------------------------------------------------------------
# API client dataclass
# ---------------------------------------------------------------------------

@dataclass
class APIClients:
    """Container for async HTTP clients for each microservice."""
    pam:                httpx.AsyncClient
    responsible_gaming: httpx.AsyncClient
    betting_engine:     httpx.AsyncClient
    wallet:             httpx.AsyncClient
    settlement:         httpx.AsyncClient
    odds_feed:          httpx.AsyncClient
    bonus_engine:       httpx.AsyncClient
    casino_aggregation: httpx.AsyncClient

    async def close_all(self) -> None:
        for attr in self.__dataclass_fields__:
            client: httpx.AsyncClient = getattr(self, attr)
            await client.aclose()


# ---------------------------------------------------------------------------
# Health-check helper
# ---------------------------------------------------------------------------

async def _wait_for_service(name: str, url: str, timeout: int) -> None:
    """Poll /health until service responds 200 or timeout is exceeded."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url}/health")
                if resp.status_code < 500:
                    return
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_error = exc
        await asyncio.sleep(HEALTH_POLL_INTERVAL)
    raise TimeoutError(
        f"Service '{name}' at {url}/health did not become healthy within "
        f"{timeout}s. Last error: {last_error}"
    )


async def _wait_for_all_services(timeout: int = HEALTH_TIMEOUT_SECS) -> None:
    """Wait for every microservice to report healthy concurrently."""
    tasks = [
        _wait_for_service(name, url, timeout)
        for name, url in SERVICE_URLS.items()
    ]
    await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Session-scoped fixture: wait for services to be healthy
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def docker_compose_up(event_loop: asyncio.AbstractEventLoop) -> Generator[None, None, None]:
    """
    Block until all services respond healthy before any test runs.

    Does NOT start docker-compose — run_tests_5x.sh handles that.
    This fixture only waits for readiness.
    """
    if not RUN_FUNCTIONAL_TESTS:
        pytest.skip(
            "chapter-46 functional microservice tests require a running integration stack; "
            "set RUN_CH46_FUNCTIONAL=1 to execute them."
        )
    event_loop.run_until_complete(_wait_for_all_services(HEALTH_TIMEOUT_SECS))
    yield
    # Teardown is handled by the shell script; nothing to do here.


# ---------------------------------------------------------------------------
# Function-scoped fixture: HTTP clients
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def api_clients() -> AsyncGenerator[APIClients, None]:
    """Provide one async HTTP client per microservice."""
    timeout = httpx.Timeout(30.0, connect=10.0)
    clients = APIClients(
        pam=httpx.AsyncClient(
            base_url=SERVICE_URLS["pam"], timeout=timeout
        ),
        responsible_gaming=httpx.AsyncClient(
            base_url=SERVICE_URLS["responsible_gaming"], timeout=timeout
        ),
        betting_engine=httpx.AsyncClient(
            base_url=SERVICE_URLS["betting_engine"], timeout=timeout
        ),
        wallet=httpx.AsyncClient(
            base_url=SERVICE_URLS["wallet"], timeout=timeout
        ),
        settlement=httpx.AsyncClient(
            base_url=SERVICE_URLS["settlement"], timeout=timeout
        ),
        odds_feed=httpx.AsyncClient(
            base_url=SERVICE_URLS["odds_feed"], timeout=timeout
        ),
        bonus_engine=httpx.AsyncClient(
            base_url=SERVICE_URLS["bonus_engine"], timeout=timeout
        ),
        casino_aggregation=httpx.AsyncClient(
            base_url=SERVICE_URLS["casino_aggregation"], timeout=timeout
        ),
    )
    try:
        yield clients
    finally:
        await clients.close_all()


# ---------------------------------------------------------------------------
# CPF fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def test_cpf() -> str:
    """Generate a single valid test CPF (bare 11-digit string)."""
    return generate_valid_cpf()


@pytest.fixture
def test_cpfs() -> list[str]:
    """Generate a batch of 200 unique valid test CPFs."""
    cpfs: set[str] = set()
    while len(cpfs) < 200:
        cpfs.add(generate_valid_cpf())
    return list(cpfs)


# ---------------------------------------------------------------------------
# Cleanup fixture — resets state between tests via service APIs
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=False)
async def cleanup(api_clients: APIClients) -> AsyncGenerator[None, None]:
    """
    Yield control to the test, then attempt best-effort cleanup.

    Each service exposes DELETE /test/cleanup (integration env only).
    Failures are silently ignored so that a missing endpoint doesn't block
    the test run.
    """
    registered_cpfs: list[str] = []
    event_ids: list[str] = []

    # Provide helpers to the test via the fixture object if needed
    yield {"registered_cpfs": registered_cpfs, "event_ids": event_ids}

    # Best-effort cleanup — delete registered test players from PAM
    for cpf in registered_cpfs:
        try:
            await api_clients.pam.delete(f"/players/{cpf}")
        except Exception:
            pass

    # Best-effort: tell responsible-gaming to clear test CPF data
    for cpf in registered_cpfs:
        try:
            await api_clients.responsible_gaming.delete(
                f"/test/cleanup/{cpf}"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Shared player registration helper (not a fixture — used inside tests)
# ---------------------------------------------------------------------------

async def register_test_player(
    client: httpx.AsyncClient,
    cpf: str | None = None,
) -> dict:
    """Register a player and return the response JSON.

    Uses the flat field schema required by PAM v2:
      phone_br, address_cep, address_street, address_number,
      address_city, address_state, document_type, document_number, lgpd_consent.
    """
    if cpf is None:
        cpf = generate_valid_cpf()

    payload = {
        "cpf": cpf,
        "full_name": "Jogador Teste " + cpf[-4:],
        "email": f"test_{cpf}@betbr-integration.test",
        "phone_br": "+5511999990000",
        "date_of_birth": "1990-06-15",
        "address_cep": "01310-100",
        "address_street": "Rua das Flores",
        "address_number": "100",
        "address_city": "São Paulo",
        "address_state": "SP",
        "document_type": "rg",
        "document_number": "RG" + cpf[:7],
        "lgpd_consent": True,
    }
    resp = await client.post("/players/register", json=payload)
    resp.raise_for_status()
    return resp.json()
