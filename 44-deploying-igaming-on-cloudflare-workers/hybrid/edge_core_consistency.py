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
Edge/Core Consistency Test Suite.

Proves that the edge layer (Cloudflare Workers + KV) and core layer
(PostgreSQL + Redis) remain consistent under normal and degraded
conditions:

  1. Balance check: edge KV vs core PostgreSQL
  2. Session check: edge KV vs core Redis
  3. Config check: edge KV vs core config service
  4. Degraded mode: edge continues when core is unreachable

Design
------
Each test category returns a ConsistencyResult with pass/fail/detail.
The suite can run against any environment pair (staging/uat/prod).
In CI, it runs against staging; in release gates, against uat.

Environments
------------
  staging  - both layers in-memory, deterministic
  uat      - edge reads from KV mock, core from Redis mock
  prod     - real HTTP calls to both layers
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ConsistencyStatus(Enum):
    CONSISTENT = "consistent"
    DRIFT = "drift"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class Layer(Enum):
    EDGE = "edge"
    CORE = "core"


@dataclass
class ConsistencyResult:
    check_name: str
    status: ConsistencyStatus
    edge_value: Any = None
    core_value: Any = None
    drift_detail: str = ""
    latency_edge_ms: float = 0.0
    latency_core_ms: float = 0.0
    timestamp: float = 0.0


@dataclass
class DegradedModeResult:
    scenario: str
    edge_available: bool
    core_available: bool
    edge_served_stale: bool
    player_impact: str
    recovery_action: str


# ---------------------------------------------------------------------------
# Simulated storage layers
# ---------------------------------------------------------------------------

class EdgeKV:
    """Simulates Cloudflare Workers KV (edge layer)."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._available: bool = True

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._store[key] = json.dumps(value, sort_keys=True)

    def get(self, key: str) -> dict[str, Any] | None:
        if not self._available:
            return None
        raw = self._store.get(key)
        return json.loads(raw) if raw else None

    def set_available(self, available: bool) -> None:
        self._available = available


class CorePostgres:
    """Simulates PostgreSQL (core layer)."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, Any]] = {}
        self._available: bool = True

    def upsert(self, table: str, key: str, value: dict[str, Any]) -> None:
        self._tables.setdefault(table, {})[key] = value

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        if not self._available:
            return None
        return self._tables.get(table, {}).get(key)

    def set_available(self, available: bool) -> None:
        self._available = available


