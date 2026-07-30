#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Domain observability smoke checks for the current casino runtime.

Validates the operational slice already implemented in new-platform:
- /api/v2/health returns 200 and exposes core domains
- /api/v2/dashboard/summary returns the expected contract
- sequential summary reads warm Redis and expose `x-dashboard-cache: redis`
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class CheckResult:
    name: str
    ok: bool
    details: dict[str, Any]


def _get_json(url: str) -> tuple[int, dict[str, str], dict[str, Any]]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        status = response.status
        headers = {k.lower(): v for k, v in response.headers.items()}
        payload = json.loads(response.read().decode("utf-8"))
    return status, headers, payload


def check_health(base_url: str) -> CheckResult:
    status, headers, payload = _get_json(f"{base_url}/api/v2/health")
    services = payload.get("services", {})
    required_services = {"pam", "wallet", "compliance", "gal", "ops"}
    missing_services = sorted(required_services - set(services.keys()))
    infra_ok = all(
        isinstance(payload.get(section), dict) and payload[section].get("status") in {"connected", "disabled"}
        for section in ("database", "redis", "kafka")
    )
    ok = status == 200 and not missing_services and payload.get("status") == "healthy" and infra_ok
    return CheckResult(
        name="health",
        ok=ok,
        details={
            "status_code": status,
            "status": payload.get("status"),
            "missing_services": missing_services,
            "database_status": payload.get("database", {}).get("status"),
            "redis_status": payload.get("redis", {}).get("status"),
            "kafka_status": payload.get("kafka", {}).get("status"),
            "correlation_id": headers.get("x-correlation-id"),
        },
    )


def check_dashboard_summary(base_url: str) -> CheckResult:
    status, headers, payload = _get_json(f"{base_url}/api/v2/dashboard/summary")
    stats = payload.get("stats", {})
    ok = (
        status == 200
        and payload.get("source") in {"live", "redis"}
        and isinstance(stats.get("players"), dict)
        and isinstance(stats.get("wallet_events"), dict)
        and isinstance(payload.get("health"), dict)
    )
    return CheckResult(
        name="dashboard_summary_contract",
        ok=ok,
        details={
            "status_code": status,
            "source": payload.get("source"),
            "generated_at": payload.get("generated_at"),
            "cache_header": headers.get("x-dashboard-cache"),
        },
    )


def check_dashboard_cache_warmup(base_url: str, attempts: int = 5) -> CheckResult:
    observations: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        status, headers, _ = _get_json(f"{base_url}/api/v2/dashboard/summary")
        observations.append(
            {
                "attempt": attempt,
                "status_code": status,
                "cache": headers.get("x-dashboard-cache"),
            }
        )

    all_200 = all(item["status_code"] == 200 for item in observations)
    saw_redis = any(item["cache"] == "redis" for item in observations)
    saw_live = any(item["cache"] == "live" for item in observations)
    ok = all_200 and saw_redis
    return CheckResult(
        name="dashboard_cache_warmup",
        ok=ok,
        details={
            "observations": observations,
            "saw_live": saw_live,
            "saw_redis": saw_redis,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default="https://new.acmetocasino.com",
        help="Platform base URL, for example https://new.acmetocasino.com",
    )
    args = parser.parse_args()

    try:
        results = [
            check_health(args.base_url.rstrip("/")),
            check_dashboard_summary(args.base_url.rstrip("/")),
            check_dashboard_cache_warmup(args.base_url.rstrip("/")),
        ]
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "base_url": args.base_url,
                },
                indent=2,
            )
        )
        return 1

    output = {
        "ok": all(result.ok for result in results),
        "base_url": args.base_url,
        "checks": [
            {"name": result.name, "ok": result.ok, **result.details}
            for result in results
        ],
    }
    print(json.dumps(output, indent=2))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
