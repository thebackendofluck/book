#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23c, Secrets Management and Git Hygiene for iGaming Engineering.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""audit-bao-leases.py — Check OpenBao for expiring or orphaned leases/tokens.

Purpose:
    Connects to an OpenBao (or Vault) instance and audits all token accessors
    for orphaned tokens and tokens expiring within a configurable warning
    threshold. Sends a Slack alert if any warnings are found.

Prerequisites:
    - VAULT_ADDR environment variable set (e.g., https://bao.yourbrand.com:8200)
    - VAULT_TOKEN environment variable set (with sys/leases and auth/token permissions)
    - requests library (pip install requests)

Usage:
    python3 audit-bao-leases.py [--warn-days 7] [--slack-webhook URL]

Examples:
    VAULT_ADDR=https://bao.internal:8200 VAULT_TOKEN=s.xxx python3 audit-bao-leases.py
    python3 audit-bao-leases.py --warn-days 3 --slack-webhook https://hooks.slack.com/...
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    print("ERROR: requests library required. Install: pip install requests", file=sys.stderr)
    sys.exit(1)


def vault_request(method, path, token, addr, payload=None):
    """Make an authenticated request to the Vault/OpenBao API."""
    url = f"{addr}/v1/{path}"
    headers = {"X-Vault-Token": token}
    try:
        resp = requests.request(method, url, headers=headers, json=payload, timeout=10)
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Cannot connect to {addr}. Is the server running?", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"ERROR: Request to {url} timed out.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 403:
        print(f"ERROR: Permission denied for {path}. Check token policy.", file=sys.stderr)
        sys.exit(1)
    return resp


def list_token_accessors(token, addr):
    """List all token accessors."""
    resp = vault_request("LIST", "auth/token/accessors", token, addr)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json().get("data", {}).get("keys", [])


def lookup_token_accessor(token, addr, accessor):
    """Look up details for a token by its accessor."""
    resp = vault_request("POST", "auth/token/lookup-accessor", token, addr, {"accessor": accessor})
    if resp.status_code != 200:
        return None
    return resp.json().get("data", {})


def send_slack_alert(webhook_url, message):
    """Send a message to a Slack webhook."""
    try:
        requests.post(webhook_url, json={"text": message}, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"WARN: Failed to send Slack alert: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Audit OpenBao leases and tokens")
    parser.add_argument(
        "--warn-days", type=int, default=7,
        help="Warn if lease/token expires within N days (default: 7)"
    )
    parser.add_argument(
        "--slack-webhook",
        default=os.environ.get("SLACK_WEBHOOK_URL", ""),
        help="Slack webhook URL for alerts"
    )
    args = parser.parse_args()

    addr = os.environ.get("VAULT_ADDR")
    token = os.environ.get("VAULT_TOKEN")
    if not addr or not token:
        print("ERROR: VAULT_ADDR and VAULT_TOKEN must be set", file=sys.stderr)
        sys.exit(1)

    warn_threshold = timedelta(days=args.warn_days)
    now = datetime.now(timezone.utc)
    warnings = []

    # Check token accessors
    print(f"[{datetime.now(timezone.utc).isoformat()}] Auditing tokens at {addr}...")
    accessors = list_token_accessors(token, addr)

    if not accessors:
        print("  No token accessors found (empty or no permission).")
        sys.exit(0)

    orphaned = 0
    expiring_soon = 0

    for accessor in accessors:
        info = lookup_token_accessor(token, addr, accessor)
        if info is None:
            orphaned += 1
            continue

        expire_time = info.get("expire_time")
        display_name = info.get("display_name", "unknown")
        policies = info.get("policies", [])

        # Flag orphaned tokens (excluding root)
        if info.get("orphan", False) and "root" not in policies:
            orphaned += 1
            warnings.append(
                f"Orphaned token: {display_name} (accessor: {accessor[:12]}...)"
            )

        # Flag tokens expiring soon
        if expire_time:
            try:
                exp = datetime.fromisoformat(expire_time.replace("Z", "+00:00"))
                if exp - now < warn_threshold:
                    expiring_soon += 1
                    days_left = max(0, (exp - now).days)
                    warnings.append(
                        f"Token expiring in {days_left}d: {display_name} "
                        f"(accessor: {accessor[:12]}...)"
                    )
            except (ValueError, TypeError):
                pass

    print(
        f"  Tokens checked: {len(accessors)}, "
        f"orphaned: {orphaned}, "
        f"expiring within {args.warn_days}d: {expiring_soon}"
    )

    # Report
    if warnings:
        report = (
            f"OpenBao Audit -- {len(warnings)} warning(s):\n"
            + "\n".join(f"  - {w}" for w in warnings)
        )
        print(report)
        if args.slack_webhook:
            send_slack_alert(args.slack_webhook, report)
        sys.exit(1)
    else:
        print("  All tokens healthy.")
        sys.exit(0)


if __name__ == "__main__":
    main()
