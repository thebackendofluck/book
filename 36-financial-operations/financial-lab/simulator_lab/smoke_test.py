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
smoke_test.py -- Health endpoint validation for the financial lab.

Checks 11 health endpoints concurrently using asyncio.gather:
  1. ledger-service /health/ready
  2. payments-service /health
  3. treasury-service /health
  4. reconciliation-service /health
  5. adyen-simulator /health
  6. trustly-simulator /health
  7. paypal-simulator /health
  8. paysafe-simulator /health
  9. skrill-simulator /health
  10. neteller-simulator /health
  11. nuvei-simulator /health
  (+bank-wire-simulator as optional 12th)

Exits 0 if all pass, 1 if any fail.

Chapter 36b: Financial Truth Layer -- lab readiness check
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Optional


import httpx


# ---------------------------------------------------------------------------
# Endpoint configuration
# ---------------------------------------------------------------------------

@dataclass
class HealthEndpoint:
    name: str
    url: str
    path: str = "/health"
    expected_status: int = 200
    required: bool = True


def _get_endpoints() -> list[HealthEndpoint]:
    return [
        HealthEndpoint(
            name="ledger-service",
            url=os.environ.get("LEDGER_URL", "http://localhost:8080"),
            path="/health/ready",
            required=True,
        ),
        HealthEndpoint(
            name="payments-service",
            url=os.environ.get("PAYMENTS_URL", "http://localhost:8081"),
            path="/health",
            required=True,
        ),
        HealthEndpoint(
            name="treasury-service",
            url=os.environ.get("TREASURY_URL", "http://localhost:8082"),
            path="/health",
            required=True,
        ),
        HealthEndpoint(
            name="reconciliation-service",
            url=os.environ.get("RECONCILIATION_URL", "http://localhost:8083"),
            path="/health",
            required=True,
        ),
        HealthEndpoint(
            name="adyen-simulator",
            url=os.environ.get("ADYEN_SIMULATOR_URL", "http://localhost:8100"),
            path="/health",
            required=True,
        ),
        HealthEndpoint(
            name="trustly-simulator",
            url=os.environ.get("TRUSTLY_SIMULATOR_URL", "http://localhost:8101"),
            path="/health",
            required=True,
        ),
        HealthEndpoint(
            name="paypal-simulator",
            url=os.environ.get("PAYPAL_SIMULATOR_URL", "http://localhost:8102"),
            path="/health",
            required=True,
        ),
        HealthEndpoint(
            name="paysafe-simulator",
            url=os.environ.get("PAYSAFE_SIMULATOR_URL", "http://localhost:8103"),
            path="/health",
            required=True,
        ),
        HealthEndpoint(
            name="skrill-simulator",
            url=os.environ.get("SKRILL_SIMULATOR_URL", "http://localhost:8104"),
            path="/health",
            required=True,
        ),
        HealthEndpoint(
            name="neteller-simulator",
            url=os.environ.get("NETELLER_SIMULATOR_URL", "http://localhost:8105"),
            path="/health",
            required=True,
        ),
        HealthEndpoint(
            name="nuvei-simulator",
            url=os.environ.get("NUVEI_SIMULATOR_URL", "http://localhost:8106"),
            path="/health",
            required=True,
        ),
        # Optional 12th endpoint
        HealthEndpoint(
            name="bank-wire-simulator",
            url=os.environ.get("BANK_WIRE_SIMULATOR_URL", "http://localhost:8107"),
            path="/health",
            required=False,  # Not required for basic smoke pass
        ),
    ]


# ---------------------------------------------------------------------------
# Health check logic
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    url: str
    status: Optional[int]
    ok: bool
    error: Optional[str]
    duration_ms: float


async def check_endpoint(
    client: httpx.AsyncClient,
    ep: HealthEndpoint,
) -> CheckResult:
    """Check a single endpoint, returning a CheckResult."""
    import time
    start = time.monotonic()
    full_url = f"{ep.url.rstrip('/')}{ep.path}"
    try:
        resp = await client.get(full_url, timeout=10.0)
        duration_ms = (time.monotonic() - start) * 1000
        ok = resp.status_code == ep.expected_status
        return CheckResult(
            name=ep.name,
            url=full_url,
            status=resp.status_code,
            ok=ok,
            error=None if ok else f"Expected {ep.expected_status}, got {resp.status_code}",
            duration_ms=duration_ms,
        )
    except httpx.ConnectError as exc:
        duration_ms = (time.monotonic() - start) * 1000
        return CheckResult(
            name=ep.name,
            url=full_url,
            status=None,
            ok=False,
            error=f"Connection refused: {exc}",
            duration_ms=duration_ms,
        )
    except httpx.TimeoutException:
        duration_ms = (time.monotonic() - start) * 1000
        return CheckResult(
            name=ep.name,
            url=full_url,
            status=None,
            ok=False,
            error="Timeout after 10s",
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        duration_ms = (time.monotonic() - start) * 1000
        return CheckResult(
            name=ep.name,
            url=full_url,
            status=None,
            ok=False,
            error=str(exc),
            duration_ms=duration_ms,
        )


async def run_smoke_tests() -> tuple[list[CheckResult], list[CheckResult]]:
    """
    Run all health checks concurrently using asyncio.gather.
    Returns (passed, failed) lists.
    """
    endpoints = _get_endpoints()
    async with httpx.AsyncClient() as client:
        results: list[CheckResult] = await asyncio.gather(
            *[check_endpoint(client, ep) for ep in endpoints]
        )

    # Separate required failures from optional ones
    required_map = {ep.name: ep.required for ep in endpoints}
    passed = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok and required_map.get(r.name, True)]

    return passed, failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("Financial Lab Smoke Test")
    print("=" * 50)

    passed, failed = asyncio.run(run_smoke_tests())
    all_results = sorted(passed + failed, key=lambda r: r.name)

    for r in all_results:
        status_icon = "PASS" if r.ok else "FAIL"
        status_str = str(r.status) if r.status is not None else "N/A"
        print(f"  [{status_icon}] {r.name:<30} HTTP {status_str:<5} ({r.duration_ms:.0f}ms)")
        if r.error:
            print(f"         Error: {r.error}")

    print("=" * 50)
    print(f"Results: {len(passed)} passed, {len(failed)} failed")

    if failed:
        print("\nFAILED endpoints:")
        for r in failed:
            print(f"  - {r.name}: {r.url}")
            print(f"    Error: {r.error}")
        return 1

    print("\nAll required services are healthy. Lab is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
