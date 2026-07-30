#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Compare edge-visible API state against core API state.

The script is intentionally contract-focused:
- it compares stable health fields
- it compares stable dashboard summary aggregates
- it ignores transient fields such as timestamps and cache source
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def _get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _stable_health_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status"),
        "version": payload.get("version"),
        "database_status": payload.get("database", {}).get("status"),
        "redis_status": payload.get("redis", {}).get("status"),
        "kafka_status": payload.get("kafka", {}).get("status"),
        "services": payload.get("services", {}),
    }


def _stable_summary_view(payload: dict[str, Any]) -> dict[str, Any]:
    stats = payload.get("stats", {})
    return {
        "players": stats.get("players"),
        "wallet_events": stats.get("wallet_events"),
        "game_rounds": stats.get("game_rounds"),
        "active_game_sessions": stats.get("active_game_sessions"),
        "aml_alerts": stats.get("aml_alerts"),
        "kyc_checks": stats.get("kyc_checks"),
        "total_deposits": stats.get("total_deposits"),
        "total_withdrawals": stats.get("total_withdrawals"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--edge-base-url",
        default="https://new.acmetocasino.com",
        help="Edge-visible base URL",
    )
    parser.add_argument(
        "--core-base-url",
        default="https://new.acmetocasino.com",
        help="Core base URL",
    )
    args = parser.parse_args()

    try:
        edge_health = _get_json(f"{args.edge_base_url.rstrip('/')}/api/v2/health")
        core_health = _get_json(f"{args.core_base_url.rstrip('/')}/api/v2/health")
        edge_summary = _get_json(f"{args.edge_base_url.rstrip('/')}/api/v2/dashboard/summary")
        core_summary = _get_json(f"{args.core_base_url.rstrip('/')}/api/v2/dashboard/summary")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1

    edge_health_view = _stable_health_view(edge_health)
    core_health_view = _stable_health_view(core_health)
    edge_summary_view = _stable_summary_view(edge_summary)
    core_summary_view = _stable_summary_view(core_summary)

    output = {
        "ok": edge_health_view == core_health_view and edge_summary_view == core_summary_view,
        "edge_base_url": args.edge_base_url,
        "core_base_url": args.core_base_url,
        "health_consistent": edge_health_view == core_health_view,
        "summary_consistent": edge_summary_view == core_summary_view,
        "edge": {
            "health": edge_health_view,
            "summary": edge_summary_view,
        },
        "core": {
            "health": core_health_view,
            "summary": core_summary_view,
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