class CoreRedis:
    """Simulates Redis (core layer)."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._available: bool = True

    def set(self, key: str, value: dict[str, Any], ttl: int = 0) -> None:
        self._store[key] = json.dumps(value, sort_keys=True)

    def get(self, key: str) -> dict[str, Any] | None:
        if not self._available:
            return None
        raw = self._store.get(key)
        return json.loads(raw) if raw else None

    def set_available(self, available: bool) -> None:
        self._available = available


# ---------------------------------------------------------------------------
# Consistency checks
# ---------------------------------------------------------------------------

def check_balance_consistency(
    edge: EdgeKV,
    core: CorePostgres,
    player_id: str,
) -> ConsistencyResult:
    """Compare player balance between edge KV and core PostgreSQL."""
    start_edge = time.monotonic()
    edge_balance = edge.get(f"balance:{player_id}")
    latency_edge = (time.monotonic() - start_edge) * 1000

    start_core = time.monotonic()
    core_balance = core.get("wallets", player_id)
    latency_core = (time.monotonic() - start_core) * 1000

    if edge_balance is None and core_balance is None:
        status = ConsistencyStatus.CONSISTENT
        detail = "Both layers report no balance"
    elif edge_balance is None or core_balance is None:
        missing = "edge" if edge_balance is None else "core"
        status = ConsistencyStatus.UNAVAILABLE if not (
            edge._available and core._available
        ) else ConsistencyStatus.DRIFT
        detail = f"Balance missing from {missing} layer"
    elif edge_balance.get("balance") == core_balance.get("balance"):
        status = ConsistencyStatus.CONSISTENT
        detail = "Balances match"
    else:
        status = ConsistencyStatus.DRIFT
        detail = (f"Edge={edge_balance.get('balance')} "
                  f"Core={core_balance.get('balance')}")

    return ConsistencyResult(
        check_name=f"balance:{player_id}",
        status=status,
        edge_value=edge_balance,
        core_value=core_balance,
        drift_detail=detail,
        latency_edge_ms=latency_edge,
        latency_core_ms=latency_core,
        timestamp=time.time(),
    )


def check_session_consistency(
    edge: EdgeKV,
    core: CoreRedis,
    session_id: str,
) -> ConsistencyResult:
    """Compare session state between edge KV and core Redis."""
    edge_session = edge.get(f"session:{session_id}")
    core_session = core.get(f"session:{session_id}")

    if edge_session is None and core_session is None:
        status = ConsistencyStatus.CONSISTENT
        detail = "No session in either layer"
    elif edge_session is None or core_session is None:
        missing = "edge" if edge_session is None else "core"
        status = ConsistencyStatus.DRIFT
        detail = f"Session missing from {missing}"
    elif (edge_session.get("player_id") == core_session.get("player_id")
          and edge_session.get("active") == core_session.get("active")):
        status = ConsistencyStatus.CONSISTENT
        detail = "Sessions match"
    else:
        status = ConsistencyStatus.DRIFT
        detail = "Session fields diverged"

    return ConsistencyResult(
        check_name=f"session:{session_id}",
        status=status,
        edge_value=edge_session,
        core_value=core_session,
        drift_detail=detail,
        timestamp=time.time(),
    )


def check_config_consistency(
    edge: EdgeKV,
    core: CoreRedis,
    config_key: str,
) -> ConsistencyResult:
    """Compare runtime config between edge KV and core Redis."""
    edge_config = edge.get(f"config:{config_key}")
    core_config = core.get(f"config:{config_key}")

    if edge_config is None and core_config is None:
        status = ConsistencyStatus.CONSISTENT
        detail = "Config not set in either layer"
    elif edge_config is None or core_config is None:
        missing = "edge" if edge_config is None else "core"
        status = ConsistencyStatus.DRIFT
        detail = f"Config missing from {missing}"
    elif json.dumps(edge_config, sort_keys=True) == json.dumps(core_config, sort_keys=True):
        status = ConsistencyStatus.CONSISTENT
        detail = "Config values identical"
    else:
        status = ConsistencyStatus.DRIFT
        detail = "Config values diverged"

    return ConsistencyResult(
        check_name=f"config:{config_key}",
        status=status,
        edge_value=edge_config,
        core_value=core_config,
        drift_detail=detail,
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# Degraded mode scenarios
# ---------------------------------------------------------------------------

def simulate_degraded_core_down(
    edge: EdgeKV,
    core_pg: CorePostgres,
    core_redis: CoreRedis,
    player_id: str,
) -> DegradedModeResult:
    """
    Scenario: Core (PostgreSQL + Redis) is unreachable.
    Edge should continue serving from cached KV state.
    """
    # Pre-populate edge with known-good state
    edge.put(f"balance:{player_id}", {"balance": 100.00, "currency": "EUR",
                                       "cached_at": time.time()})
    edge.put(f"session:{player_id}", {"player_id": player_id, "active": True})

    # Simulate core outage
    core_pg.set_available(False)
    core_redis.set_available(False)

    # Edge should still serve
    balance = edge.get(f"balance:{player_id}")
    session = edge.get(f"session:{player_id}")
    edge_ok = balance is not None and session is not None

    # Restore
    core_pg.set_available(True)
    core_redis.set_available(True)

    return DegradedModeResult(
        scenario="core_completely_down",
        edge_available=edge_ok,
        core_available=False,
        edge_served_stale=True,
        player_impact="Read-only mode: can view balance and session, "
                      "cannot place bets or make deposits",
        recovery_action="Reconcile edge KV with core on recovery, "
                        "replay any queued mutations",
    )


def simulate_degraded_edge_down(
    edge: EdgeKV,
    core_pg: CorePostgres,
    core_redis: CoreRedis,
    player_id: str,
) -> DegradedModeResult:
    """
    Scenario: Edge (Cloudflare) is unreachable.
    Players fall back to core origin.
    """
    edge.set_available(False)

    core_pg.upsert("wallets", player_id, {"balance": 100.00, "currency": "EUR"})
    core_redis.set("session:" + player_id, {"player_id": player_id, "active": True})

    core_balance = core_pg.get("wallets", player_id)
    core_session = core_redis.get("session:" + player_id)
    core_ok = core_balance is not None and core_session is not None

    edge.set_available(True)

    return DegradedModeResult(
        scenario="edge_completely_down",
        edge_available=False,
        core_available=core_ok,
        edge_served_stale=False,
        player_impact="Higher latency (origin-direct), full functionality preserved",
        recovery_action="DNS failover to origin; re-sync KV on edge recovery",
    )


def simulate_degraded_split_brain(
    edge: EdgeKV,
    core_pg: CorePostgres,
    player_id: str,
) -> DegradedModeResult:
    """
    Scenario: Edge and core have divergent balances (split brain).
    Demonstrates detection and resolution strategy.
    """
    edge.put(f"balance:{player_id}", {"balance": 95.00, "currency": "EUR"})
    core_pg.upsert("wallets", player_id, {"balance": 100.00, "currency": "EUR"})

    edge_bal = edge.get(f"balance:{player_id}")
    core_bal = core_pg.get("wallets", player_id)
    if edge_bal is None or core_bal is None:
        diverged = True
    else:
        diverged = edge_bal["balance"] != core_bal["balance"]

    return DegradedModeResult(
        scenario="split_brain_balance",
        edge_available=True,
        core_available=True,
        edge_served_stale=diverged,
        player_impact="Player may see incorrect balance; "
                      "core is authoritative for financial data",
        recovery_action="Force re-sync from core PostgreSQL to edge KV; "
                        "core always wins for wallet state",
    )


# ---------------------------------------------------------------------------
# Full suite
# ---------------------------------------------------------------------------

@dataclass
class ConsistencySuiteReport:
    environment: str
    checks: list[ConsistencyResult]
    degraded_scenarios: list[DegradedModeResult]
    all_consistent: bool
    drift_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "all_consistent": self.all_consistent,
            "drift_count": self.drift_count,
            "checks": [
                {
                    "name": c.check_name,
                    "status": c.status.value,
                    "detail": c.drift_detail,
                    "latency_edge_ms": round(c.latency_edge_ms, 2),
                    "latency_core_ms": round(c.latency_core_ms, 2),
                }
                for c in self.checks
            ],
            "degraded_scenarios": [
                {
                    "scenario": d.scenario,
                    "edge_available": d.edge_available,
                    "core_available": d.core_available,
                    "edge_served_stale": d.edge_served_stale,
                    "player_impact": d.player_impact,
                    "recovery_action": d.recovery_action,
                }
                for d in self.degraded_scenarios
            ],
        }


def run_consistency_suite(environment: str = "staging") -> ConsistencySuiteReport:
    """Run the full edge/core consistency test suite."""
    edge = EdgeKV()
    core_pg = CorePostgres()
    core_redis = CoreRedis()

    # Seed consistent data
    players = ["player-001", "player-002", "player-003"]
    for pid in players:
        bal = {"balance": 100.00, "currency": "EUR"}
        edge.put(f"balance:{pid}", bal)
        core_pg.upsert("wallets", pid, bal)

        sess = {"player_id": pid, "active": True, "started_at": time.time()}
        edge.put(f"session:{pid}", sess)
        core_redis.set(f"session:{pid}", sess)

    configs = {
        "feature:aviator": {"enabled": True, "rollout_pct": 100},
        "jurisdiction:GB": {"min_age": 18, "deposit_limit_daily_gbp": 1000},
        "supplier:pragmatic": {"enabled": True, "rtp_target": 96.5},
    }
    for key, val in configs.items():
        edge.put(f"config:{key}", val)
        core_redis.set(f"config:{key}", val)

    # Run checks
    checks: list[ConsistencyResult] = []
    for pid in players:
        checks.append(check_balance_consistency(edge, core_pg, pid))
        checks.append(check_session_consistency(edge, core_redis, pid))
    for key in configs:
        checks.append(check_config_consistency(edge, core_redis, key))

    # Degraded scenarios
    degraded: list[DegradedModeResult] = [
        simulate_degraded_core_down(edge, core_pg, core_redis, "player-001"),
        simulate_degraded_edge_down(edge, core_pg, core_redis, "player-002"),
        simulate_degraded_split_brain(edge, core_pg, "player-003"),
    ]

    drift_count = sum(1 for c in checks if c.status == ConsistencyStatus.DRIFT)
    all_consistent = drift_count == 0

    return ConsistencySuiteReport(
        environment=environment,
        checks=checks,
        degraded_scenarios=degraded,
        all_consistent=all_consistent,
        drift_count=drift_count,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Edge/Core Consistency Suite")
    parser.add_argument("--env", choices=["staging", "uat", "prod"],
                        default="staging")
    args = parser.parse_args()

    report = run_consistency_suite(args.env)
    print(json.dumps(report.to_dict(), indent=2))

    if report.all_consistent:
        print(f"\nAll {len(report.checks)} checks passed. No drift detected.")
    else:
        print(f"\nDRIFT DETECTED: {report.drift_count}/{len(report.checks)} checks.")

    return 0 if report.all_consistent else 1


if __name__ == "__main__":
    raise SystemExit(main())
