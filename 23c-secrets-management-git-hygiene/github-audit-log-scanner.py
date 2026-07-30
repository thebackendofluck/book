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

"""github-audit-log-scanner.py — Poll GitHub audit log for suspicious events.

Purpose:
    Queries the GitHub organization audit log for security-relevant events
    including new deploy keys, changed branch protections, force pushes,
    new outside collaborators, repository visibility changes, and repo
    deletions. Sends a Slack alert if any suspicious events are found.

Prerequisites:
    - GH_TOKEN or GITHUB_TOKEN environment variable set (with admin:org scope)
    - requests library (pip install requests)

Usage:
    python3 github-audit-log-scanner.py <org> [--since 24h] [--slack-webhook URL]

Examples:
    GH_TOKEN=ghp_xxx python3 github-audit-log-scanner.py myorg
    python3 github-audit-log-scanner.py myorg --since 7d --slack-webhook https://hooks.slack.com/...
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    print("ERROR: requests library required. Install: pip install requests", file=sys.stderr)
    sys.exit(1)

# Events that indicate potential unauthorized activity or security changes
SUSPICIOUS_ACTIONS = [
    "repo.deploy_key.create",
    "repo.deploy_key.delete",
    "protected_branch.destroy",
    "protected_branch.update",
    "protected_branch.create",
    "git.push",  # force pushes appear with additional context
    "repo.add_member",
    "org.invite_member",
    "repo.access",  # visibility changes
    "repo.destroy",
    "repo.create",
    "team.add_repository",
    "org.update_member",
]


def parse_duration(s):
    """Parse a duration string like '24h', '7d', '30m' into timedelta."""
    units = {"m": "minutes", "h": "hours", "d": "days"}
    if not s or len(s) < 2:
        raise ValueError(f"Invalid duration: {s}")
    try:
        num = int(s[:-1])
    except ValueError:
        raise ValueError(f"Invalid number in duration: {s}")
    unit = s[-1].lower()
    if unit not in units:
        raise ValueError(f"Unknown unit '{unit}'. Use m (minutes), h (hours), or d (days).")
    return timedelta(**{units[unit]: num})


def fetch_audit_log(org, token, since_iso, per_page=100):
    """Fetch audit log entries from GitHub API with pagination."""
    url = f"https://api.github.com/orgs/{org}/audit-log"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {
        "per_page": per_page,
        "phrase": f"created:>{since_iso}",
        "include": "all",
    }
    entries = []
    page = 0
    max_pages = 50  # safety limit

    while url and page < max_pages:
        page += 1
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        except requests.exceptions.ConnectionError:
            print("ERROR: Cannot connect to GitHub API.", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.Timeout:
            print("ERROR: GitHub API request timed out.", file=sys.stderr)
            sys.exit(1)

        if resp.status_code == 403:
            print("ERROR: Forbidden. Ensure token has admin:org scope.", file=sys.stderr)
            sys.exit(1)
        if resp.status_code == 404:
            print(
                f"ERROR: Org '{org}' not found or audit log not available "
                f"(requires GitHub Enterprise or org admin access).",
                file=sys.stderr,
            )
            sys.exit(1)
        resp.raise_for_status()
        entries.extend(resp.json())
        url = resp.links.get("next", {}).get("url")
        params = {}  # params are encoded in the next URL

    return entries


def send_slack_alert(webhook_url, message):
    """Send a message to a Slack webhook."""
    try:
        requests.post(webhook_url, json={"text": message}, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"WARN: Failed to send Slack alert: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Scan GitHub audit log for suspicious events"
    )
    parser.add_argument("org", help="GitHub organization name")
    parser.add_argument(
        "--since", default="24h",
        help="Look back duration, e.g., 24h, 7d, 30m (default: 24h)"
    )
    parser.add_argument(
        "--slack-webhook",
        default=os.environ.get("SLACK_WEBHOOK_URL", ""),
        help="Slack webhook URL for alerts"
    )
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ERROR: GH_TOKEN or GITHUB_TOKEN must be set", file=sys.stderr)
        sys.exit(1)

    try:
        since = datetime.now(timezone.utc) - parse_duration(args.since)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(
        f"[{datetime.now(timezone.utc).isoformat()}] "
        f"Scanning audit log for {args.org} since {since_iso}"
    )

    entries = fetch_audit_log(args.org, token, since_iso)
    suspicious = [e for e in entries if e.get("action") in SUSPICIOUS_ACTIONS]

    if not suspicious:
        print(f"  No suspicious events found in {len(entries)} log entries.")
        sys.exit(0)

    print(
        f"  Found {len(suspicious)} suspicious event(s) "
        f"in {len(entries)} total entries:\n"
    )
    alerts = []
    for event in suspicious:
        action = event.get("action", "unknown")
        actor = event.get("actor", "unknown")
        repo = event.get("repo", "N/A")
        ts = event.get("created_at", event.get("@timestamp", "unknown"))
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

        line = f"  [{ts}] {action} by {actor} on {repo}"
        print(line)
        alerts.append(line)

    if args.slack_webhook:
        message = (
            f"GitHub Audit Alert ({args.org}): "
            f"{len(suspicious)} suspicious event(s):\n"
            + "\n".join(alerts)
        )
        send_slack_alert(args.slack_webhook, message)

    sys.exit(1)


if __name__ == "__main__":
    main()
