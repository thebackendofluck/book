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
Load simulation tests for the Brazilian Betting Platform.

Uses asyncio + aiohttp for high-concurrency HTTP requests against real services.
No external load-testing tools (e.g., locust) are used.

Each test asserts:
  - p95 response latency < 500ms
  - 0 unexpected errors (HTTP 5xx or connection failures)

Concurrency levels are tunable via environment variables:
  LOAD_CONCURRENCY_REGISTRATIONS  (default: 100)
  LOAD_CONCURRENCY_BETS           (default: 100)
  LOAD_CONCURRENCY_DEPOSITS       (default: 50)
  LOAD_MIXED_DURATION_SECS        (default: 30)

Run with:
    pytest test_load_simulation.py -v --asyncio-mode=auto -s
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import List

import aiohttp
import pytest

# Sibling conftest.py exports helpers; add its directory to sys.path
# so `from conftest import ...` works in both prepend and importlib
# import modes.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from conftest import SERVICE_URLS, generate_valid_cpf


# ---------------------------------------------------------------------------
# Constants / config
# ---------------------------------------------------------------------------

CONCURRENCY_REGISTRATIONS = int(os.environ.get("LOAD_CONCURRENCY_REGISTRATIONS", "100"))
CONCURRENCY_BETS          = int(os.environ.get("LOAD_CONCURRENCY_BETS",           "100"))
CONCURRENCY_DEPOSITS      = int(os.environ.get("LOAD_CONCURRENCY_DEPOSITS",        "50"))
MIXED_DURATION_SECS       = int(os.environ.get("LOAD_MIXED_DURATION_SECS",         "30"))

