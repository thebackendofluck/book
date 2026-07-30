#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Query DefectDojo for findings approaching or past SLA."""

from __future__ import annotations

import datetime as dt
import os
import sys

import requests


DEFECTDOJO_URL = os.environ["DEFECTDOJO_URL"].rstrip("/")
DEFECTDOJO_TOKEN = os.environ["DEFECTDOJO_TOKEN"]
SLACK_WEBHOOK = os.environ.get("SLACK_SECURITY_WEBHOOK", "")

SLA_DAYS = {"Critical": 1, "High": 7, "Medium": 30, "Low": 90}
WARN_AT = {"Critical": 0.5, "High": 3, "Medium": 14, "Low": 45}


def notify(message: str) -> None:
    if SLACK_WEBHOOK:
        requests.post(SLACK_WEBHOOK, json={"text": message}, timeout=10)


def open_findings() -> list[dict]:
    response = requests.get(
        f"{DEFECTDOJO_URL}/api/v2/findings/",
        headers={"Authorization": f"Token {DEFECTDOJO_TOKEN}"},
        params={"active": "true", "verified": "true", "limit": 500},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("results", [])


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    breaches: list[str] = []
    for finding in open_findings():
        severity = finding.get("severity")
        if severity not in SLA_DAYS:
            continue
        created = dt.datetime.fromisoformat(finding["date"].replace("Z", "+00:00"))
        age_days = (now - created).total_seconds() / 86_400
        if age_days >= WARN_AT[severity]:
            breaches.append(f"{severity}: {finding.get('title')} age={age_days:.1f}d")

    if breaches:
        message = "DefectDojo SLA warning:\n" + "\n".join(breaches)
        notify(message)
        print(message, file=sys.stderr)
        return 1

    print("No DefectDojo SLA warnings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
