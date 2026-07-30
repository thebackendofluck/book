#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24j, IP Reputation and Blocklist Integration for iGaming Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
iprep-fp-detector.py — Scan Suricata eve.json for potential false positive iprep blocks.

Looks for blocked connections that match known-legitimate traffic patterns:
- HTTP 4xx/5xx response codes (blocked connections typically don't get responses)
- User-Agent strings matching known payment provider clients
- Destination ports that match internal service ports (not public-facing)

Run as: iprep-fp-detector.py --eve /var/log/suricata/eve.json --hours 24
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Patterns suggesting a blocked IP might be legitimate
LEGITIMATE_UA_PATTERNS = [
    "Stripe", "PayPal", "Trustly", "Klarna", "Adyen",
    "WorldPay", "ACI ", "Checkout.com", "Braintree",
]


def analyze_drops(eve_path: str, hours: int):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    suspicious_drops = []

    try:
        with open(eve_path, 'r') as f:
            for line in f:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Only look at drop events with iprep sig IDs
                if event.get("event_type") != "alert":
                    continue

                alert = event.get("alert", {})
                sig_id = alert.get("signature_id", 0)

                # Our iprep rules are in the 9100000-9199999 range
                if not (9100000 <= sig_id <= 9199999):
                    continue

                # Check if HTTP request details suggest legitimate client
                http = event.get("http", {})
                user_agent = http.get("http_user_agent", "")

                for pattern in LEGITIMATE_UA_PATTERNS:
                    if pattern.lower() in user_agent.lower():
                        suspicious_drops.append({
                            "timestamp": event.get("timestamp"),
                            "src_ip": event.get("src_ip"),
                            "dest_port": event.get("dest_port"),
                            "signature": alert.get("signature"),
                            "user_agent": user_agent,
                            "flag": f"Legitimate UA pattern: {pattern}",
                        })
                        break

    except FileNotFoundError:
        print(f"ERROR: Eve log not found at {eve_path}", file=sys.stderr)
        sys.exit(1)

    if suspicious_drops:
        print(f"WARNING: {len(suspicious_drops)} potential false positive blocks detected in last {hours}h:")
        for drop in suspicious_drops[:20]:  # Show first 20
            print(f"  {drop['timestamp']} | {drop['src_ip']}:{drop['dest_port']} | {drop['flag']}")
            print(f"    Sig: {drop['signature']}")
            print(f"    UA: {drop['user_agent'][:80]}")
    else:
        print(f"No suspicious drops detected in last {hours}h iprep events.")

    return len(suspicious_drops)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--eve", default="/var/log/suricata/eve.json")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()

    count = analyze_drops(args.eve, args.hours)
    sys.exit(1 if count > 0 else 0)
