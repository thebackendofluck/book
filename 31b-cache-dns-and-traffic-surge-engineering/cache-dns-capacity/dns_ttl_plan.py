#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 31b, Cache, DNS, and Traffic Surge Engineering.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Generate a DNS TTL runway for planned cutovers.

This script prints a plan only. It does not call any DNS provider.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone


def parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--current-ttl", type=int, required=True)
    parser.add_argument("--normal-ttl", type=int, default=900)
    parser.add_argument("--cutover-at", required=True, help="UTC ISO timestamp")
    parser.add_argument("--migration-ttl", type=int, default=300)
    parser.add_argument("--final-ttl", type=int, default=60)
    args = parser.parse_args()

    cutover = parse_utc(args.cutover_at)
    lower_48h = cutover - timedelta(hours=48)
    lower_4h = cutover - timedelta(hours=4)
    restore = cutover + timedelta(hours=24)

    print("# DNS TTL Runway")
    print("")
    print(f"- hostname: {args.hostname}")
    print(f"- current_ttl: {args.current_ttl}s")
    print(f"- cutover_at: {fmt(cutover)}")
    print("")
    print("| Time | Action | TTL | Reason |")
    print("|---|---|---:|---|")
    print(
        f"| {fmt(lower_48h)} | lower TTL | {args.migration_ttl}s | "
        "start resolver cache runway |"
    )
    print(
        f"| {fmt(lower_4h)} | lower TTL again if cutover is still likely | "
        f"{args.final_ttl}s | reduce rollback/failover wait |"
    )
    print(
        f"| {fmt(cutover)} | change target record | {args.final_ttl}s | "
        "cutover window |"
    )
    print(
        f"| {fmt(restore)} | restore normal TTL | {args.normal_ttl}s | "
        "reduce DNS query load after stability |"
    )
    print("")
    print("## Checks")
    print("")
    print("```bash")
    print(f"dig +nocmd {args.hostname} A +noall +answer")
    print(f"dig +trace {args.hostname}")
    print(f"dig @1.1.1.1 {args.hostname} A")
    print(f"dig @8.8.8.8 {args.hostname} A")
    print("```")
    print("")
    print("## Notes")
    print("")
    print("- Lower TTL before the cutover, not during it.")
    print("- Keep internal-only services on internal DNS overrides.")
    print("- Do not expose staging or admin tools through public DNS as a shortcut.")


if __name__ == "__main__":
    main()
