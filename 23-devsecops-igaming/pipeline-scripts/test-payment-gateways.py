#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Check that configured payment gateway endpoints are reachable."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from urllib.parse import urlparse


def endpoint_for(provider: str, env: str) -> str:
    env_key = f"{provider.upper()}_{env.upper()}_URL".replace("-", "_")
    return os.environ.get(env_key, f"https://{provider}.{env}.payments.example.test/health")


def check_tcp(url: str, timeout: int) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        raise ValueError(f"invalid endpoint: {url}")

    with socket.create_connection((host, port), timeout=timeout):
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Payment provider connectivity check")
    parser.add_argument("--env", required=True)
    parser.add_argument("--providers", required=True, help="Comma-separated provider list")
    parser.add_argument("--timeout", type=int, default=5)
    args = parser.parse_args()

    failures: list[str] = []
    for provider in [item.strip() for item in args.providers.split(",") if item.strip()]:
        endpoint = endpoint_for(provider, args.env)
        try:
            check_tcp(endpoint, args.timeout)
            print(f"{provider}: reachable ({endpoint})")
        except OSError as exc:
            failures.append(f"{provider}: {exc}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