P95_THRESHOLD_MS = 500  # milliseconds


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class LoadResult:
    latencies_ms: List[float] = field(default_factory=list)
    errors:       List[str]   = field(default_factory=list)
    success:      int = 0
    total:        int = 0

    def p95(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lats = sorted(self.latencies_ms)
        idx = max(0, int(len(sorted_lats) * 0.95) - 1)
        return sorted_lats[idx]

    def p99(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lats = sorted(self.latencies_ms)
        idx = max(0, int(len(sorted_lats) * 0.99) - 1)
        return sorted_lats[idx]

    def mean(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0.0

    def error_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return len(self.errors) / self.total

    def summary(self, label: str) -> str:
        return (
            f"[{label}] total={self.total} success={self.success} "
            f"errors={len(self.errors)} "
            f"mean={self.mean():.1f}ms p95={self.p95():.1f}ms p99={self.p99():.1f}ms"
        )


# ---------------------------------------------------------------------------
# aiohttp session factory
# ---------------------------------------------------------------------------

def _make_connector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(limit=256, limit_per_host=128, force_close=False)


def _player_payload(cpf: str) -> dict:
    return {
        "cpf": cpf,
        "full_name": f"Load Test Player {cpf[-4:]}",
        "email": f"load_{cpf}@betbr-load.test",
        "phone": "+5511900000001",
        "date_of_birth": "1990-05-20",
        "address": {
            "street": "Rua do Teste",
            "number": "1",
            "city": "São Paulo",
            "state": "SP",
            "zip_code": "01310-100",
        },
    }


def _deposit_payload(amount: float = 100.0) -> dict:
    return {
        "amount": amount,
        "payment_method": "pix",
        "description": "load test deposit",
    }


def _bet_payload(cpf: str, stake: float = 10.0) -> dict:
    event_id = f"evt-load-{uuid.uuid4().hex[:8]}"
    sel_id   = f"sel-load-{uuid.uuid4().hex[:8]}"
    return {
        "cpf": cpf,
        "event_id": event_id,
        "selections": [
            {
                "event_id": event_id,
                "market_id": f"mkt-{uuid.uuid4().hex[:8]}",
                "selection_id": sel_id,
                "odds": 1.9,
            }
        ],
        "stake": stake,
        "bet_type": "single",
    }


# ---------------------------------------------------------------------------
# Generic load runner
# ---------------------------------------------------------------------------

async def _run_concurrent(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    payloads: List[dict],
    result: LoadResult,
    semaphore: asyncio.Semaphore,
) -> None:
    """Fire all requests in payloads concurrently and record latencies."""
    async def _one(payload: dict) -> None:
        async with semaphore:
            start = time.monotonic()
            try:
                async with getattr(session, method)(url, json=payload) as resp:
                    await resp.read()
                    elapsed = (time.monotonic() - start) * 1000
                    result.total += 1
                    result.latencies_ms.append(elapsed)
                    if resp.status < 500:
                        result.success += 1
                    else:
                        result.errors.append(
                            f"HTTP {resp.status} from {url}"
                        )
            except aiohttp.ClientError as exc:
                elapsed = (time.monotonic() - start) * 1000
                result.total += 1
                result.latencies_ms.append(elapsed)
                result.errors.append(str(exc))

    await asyncio.gather(*[_one(p) for p in payloads])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_100_concurrent_registrations() -> None:
    """
    Fire CONCURRENCY_REGISTRATIONS parallel registration requests to PAM.
    Assert p95 < 500ms and 0 server errors.
    """
    n = CONCURRENCY_REGISTRATIONS
    cpfs = [generate_valid_cpf() for _ in range(n)]
    payloads = [_player_payload(cpf) for cpf in cpfs]
    result = LoadResult()
    semaphore = asyncio.Semaphore(50)  # cap concurrent open connections

    url = SERVICE_URLS["pam"] + "/players/register"
    connector = _make_connector()
    timeout = aiohttp.ClientTimeout(total=30, connect=10)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        await _run_concurrent(session, "post", url, payloads, result, semaphore)

    print(f"\n{result.summary('100 concurrent registrations')}")
    assert result.total == n, f"Not all requests completed: {result.total}/{n}"
    assert len(result.errors) == 0, (
        f"Errors during concurrent registrations:\n" + "\n".join(result.errors[:10])
    )
    assert result.p95() < P95_THRESHOLD_MS, (
        f"p95 latency {result.p95():.1f}ms exceeds {P95_THRESHOLD_MS}ms threshold"
    )


@pytest.mark.asyncio
async def test_100_concurrent_bets() -> None:
    """
    Pre-register CONCURRENCY_BETS players then fire parallel bet requests.
    Assert p95 < 500ms and 0 server errors.
    """
    n = CONCURRENCY_BETS
    pam_url = SERVICE_URLS["pam"]
    bet_url = SERVICE_URLS["betting_engine"] + "/bets"
    wallet_url = SERVICE_URLS["wallet"]

    connector = _make_connector()
    timeout = aiohttp.ClientTimeout(total=30, connect=10)

    cpfs: list[str] = []

    # --- Setup: register and fund players ---
    reg_result = LoadResult()
    semaphore = asyncio.Semaphore(30)
    temp_cpfs = [generate_valid_cpf() for _ in range(n)]
    reg_payloads = [_player_payload(cpf) for cpf in temp_cpfs]

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        await _run_concurrent(
            session, "post", pam_url + "/players/register",
            reg_payloads, reg_result, semaphore,
        )

    # Collect successfully registered CPFs
    cpfs = temp_cpfs  # assume all registered; service may accept all in mock mode
    # Fund each player (fire-and-forget; errors are best-effort)
    dep_result = LoadResult()
    dep_payloads = [_deposit_payload(200.0) for _ in cpfs]
    dep_urls = [wallet_url + f"/wallet/{cpf}/deposit" for cpf in cpfs]

    connector2 = _make_connector()
    async with aiohttp.ClientSession(connector=connector2, timeout=timeout) as session:
        async def _deposit_one(cpf: str, sem: asyncio.Semaphore) -> None:
            async with sem:
                try:
                    async with session.post(
                        wallet_url + f"/wallet/{cpf}/deposit",
                        json=_deposit_payload(200.0),
                    ) as r:
                        await r.read()
                except Exception:
                    pass

        await asyncio.gather(*[
            _deposit_one(cpf, asyncio.Semaphore(30)) for cpf in cpfs
        ])

    # --- Load: concurrent bets ---
    bet_result = LoadResult()
    bet_payloads = [_bet_payload(cpf, stake=10.0) for cpf in cpfs]
    bet_semaphore = asyncio.Semaphore(50)

    connector3 = _make_connector()
    async with aiohttp.ClientSession(connector=connector3, timeout=timeout) as session:
        await _run_concurrent(
            session, "post", bet_url, bet_payloads, bet_result, bet_semaphore,
        )

    print(f"\n{bet_result.summary('100 concurrent bets')}")
    assert bet_result.total == n, f"Not all bet requests completed: {bet_result.total}/{n}"
    assert len(bet_result.errors) == 0, (
        f"Errors during concurrent bets:\n" + "\n".join(bet_result.errors[:10])
    )
    assert bet_result.p95() < P95_THRESHOLD_MS, (
        f"p95 latency {bet_result.p95():.1f}ms exceeds {P95_THRESHOLD_MS}ms"
    )


@pytest.mark.asyncio
async def test_50_concurrent_pix_deposits() -> None:
    """
    Pre-register CONCURRENCY_DEPOSITS players and fire parallel PIX deposit requests.
    Assert p95 < 500ms and 0 server errors.
    """
    n = CONCURRENCY_DEPOSITS
    pam_url = SERVICE_URLS["pam"]
    wallet_url = SERVICE_URLS["wallet"]

    connector = _make_connector()
    timeout = aiohttp.ClientTimeout(total=30, connect=10)

    # Setup: register players
    cpfs = [generate_valid_cpf() for _ in range(n)]
    reg_sem = asyncio.Semaphore(25)
    reg_payloads = [_player_payload(cpf) for cpf in cpfs]

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        reg_result = LoadResult()
        await _run_concurrent(
            session, "post", pam_url + "/players/register",
            reg_payloads, reg_result, reg_sem,
        )

    # Load: concurrent deposits
    dep_result = LoadResult()
    dep_payloads = [_deposit_payload(100.0) for _ in cpfs]
    dep_semaphore = asyncio.Semaphore(25)

    connector2 = _make_connector()

    async def _deposit_for_cpf(
        sess: aiohttp.ClientSession,
        cpf: str,
        sem: asyncio.Semaphore,
        res: LoadResult,
    ) -> None:
        url = wallet_url + f"/wallet/{cpf}/deposit"
        async with sem:
            start = time.monotonic()
            try:
                async with sess.post(url, json=_deposit_payload(100.0)) as resp:
                    await resp.read()
                    elapsed = (time.monotonic() - start) * 1000
                    res.total += 1
                    res.latencies_ms.append(elapsed)
                    if resp.status < 500:
                        res.success += 1
                    else:
                        res.errors.append(f"HTTP {resp.status} — {cpf}")
            except aiohttp.ClientError as exc:
                elapsed = (time.monotonic() - start) * 1000
                res.total += 1
                res.latencies_ms.append(elapsed)
                res.errors.append(str(exc))

    async with aiohttp.ClientSession(connector=connector2, timeout=timeout) as session:
        await asyncio.gather(*[
            _deposit_for_cpf(session, cpf, dep_semaphore, dep_result)
            for cpf in cpfs
        ])

    print(f"\n{dep_result.summary('50 concurrent PIX deposits')}")
    assert dep_result.total == n, (
        f"Not all deposit requests completed: {dep_result.total}/{n}"
    )
    assert len(dep_result.errors) == 0, (
        f"Errors during concurrent deposits:\n" + "\n".join(dep_result.errors[:10])
    )
    assert dep_result.p95() < P95_THRESHOLD_MS, (
        f"p95 latency {dep_result.p95():.1f}ms exceeds {P95_THRESHOLD_MS}ms"
    )


@pytest.mark.asyncio
async def test_mixed_workload_30_seconds() -> None:
    """
    Sustain a mixed workload for MIXED_DURATION_SECS seconds with concurrent:
      - Player registrations
      - Balance checks
      - Odds queries
      - Bet placements
    Assert p95 < 500ms across all request types and 0 server errors.
    """
    duration = MIXED_DURATION_SECS
    results: dict[str, LoadResult] = {
        "registration": LoadResult(),
        "balance":      LoadResult(),
        "odds":         LoadResult(),
        "bet":          LoadResult(),
    }
    stop_event = asyncio.Event()
    semaphore = asyncio.Semaphore(40)

    pam_url     = SERVICE_URLS["pam"]
    wallet_url  = SERVICE_URLS["wallet"]
    odds_url    = SERVICE_URLS["odds_feed"]
    betting_url = SERVICE_URLS["betting_engine"]

    # Pre-register a pool of players to use in the mixed workload
    pool_size = 50
    pool_cpfs = [generate_valid_cpf() for _ in range(pool_size)]

    connector = _make_connector()
    timeout = aiohttp.ClientTimeout(total=20, connect=10)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as setup_session:
        reg_result = LoadResult()
        await _run_concurrent(
            setup_session, "post", pam_url + "/players/register",
            [_player_payload(cpf) for cpf in pool_cpfs],
            reg_result,
            asyncio.Semaphore(25),
        )

    import random

    async def _registration_worker(
        sess: aiohttp.ClientSession,
        res: LoadResult,
    ) -> None:
        while not stop_event.is_set():
            cpf = generate_valid_cpf()
            async with semaphore:
                start = time.monotonic()
                try:
                    async with sess.post(
                        pam_url + "/players/register",
                        json=_player_payload(cpf),
                    ) as r:
                        await r.read()
                        elapsed = (time.monotonic() - start) * 1000
                        res.total += 1
                        res.latencies_ms.append(elapsed)
                        if r.status < 500:
                            res.success += 1
                        else:
                            res.errors.append(f"reg HTTP {r.status}")
                except aiohttp.ClientError as exc:
                    elapsed = (time.monotonic() - start) * 1000
                    res.total += 1
                    res.latencies_ms.append(elapsed)
                    res.errors.append(f"reg error: {exc}")
            await asyncio.sleep(0.05)

    async def _balance_worker(
        sess: aiohttp.ClientSession,
        res: LoadResult,
    ) -> None:
        while not stop_event.is_set():
            cpf = random.choice(pool_cpfs)
            async with semaphore:
                start = time.monotonic()
                try:
                    async with sess.get(wallet_url + f"/wallet/{cpf}/balance") as r:
                        await r.read()
                        elapsed = (time.monotonic() - start) * 1000
                        res.total += 1
                        res.latencies_ms.append(elapsed)
                        if r.status < 500:
                            res.success += 1
                        else:
                            res.errors.append(f"balance HTTP {r.status}")
                except aiohttp.ClientError as exc:
                    elapsed = (time.monotonic() - start) * 1000
                    res.total += 1
                    res.latencies_ms.append(elapsed)
                    res.errors.append(f"balance error: {exc}")
            await asyncio.sleep(0.02)

    async def _odds_worker(
        sess: aiohttp.ClientSession,
        res: LoadResult,
    ) -> None:
        while not stop_event.is_set():
            async with semaphore:
                start = time.monotonic()
                try:
                    async with sess.get(odds_url + "/odds/sport/football") as r:
                        await r.read()
                        elapsed = (time.monotonic() - start) * 1000
                        res.total += 1
                        res.latencies_ms.append(elapsed)
                        if r.status < 500:
                            res.success += 1
                        else:
                            res.errors.append(f"odds HTTP {r.status}")
                except aiohttp.ClientError as exc:
                    elapsed = (time.monotonic() - start) * 1000
                    res.total += 1
                    res.latencies_ms.append(elapsed)
                    res.errors.append(f"odds error: {exc}")
            await asyncio.sleep(0.03)

    async def _bet_worker(
        sess: aiohttp.ClientSession,
        res: LoadResult,
    ) -> None:
        while not stop_event.is_set():
            cpf = random.choice(pool_cpfs)
            async with semaphore:
                start = time.monotonic()
                try:
                    async with sess.post(
                        betting_url + "/bets",
                        json=_bet_payload(cpf, stake=5.0),
                    ) as r:
                        await r.read()
                        elapsed = (time.monotonic() - start) * 1000
                        res.total += 1
                        res.latencies_ms.append(elapsed)
                        if r.status < 500:
                            res.success += 1
                        else:
                            res.errors.append(f"bet HTTP {r.status}")
                except aiohttp.ClientError as exc:
                    elapsed = (time.monotonic() - start) * 1000
                    res.total += 1
                    res.latencies_ms.append(elapsed)
                    res.errors.append(f"bet error: {exc}")
            await asyncio.sleep(0.04)

    # Run mixed workload
    mixed_connector = _make_connector()
    async with aiohttp.ClientSession(
        connector=mixed_connector, timeout=aiohttp.ClientTimeout(total=20, connect=10)
    ) as session:
        workers = [
            asyncio.create_task(_registration_worker(session, results["registration"])),
            asyncio.create_task(_registration_worker(session, results["registration"])),
            asyncio.create_task(_balance_worker(session, results["balance"])),
            asyncio.create_task(_balance_worker(session, results["balance"])),
            asyncio.create_task(_balance_worker(session, results["balance"])),
            asyncio.create_task(_odds_worker(session, results["odds"])),
            asyncio.create_task(_odds_worker(session, results["odds"])),
            asyncio.create_task(_bet_worker(session, results["bet"])),
            asyncio.create_task(_bet_worker(session, results["bet"])),
        ]

        await asyncio.sleep(duration)
        stop_event.set()

        # Wait for all workers to finish in-flight requests
        await asyncio.gather(*workers, return_exceptions=True)

    # Summarise and assert
    all_errors: list[str] = []
    for label, res in results.items():
        print(f"\n{res.summary(f'mixed/{label}')}")
        all_errors.extend(res.errors)

    # At least some requests should have been made
    total_requests = sum(r.total for r in results.values())
    assert total_requests > 0, "No requests were fired during mixed workload"

    # No 5xx errors allowed
    assert len(all_errors) == 0, (
        f"Server errors during mixed workload:\n" + "\n".join(all_errors[:20])
    )

    # p95 per category
    for label, res in results.items():
        if res.latencies_ms:
            p95 = res.p95()
            assert p95 < P95_THRESHOLD_MS, (
                f"[{label}] p95 {p95:.1f}ms exceeds {P95_THRESHOLD_MS}ms threshold"
            )


# ---------------------------------------------------------------------------
# Odds Feed throughput
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odds_update_throughput() -> None:
    """
    Push 200 rapid odds updates and assert p95 < 500ms and 0 errors.
    """
    n = 200
    odds_url = SERVICE_URLS["odds_feed"] + "/odds/update"
    connector = _make_connector()
    timeout = aiohttp.ClientTimeout(total=15, connect=5)

    payloads = [
        {
            "event_id": f"evt-tput-{uuid.uuid4().hex[:8]}",
            "market_id": f"mkt-{uuid.uuid4().hex[:8]}",
            "selection_id": f"sel-{uuid.uuid4().hex[:8]}",
            "new_odds": round(1.1 + (i % 40) * 0.1, 2),
            "sport": "football",
            "updated_at": "2025-01-01T12:00:00Z",
            "source": "throughput_test",
        }
        for i in range(n)
    ]

    result = LoadResult()
    semaphore = asyncio.Semaphore(50)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        await _run_concurrent(session, "post", odds_url, payloads, result, semaphore)

    print(f"\n{result.summary('odds update throughput')}")
    assert result.total == n
    assert len(result.errors) == 0, (
        "Errors during odds update throughput:\n" + "\n".join(result.errors[:10])
    )
    assert result.p95() < P95_THRESHOLD_MS, (
        f"p95 {result.p95():.1f}ms exceeds {P95_THRESHOLD_MS}ms"
    )
