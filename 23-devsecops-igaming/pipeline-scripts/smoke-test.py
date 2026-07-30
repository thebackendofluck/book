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

"""Run a minimal HTTP smoke test after staging deployment."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Deployment smoke test")
    parser.add_argument("--env", required=True)
    parser.add_argument("--url", default="")
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    base_url = args.url or os.environ.get(f"{args.env.upper()}_BASE_URL")
    if not base_url:
        print(f"Missing --url or {args.env.upper()}_BASE_URL", file=sys.stderr)
        return 1

    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/health", timeout=args.timeout) as response:
            if response.status >= 400:
                print(f"Health check returned HTTP {response.status}", file=sys.stderr)
                return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        return 1

    print(f"{args.env} smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
