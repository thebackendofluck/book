#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 22b, Developer Inner-Loop Experience in Containerized iGaming Pla.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Post-deploy smoke tests for staging validation."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def request_json(url: str, api_key: str, timeout: int) -> dict:
    request = urllib.request.Request(url)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return json.loads(body or "{}")


def check_endpoint(base_url: str, path: str, api_key: str, timeout: int) -> None:
    """Require an explicit healthy status field.

    A missing status must fail. Defaulting it to "ok" meant a 200 with an empty
    body, or a reverse proxy answering in place of a dead service, counted as a
    passing smoke test.
    """
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    payload = request_json(url, api_key, timeout)

    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} did not return a JSON object")

    if "status" not in payload:
        raise RuntimeError(f"{url} returned no status field: {payload!r}")

    status = str(payload["status"]).lower()
    if status not in {"ok", "healthy", "pass"}:
        raise RuntimeError(f"{url} returned unhealthy status: {status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run staging API smoke tests")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    try:
        check_endpoint(args.base_url, "/health", args.api_key, args.timeout)
        check_endpoint(args.base_url, "/api/games/health", args.api_key, args.timeout)
        check_endpoint(args.base_url, "/api/payments/sandbox/health", args.api_key, args.timeout)
    except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        return 1

    print("Smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
